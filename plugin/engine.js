/* SPDX-License-Identifier: MIT */
var DriveAnnotationEngine = class {
  constructor(importer, runtime = typeof PAPERPILE_RUNTIME !== "undefined" ? PAPERPILE_RUNTIME : {}) {
    if (!runtime.project) throw new Error("The bridge project path is missing. Run the generated installer.");
    this.importer = importer;
    this.project = runtime.project;
    this.registry = this.project + "/" + (runtime.state_files?.registry || ".state/drive-publication.json");
    this.python = runtime.desktop_python || "/usr/bin/python3";
    this.pollMilliseconds = (runtime.poll_minutes || 5) * 60000;
    this.timer = null;
    this.busy = false;
    this.stopped = false;
  }

  async read(path, fallback) {
    return await IOUtils.exists(path) ? JSON.parse(await Zotero.File.getContentsAsync(path)) : fallback;
  }

  async write(path, data) {
    await Zotero.File.putContentsAsync(path, JSON.stringify(data, null, 2) + "\n");
  }

  start() {
    // Stored attachments are required for Zotero's normal file/annotation sync.
    // ZotMoov's move mode would turn future attachments into linked files.
    Zotero.Prefs.set("extensions.zotmoov.enable_automove", false, true);
    this.timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    this.timer.initWithCallback({notify: () => { if (!this.busy) this.current = this.cycle(); }}, this.pollMilliseconds, Ci.nsITimer.TYPE_REPEATING_SLACK);
    const window = Zotero.getMainWindow();
    if (window) this.addMenu(window);
    this.current = this.cycle();
  }

  async stop() {
    this.stopped = true;
    this.timer?.cancel();
    const window = Zotero.getMainWindow();
    if (window) this.removeMenu(window);
    if (this.current) await this.current;
  }

  addMenu(window) {
    if (window.document.getElementById("paperpile-drive-sync")) return;
    const popup = window.document.getElementById("menu_ToolsPopup");
    if (!popup) return;
    const item = window.document.createXULElement("menuitem");
    item.id = "paperpile-drive-sync";
    item.setAttribute("label", "Sync Drive PDF annotations now");
    item.addEventListener("command", async () => {
      if (!this.busy) { this.current = this.cycle(); await this.current; }
      await this.showStatus(window);
    });
    popup.appendChild(item);
    const status = window.document.createXULElement("menuitem");
    status.id = "paperpile-drive-status";
    status.setAttribute("label", "Drive PDF sync status");
    status.addEventListener("command", () => this.showStatus(window));
    popup.appendChild(status);
  }

  removeMenu(window) {
    window.document.getElementById("paperpile-drive-sync")?.remove();
    window.document.getElementById("paperpile-drive-status")?.remove();
  }

  async showStatus(window) {
    const status = await this.read(this.project + "/.state/drive-bridge-status.json", {status: "No sync yet"});
    const pushed = status.pushed || {};
    Services.prompt.alert(window, "Drive PDF annotation sync", [
      "Status: " + status.status, "Phase: " + (status.phase || ""),
      "Last finished: " + (status.finished || "First sync is still running"),
      "Drive comments added in Zotero: " + (status.imported?.created || 0),
      "Zotero comment edits sent to Drive: " + (pushed.comments_updated || 0),
      "PDF copies updated: " + (pushed.pdfs_updated || 0),
      "Conflicts: " + (pushed.conflicts?.length || 0),
      "Errors: " + ((pushed.errors?.length || 0) + (status.error ? 1 : 0)),
      status.error || "", "Full report: " + this.project + "/.state/drive-bridge-status.json",
    ].filter(Boolean).join("\n"));
  }

  async desktop(phase) {
    const {Subprocess} = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
    const process = await Subprocess.call({command: this.python,
      arguments: [this.project + "/scripts/bridge_cycle.py", phase], stderr: "stdout"});
    let output = "";
    for (;;) {
      const chunk = await process.stdout.readString();
      if (!chunk) break;
      output = (output + chunk).slice(-5000);
    }
    const result = await process.wait();
    if (result.exitCode !== 0) throw new Error("Drive helper failed: " + output.slice(-2500));
  }

  async cycle() {
    if (this.busy || this.stopped) return;
    this.busy = true;
    const path = this.project + "/.state/drive-bridge-status.json";
    const status = {started: new Date().toISOString(), status: "running", phase: "reading Drive comments"};
    try {
      await this.write(path, status);
      await this.desktop("pull");
      if (this.stopped) return;
      status.phase = "importing PDF annotations";
      await this.write(path, status);
      const imported = await this.importer.run();
      status.imported = imported.summary;
      status.preparation_errors = imported.preparation_errors;
      status.zotero_data_sync_account_present = Boolean(Zotero.Users?.getCurrentUserID?.());
      status.zotero_auto_sync = Zotero.Prefs?.get("sync.autoSync");
      status.zotero_file_sync = Zotero.Prefs?.get("sync.storage.enabled");
      status.zotero_file_protocol = Zotero.Prefs?.get("sync.storage.protocol");
      const conflicts = new Set(imported.results.filter(r => r.status.includes("preserved") || r.status.includes("review"))
        .map(r => r.file_id + ":" + r.drive_comment_id + ":" + (r.page - 1)));
      const mappings = (await this.read(this.project + "/.state/drive-zotero-state.json", {mappings: {}})).mappings;
      const packets = [];
      for (const [id, mapping] of Object.entries(mappings)) {
        if (conflicts.has(id) || !mapping.file_id || mapping.pending) continue;
        const annotation = Zotero.Items.getByLibraryAndKey(Zotero.Libraries.userLibraryID, mapping.zotero_key);
        if (!annotation || !annotation.isAnnotation()) continue;
        await annotation.loadAllData();
        const current = annotation.annotationComment || "";
        if (current === mapping.zotero_comment) continue;
        const suffix = mapping.drive_suffix || "";
        if (suffix && !current.endsWith(suffix)) {
          status.reply_edits_need_review = (status.reply_edits_need_review || 0) + 1;
          continue;
        }
        packets.push({id, request_id: Date.now() + ":" + id, file_id: mapping.file_id,
          comment_id: mapping.drive_comment_id, body: suffix ? current.slice(0, -suffix.length) : current,
          expected_body: mapping.drive_body, zotero_comment: current,
          expected_zotero_comment: mapping.zotero_comment});
      }
      status.phase = "exporting annotated PDFs";
      await this.write(path, status);
      const registry = await this.read(this.registry, null);
      if (!registry) throw new Error("The verified Drive publication registry is missing");
      const repair = await this.read(this.project + "/output/migration/zotero-repair-report.json", null);
      const collection = Zotero.Collections.get(repair.collection_id);
      if (!collection || collection.libraryID !== Zotero.Libraries.userLibraryID) throw new Error("Migration collection is missing");
      await collection.loadAllData();
      const oldBox = await this.read(this.project + "/.state/drive-outbox.json", {files: {}});
      const box = {files: {}, comments: packets};
      const fullDirectory = this.project + "/output/sync/annotated-pdfs";
      await IOUtils.makeDirectory(fullDirectory, {createAncestors: true, ignoreExisting: true});
      const owners = new Map(Object.entries(registry.files).map(([key, r]) => [r.relative_path, key]));
      const authorID = Zotero.CreatorTypes.getID("author");
      for (const item of collection.getChildItems().filter(i => i.isRegularItem())) {
        await item.loadAllData();
        const authors = item.getCreators().filter(c => c.creatorTypeID === authorID);
        const surname = Zotero.File.getValidFileName(authors.at(-1)?.lastName || "");
        const year = Zotero.Date.strToDate(item.getField("date")).year;
        if (!surname || !year || !item.getField("title")) continue;
        const base = Zotero.Attachments.getFileBaseNameFromItem(item,
          {formatString: '{{ year }}-{{ authors max="1" name="family" }}-{{ title case="pascal" }}'}) + ".pdf";
        const attachments = await Zotero.Items.getAsync(item.getAttachments());
        for (const attachment of attachments.filter(a => a.isPDFAttachment() && a.isStoredFileAttachment())) {
          await attachment.loadAllData();
          const source = await attachment.getFilePathAsync();
          if (!source || !await IOUtils.exists(source)) continue;
          if (!source.startsWith(Zotero.Attachments.getStorageDirectory(attachment).path + "/")) continue;
          const registered = registry.files[attachment.key];
          if (!registered && !oldBox.files[attachment.key]) {
            await Zotero.PDFWorker.import(attachment.id, true, undefined, true);
            attachment.attachmentSyncedHash = null;
            attachment.attachmentSyncState = "to_upload";
            await attachment.saveTx();
            await attachment.loadAllData();
          }
          let relative = registered?.relative_path || surname + "/" + base;
          if (owners.has(relative) && owners.get(relative) !== attachment.key) {
            relative = surname + "/" + base.replace(/\.pdf$/, "-" + attachment.key + ".pdf");
          }
          owners.set(relative, attachment.key);
          const annotations = attachment.getAnnotations();
          for (const annotation of annotations) await annotation.loadAllData();
          const stat = await IOUtils.stat(source);
          const signature = JSON.stringify([stat.size, stat.lastModified, relative,
            annotations.map(a => [a.key, a.annotationType, a.annotationText, a.annotationComment,
              a.annotationColor, a.annotationPosition, a.annotationAuthorName, a.annotationPageLabel, a.getTags()])]);
          const full = fullDirectory + "/" + attachment.key + ".pdf";
          if (oldBox.files[attachment.key]?.signature !== signature || !await IOUtils.exists(full)) {
            const temporary = fullDirectory + "/" + attachment.key + ".partial.pdf";
            await Zotero.PDFWorker.export(attachment.id, temporary, true);
            await IOUtils.move(temporary, full, {noOverwrite: false});
          }
          const excluded = [], overrides = {};
          for (const mapping of Object.values(mappings).filter(m => m.attachment_key === attachment.key)) {
            if (mapping.origin === "drive") excluded.push(mapping.zotero_key);
            else if (mapping.drive_suffix) {
              const a = annotations.find(a => a.key === mapping.zotero_key);
              const comment = a?.annotationComment || "";
              if (comment.endsWith(mapping.drive_suffix)) overrides[mapping.zotero_key] = comment.slice(0, -mapping.drive_suffix.length);
            }
          }
          box.files[attachment.key] = {attachment_id: attachment.id, zotero_item_id: item.id,
            full_path: full, relative_path: relative, signature,
            exclude_keys: excluded, comment_overrides: overrides};
        }
      }
      await this.write(this.project + "/.state/drive-outbox.json", box);
      if (this.stopped) return;
      status.phase = "updating Drive copies";
      status.pdfs = Object.keys(box.files).length;
      await this.write(path, status);
      await this.desktop("push");
      const pushed = await this.read(this.project + "/.state/drive-push-report.json", {});
      status.pushed = pushed;
      status.status = imported.summary.review || imported.summary.errors || imported.preparation_errors.length
        || pushed.conflicts?.length || pushed.errors?.length || status.reply_edits_need_review ? "needs_review" : "finished";
      status.finished = new Date().toISOString();
      await this.write(path, status);
    } catch (error) {
      status.status = "error";
      status.error = String(error.message || error);
      await this.write(path, status);
      Zotero.logError(error);
    } finally {
      this.busy = false;
    }
    return status;
  }
};
