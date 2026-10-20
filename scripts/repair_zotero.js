/* SPDX-License-Identifier: MIT
 * Run inside Zotero 7's Tools > Developer > Run JavaScript.
 * Repairs existing references only. It never imports new references or writes
 * to the mounted source PDFs. Node tests load the functions without running them.
 */
(async function () {
  const PROJECT = typeof PAPERPILE_RUNTIME !== "undefined" ? PAPERPILE_RUNTIME.project : "";
  const FORMAT = '{{ year }}-{{ authors max="1" name="family" }}-{{ title case="pascal" }}';
  const AUTO_MOVE = "extensions.zotmoov.enable_automove";

  function normalizeDOI(value) {
    return String(value || "").trim().replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "").toLowerCase();
  }

  function normalizeTitle(value) {
    return String(value || "").replace(/<[^>]*>/g, "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  }

  function buildPlan(records, items) {
    const byDOI = new Map();
    const byTitle = new Map();
    const append = (map, key, value) => {
      if (key) map.set(key, [...(map.get(key) || []), value]);
    };
    for (const item of items) {
      append(byDOI, normalizeDOI(item.getField("DOI")), item);
      append(byTitle, normalizeTitle(item.getField("title")), item);
    }
    const used = new Set();
    return records.map(record => {
      const doi = normalizeDOI(record.doi);
      let matches = doi ? byDOI.get(doi) || [] : [];
      if (!matches.length) {
        matches = (byTitle.get(normalizeTitle(record.title)) || []).filter(item => {
          const itemDOI = normalizeDOI(item.getField("DOI"));
          return !doi || !itemDOI || doi === itemDOI;
        });
      }
      if (matches.length !== 1) {
        return {record, reason: matches.length ? "ambiguous_reference" : "reference_not_found"};
      }
      if (used.has(matches[0].id)) return {record, reason: "duplicate_export_reference"};
      used.add(matches[0].id);
      return {record, item: matches[0]};
    });
  }

  function safeComponent(value, Z) {
    const result = Z.File.getValidFileName(String(value || "").trim());
    return result && result !== "." && result !== ".." ? result : null;
  }

  async function runRepair(options = {}) {
    if (!options.directory && !PROJECT) throw new Error("Run the generated repair launcher from output/setup/ after building the project.");
    const Z = options.Z || Zotero;
    const io = options.io || IOUtils;
    const directory = options.directory || PROJECT + "/output/migration";
    const dryRun = Boolean(options.dryRun);
    const reportPath = directory + (dryRun ? "/zotero-repair-preview.json" : "/zotero-repair-report.json");
    const exportRoot = directory + "/annotated-pdfs";
    const manifest = options.manifest || JSON.parse(await Z.File.getContentsAsync(directory + "/migration-manifest.json"));
    if (Z.paperpileRepairRunning) throw new Error("A Paperpile repair is already running. Wait for it to finish.");
    const collections = Z.Collections.getByLibrary(Z.Libraries.userLibraryID, true)
      .filter(collection => collection.name === manifest.collection_name);
    if (collections.length !== 1) {
      throw new Error("Expected one collection named " + manifest.collection_name + ". No references were changed.");
    }
    const collection = collections[0];
    await collection.loadAllData();
    const library = Z.Libraries.get(collection.libraryID);
    if (!library.editable || !library.filesEditable) throw new Error("This library is not editable.");
    const items = collection.getChildItems().filter(item => item.isRegularItem());
    for (const item of items) await item.loadAllData();
    const plan = buildPlan(manifest.references, items);
    const matched = plan.filter(row => row.item).length;
    if (!matched) throw new Error("No exported references match this collection. No references were changed.");

    const report = {
      version: 1, started: new Date().toISOString(), status: dryRun ? "preview" : "running",
      collection: collection.name, collection_id: collection.id,
      summary: {references_in_export: manifest.references.length, matched_references: matched,
        copied_pdfs: 0, reused_pdfs: 0, annotations_in_zotero: 0, annotated_exports: 0,
        unresolved_pdfs: 0, references_without_pdfs: 0, review: 0, errors: 0},
      results: [],
    };
    await io.makeDirectory(directory, {createAncestors: true, ignoreExisting: true});
    const saveReport = () => Z.File.putContentsAsync(reportPath, JSON.stringify(report, null, 2) + "\n");
    if (dryRun) {
      report.results = plan.map(row => ({paperpile_id: row.record.paperpile_id,
        zotero_item_id: row.item?.id || null, reason: row.reason || "matched",
        available_sources: row.record.attachments.filter(a => a.mounted).length}));
      await saveReport();
      return report;
    }

    // Refuse a disconnected mount before touching the library.
    const sources = plan.flatMap(row => row.item ? row.record.attachments.filter(a => a.mounted) : []);
    let reachable = false;
    for (const source of sources) {
      if (await io.exists(source.mounted.local_path)) { reachable = true; break; }
    }
    if (sources.length && !reachable) throw new Error("The Google Drive mount is disconnected. Reconnect it in Files, then retry.");

    let progressWindow, progressLine;
    try {
      if (Z.ProgressWindow) {
        progressWindow = new Z.ProgressWindow();
        progressWindow.changeHeadline("Repairing Paperpile PDFs");
        progressLine = new progressWindow.ItemProgress("attachment", "Starting");
        progressWindow.show();
      }
    } catch (_) { /* Progress display is optional. */ }

    const exportOwners = new Map();
    if (await io.exists(reportPath)) {
      const previous = JSON.parse(await Z.File.getContentsAsync(reportPath));
      for (const row of previous.results || []) {
        if (row.export_path && row.zotero_item_id) exportOwners.set(row.export_path, row.zotero_item_id);
      }
    }
    const autoMove = Z.Prefs.get(AUTO_MOVE, true);
    await saveReport();
    if (Z.paperpileRepairRunning) throw new Error("A Paperpile repair is already running. Wait for it to finish.");
    Z.Prefs.set(AUTO_MOVE, false, true);
    Z.paperpileRepairRunning = true;
    try {
      for (let index = 0; index < plan.length; index++) {
        const {record, item, reason} = plan[index];
        const result = {paperpile_id: record.paperpile_id, title: record.title,
          zotero_item_id: item?.id || null, status: "pending"};
        report.results.push(result);
        if (progressLine) {
          progressLine.setText(`${index + 1}/${plan.length}: ${record.title || "Untitled reference"}`);
          progressLine.setProgress(100 * index / plan.length);
        }
        try {
          if (reason) {
            result.status = reason;
            report.summary.review++;
            continue;
          }
          const attachments = (await Z.Items.getAsync(item.getAttachments())).filter(a => a.isPDFAttachment());
          const working = [];
          for (const attachment of attachments) {
            const path = await attachment.getFilePathAsync();
            if (path && await io.exists(path)) working.push(attachment);
          }
          if (working.length > 1) {
            result.status = "multiple_existing_pdfs_need_review";
            report.summary.review++;
            continue;
          }
          let attachment = working[0];
          let source;
          if (!attachment) {
            const candidates = record.attachments.filter(a => a.mounted);
            if (candidates.length !== 1) {
              result.status = record.attachments.length ? "source_pdf_not_resolved" : "no_pdf_in_export";
              if (record.attachments.length) report.summary.unresolved_pdfs++;
              else report.summary.references_without_pdfs++;
              continue;
            }
            source = candidates[0].mounted.local_path;
            if (!await io.exists(source)) throw new Error("The mounted source PDF is not reachable.");
            attachment = await Z.Attachments.importFromFile({file: source, parentItemID: item.id,
              title: "PDF", contentType: "application/pdf", moveFile: false});
            result.created_attachment = true;
            report.summary.copied_pdfs++;
          } else {
            report.summary.reused_pdfs++;
          }
          result.attachment_id = attachment.id;
          result.attachment_key = attachment.key;

          // Transfer annotations only from an actual Zotero-owned storage copy.
          // A linked source must never be changed by the PDF annotation worker.
          const storedPath = await attachment.getFilePathAsync();
          const storageDirectory = Z.Attachments.getStorageDirectory(attachment).path.replace(/\/$/, "");
          if (!attachment.isStoredFileAttachment() || !storedPath || !storedPath.startsWith(storageDirectory + "/") || storedPath === source) {
            result.status = "linked_or_nonstandard_pdf_needs_review";
            report.summary.review++;
            continue;
          }
          const hasNamingMetadata = Boolean(item.getField("title") && record.authors.length && record.published.year);
          if (hasNamingMetadata) {
            const name = Z.Attachments.getFileBaseNameFromItem(item, {formatString: FORMAT}) + ".pdf";
            if (await attachment.renameAttachmentFile(name, false, true) !== true) {
              throw new Error("The stored PDF could not be renamed; it remains attached for retry.");
            }
          }
          await Z.PDFWorker.import(attachment.id, true, undefined, true);
          attachment.attachmentSyncedHash = null;
          attachment.attachmentSyncState = "to_upload";
          await attachment.saveTx();
          await attachment.loadAllData();
          const annotations = attachment.getAnnotations();
          result.annotations = annotations.length;
          result.native_annotations = annotations.filter(a => !a.annotationIsExternal).length;
          report.summary.annotations_in_zotero += annotations.length;
          result.stored_path = await attachment.getFilePathAsync();
          result.status = "attached_and_annotations_imported";

          // Prepare annotated copies locally. Publishing them to Drive is a
          // separate step, so original Paperpile files cannot be overwritten.
          const surname = safeComponent(Object.hasOwn(record, "author_folder")
            ? record.author_folder : record.authors.at(-1)?.last, Z);
          if (options.exportCopies !== false && surname && hasNamingMetadata) {
            const exportDirectory = exportRoot + "/" + surname;
            await io.makeDirectory(exportDirectory, {createAncestors: true, ignoreExisting: true});
            const filename = result.stored_path.split("/").at(-1);
            let exportPath = exportDirectory + "/" + filename;
            let collisionIndex = 0;
            while ((exportOwners.has(exportPath) && exportOwners.get(exportPath) !== item.id)
                || (await io.exists(exportPath) && exportOwners.get(exportPath) !== item.id)) {
              collisionIndex++;
              const suffix = "-" + item.key + (collisionIndex > 1 ? "-" + collisionIndex : "");
              exportPath = exportDirectory + "/" + filename.replace(/\.pdf$/i, suffix + ".pdf");
            }
            const tmpPath = exportPath.replace(/\.pdf$/i, ".partial-" + item.key + ".pdf");
            await Z.PDFWorker.export(attachment.id, tmpPath, true);
            await io.move(tmpPath, exportPath, {noOverwrite: false});
            result.export_path = exportPath;
            exportOwners.set(exportPath, item.id);
            report.summary.annotated_exports++;
          } else if (!surname || !hasNamingMetadata) {
            result.export_needs_metadata_review = true;
            report.summary.review++;
          }
        } catch (error) {
          result.status = "error";
          result.error = String(error.message || error);
          report.summary.errors++;
        } finally {
          await saveReport();
        }
      }
      report.status = report.summary.errors ? "finished_with_errors" : "finished";
      report.finished = new Date().toISOString();
      await saveReport();
    } finally {
      Z.paperpileRepairRunning = false;
      if (autoMove === undefined) Z.Prefs.clear(AUTO_MOVE, true);
      else Z.Prefs.set(AUTO_MOVE, autoMove, true);
      if (progressWindow) {
        progressWindow.changeHeadline("Paperpile repair finished");
        progressLine?.setProgress(100);
        progressLine?.setText(`${report.summary.copied_pdfs} PDFs added; ${report.summary.errors} errors`);
      }
    }
    return report;
  }

  const api = {runRepair, buildPlan, normalizeDOI, normalizeTitle};
  if (typeof Zotero === "undefined" && typeof module !== "undefined" && module.exports) {
    module.exports = api;
    return api;
  }
  const report = await runRepair(typeof PAPERPILE_REPAIR_OPTIONS !== "undefined" ? PAPERPILE_REPAIR_OPTIONS : {});
  return JSON.stringify(report.summary, null, 2);
})();
