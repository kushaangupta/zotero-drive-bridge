/* SPDX-License-Identifier: MIT
 * Run in Zotero 7's Tools > Developer > Run JavaScript, as an async function.
 * Imports the validated comment plan into native PDF annotations. It preserves
 * existing annotations and stops on concurrent edits. No references are added,
 * no source PDFs are changed, and no Google Drive comments are changed.
 */
(async function () {
  const PROJECT = typeof PAPERPILE_RUNTIME !== "undefined" ? PAPERPILE_RUNTIME.project : "";
  const STATE = PROJECT + "/.state/drive-zotero-state.json";
  const REPORT = PROJECT + "/output/migration/drive-annotation-import-report.json";

  function decide(existing, mapping, entry) {
    if (!existing) return mapping ? "missing_annotation_needs_review" : "create";
    if (!mapping) {
      // The preparer matched the exact PDF comment and page bounds. Check the
      // live database again in case the annotation was edited since that export.
      if (existing.comment !== entry.drive_body) return "concurrent_edit_preserved";
      return existing.comment === entry.annotation.comment ? "adopt" : "update";
    }
    if (entry.annotation.comment === mapping.drive_comment) return "unchanged";
    if (existing.comment !== mapping.zotero_comment) return "concurrent_edit_preserved";
    return "update";
  }

  async function run(options = {}) {
    if (!PROJECT && !options.plan) throw new Error("Run the generated import launcher or install the configured add-on.");
    const Z = options.Z || Zotero;
    const io = options.io || IOUtils;
    const plan = options.plan || JSON.parse(await Z.File.getContentsAsync(PROJECT + "/.state/drive-annotation-plan.json"));
    const state = options.state || (await io.exists(STATE)
      ? JSON.parse(await Z.File.getContentsAsync(STATE)) : {version: 1, mappings: {}});
    const ackPath = PROJECT + "/.state/drive-outgoing-ack.json";
    if (!options.state && await io.exists(ackPath)) {
      const acknowledgments = JSON.parse(await Z.File.getContentsAsync(ackPath));
      for (const [id, ack] of Object.entries(acknowledgments)) {
        const mapping = state.mappings[id];
        if (!mapping || mapping.last_ack === ack.request_id) continue;
        if (mapping.zotero_comment !== ack.expected_zotero_comment) continue;
        mapping.zotero_comment = ack.zotero_comment;
        mapping.drive_body = ack.body;
        mapping.drive_comment = ack.body + (mapping.drive_suffix || "");
        mapping.last_ack = ack.request_id;
      }
    }
    const saveState = options.saveState || (() => Z.File.putContentsAsync(STATE, JSON.stringify(state, null, 2) + "\n"));
    if (Z.paperpileDriveImportRunning) throw new Error("A Drive annotation import is already running");
    Z.paperpileDriveImportRunning = true;
    const report = {started: new Date().toISOString(), status: "running",
      summary: {created: 0, adopted: 0, updated: 0, unchanged: 0, review: 0, errors: 0},
      results: [], export_paths: [], preparation_errors: plan.errors || []};
    const changed = new Map();
    try {
      for (const file of plan.files) {
        const attachment = await Z.Items.getAsync(file.attachment_id);
        if (!attachment || attachment.key !== file.attachment_key || attachment.parentID !== file.zotero_item_id
          || !attachment.isStoredFileAttachment() || !attachment.isPDFAttachment()
          || attachment.libraryID !== Z.Libraries.userLibraryID) {
          throw new Error("Attachment identity or storage does not match the prepared plan");
        }
        await attachment.loadAllData();
        for (const entry of file.entries) {
          const id = file.file_id + ":" + entry.drive_comment_id + ":" + entry.page_index;
          const result = {file_id: file.file_id, attachment_key: attachment.key, drive_comment_id: entry.drive_comment_id, page: entry.page_index + 1};
          report.results.push(result);
          try {
            const mapping = state.mappings[id];
            const key = mapping?.zotero_key || entry.matched_zotero_key;
            let annotation = key ? Z.Items.getByLibraryAndKey(attachment.libraryID, key) : null;
            if (annotation) {
              await annotation.loadAllData();
              if (!annotation.isAnnotation() || annotation.parentID !== attachment.id || annotation.annotationIsExternal) {
                throw new Error("Mapped annotation belongs to another attachment or is read-only");
              }
            }
            // A pending key was reserved before a previous create. Retry with
            // that same key if saving had not yet committed.
            const effectiveMapping = mapping?.pending ? null : mapping;
            const action = key && !annotation && !mapping?.pending ? "missing_annotation_needs_review"
              : mapping?.pending && annotation && annotation.annotationComment === entry.annotation.comment ? "adopt"
              : decide(annotation && {comment: annotation.annotationComment || ""}, effectiveMapping, entry);
            result.status = action;
            if (action === "concurrent_edit_preserved" || action === "missing_annotation_needs_review") {
              result.drive_comment = entry.annotation.comment;
              report.summary.review++;
              continue;
            }
            if (action === "create") {
              const newKey = mapping?.zotero_key || Z.DataObjectUtilities.generateKey();
              state.mappings[id] = {zotero_key: newKey, pending: true};
              await saveState();
              annotation = await Z.Annotations.saveFromJSON(attachment, {...entry.annotation, key: newKey});
              report.summary.created++;
              changed.set(attachment.key, attachment);
            } else if (action === "update") {
              // Change only the comment. Keep tags, color, position and any
              // other annotation fields chosen in Zotero.
              annotation.annotationComment = entry.annotation.comment;
              await annotation.saveTx();
              report.summary.updated++;
              changed.set(attachment.key, attachment);
            } else if (action === "adopt") report.summary.adopted++;
            else report.summary.unchanged++;
            result.zotero_key = annotation.key;
            if (action === "unchanged") {
              // A local edit has not been sent to Drive. Retain the last shared
              // baseline so a later Drive edit is still detected as a conflict.
              state.mappings[id] = {...mapping, checked: new Date().toISOString()};
            } else {
              state.mappings[id] = {zotero_key: annotation.key, attachment_key: attachment.key,
                drive_comment: entry.annotation.comment, zotero_comment: annotation.annotationComment || "",
                drive_modified: entry.drive_modified, checked: new Date().toISOString()};
            }
            Object.assign(state.mappings[id], {
              file_id: file.file_id, drive_comment_id: entry.drive_comment_id, page_index: entry.page_index,
              origin: mapping?.origin || (entry.matched_zotero_key ? "zotero" : "drive"),
              drive_body: state.mappings[id].drive_body ?? entry.drive_body,
              drive_suffix: state.mappings[id].drive_suffix ?? entry.annotation.comment.slice(entry.drive_body.length),
            });
            if (action !== "unchanged") {
              state.mappings[id].drive_body = entry.drive_body;
              state.mappings[id].drive_suffix = entry.annotation.comment.slice(entry.drive_body.length);
            }
            await saveState();
          } catch (error) {
            result.status = "error";
            result.error = String(error.message || error);
            report.summary.errors++;
          }
        }
      }
      if (options.exportPDFs !== false) {
        const directory = PROJECT + "/output/sync/annotated-pdfs";
        await io.makeDirectory(directory, {createAncestors: true, ignoreExisting: true});
        for (const attachment of changed.values()) {
          const path = directory + "/" + attachment.key + ".pdf";
          const temporary = directory + "/" + attachment.key + ".partial.pdf";
          await Z.PDFWorker.export(attachment.id, temporary, true);
          await io.move(temporary, path, {noOverwrite: false});
          report.export_paths.push(path);
        }
      }
      report.status = report.summary.errors || report.summary.review || report.preparation_errors.length ? "needs_review" : "finished";
      report.finished = new Date().toISOString();
      await Z.File.putContentsAsync(REPORT, JSON.stringify(report, null, 2) + "\n");
      return report;
    } finally {
      Z.paperpileDriveImportRunning = false;
    }
  }

  if (typeof module !== "undefined" && module.exports && typeof Zotero === "undefined") {
    module.exports = {decide, run};
    return;
  }
  if (typeof PAPERPILE_LOAD_ONLY !== "undefined" && PAPERPILE_LOAD_ONLY) {
    PAPERPILE_LOAD_ONLY.importer = {run, decide};
    return;
  }
  const report = await run();
  return JSON.stringify({status: report.status, ...report.summary, annotated_PDFs_exported: report.export_paths.length}, null, 2);
})();
