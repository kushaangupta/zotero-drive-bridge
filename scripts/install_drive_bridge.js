/* SPDX-License-Identifier: MIT. Run inside Zotero as an async function. */
(async function () {
// Replaced by the builder in output/setup/install_drive_bridge.js.
const project = "__PAPERPILE_PROJECT_ROOT__";
const packagePath = "__PAPERPILE_XPI_PATH__";
if (project.startsWith("__")) throw new Error("Build first, then run output/setup/install_drive_bridge.js.");
if (!await IOUtils.exists(project + "/.state/runtime-config.json")) throw new Error("The generated runtime configuration is missing. Rebuild first.");
Zotero.Prefs.set("extensions.paperpileDriveBridge.projectPath", project, true);
// Zotero 7's customized manager is the JSM. Firefox's similarly named ESM
// does not recognize applications.zotero in Zotero plugin manifests.
const {AddonManager} = ChromeUtils.import("resource://gre/modules/AddonManager.jsm");
const file = Zotero.File.pathToFile(packagePath);
Services.obs.notifyObservers(file, "flush-cache-entry");
const install = await AddonManager.getInstallForFile(file);
function failure(label) {
  let details = "";
  try {
    details = Services.console.getMessageArray().map(m => m.message)
      .filter(m => /(invalid|manifest|paperpile|drive-bridge|xpi|addon)/i.test(m)).slice(-8).join("\n");
  } catch (_) {}
  return new Error(label + ": " + install.error + (details ? "\n" + details : ""));
}
await new Promise((resolve, reject) => {
  install.addListener({
    onInstallEnded: resolve,
    onInstallFailed: () => reject(failure("Plugin install failed")),
    onDownloadFailed: () => reject(failure("Plugin download failed")),
  });
  install.install();
});
const addon = await AddonManager.getAddonByID("paperpile-drive-bridge@local");
if (!addon) throw new Error("The installed add-on could not be found");
addon.applyBackgroundUpdates = AddonManager.AUTOUPDATE_DISABLE;
if (addon.appDisabled) throw new Error("Installed but Zotero disabled the add-on: compatible=" + addon.isCompatible + ", secureUpdates=" + addon.providesUpdatesSecurely);
if (addon.userDisabled) await addon.enable();
await Zotero.Promise.delay(250);
if (!addon.isActive) throw new Error("Installed but the add-on is not active yet");
if (!Zotero.PaperpileDriveBridge || Zotero.PaperpileDriveBridge.project !== project) {
  throw new Error("The add-on is active, but its bridge did not start. Check Zotero's Error Console and rebuild the local configuration.");
}
if (typeof Zotero.PaperpileDriveBridge.libraryItems !== "function") {
  throw new Error("The library-wide update has not activated. Restart Zotero, then run this installer again.");
}
return "Installed. The bridge now watches all of My Library. The first Drive sync is running. Later syncs run at the configured interval while Zotero is open. Tools → Sync Drive PDF annotations now runs it immediately.";
})();
