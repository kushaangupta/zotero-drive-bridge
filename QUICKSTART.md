# Quick setup (TL;DR)

**Linux/GNOME · Zotero 7.0.x.** Already set up? Skip to [Everyday use](#everyday-use).
For explanations and troubleshooting, see the [full README](README.md).

## 1. Install dependencies

From the repository root, on Debian/Ubuntu:

```sh
sudo apt install python3-venv python3-gi gvfs-backends gnome-online-accounts
/usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
/usr/bin/python3 scripts/setup.py init
```

## 2. Connect Drive and configure

- Add your Google account in **GNOME Settings → Online Accounts**; enable files.
- Open Drive in Files. Create a destination folder and copy its location with
  **Ctrl+L** (`google-drive://…`).
- In `config.local.json`, set `destination_drive_uri` to that location and
  `collection_name` to the exact Zotero collection name. Keep the other defaults.
- Keep PDFs stored inside Zotero; disable ZotMoov automatic moving.

## 3. Import once — skip if already migrated

The first setup needs the collection/file mappings created by this import.
Back up Zotero first. Keep original PDFs in a separate source folder.

1. Put the matching RIS and JSON exports in `input/library.ris` and
   `input/library.json`. Use PDFs containing your existing annotations.
2. Add `source_drive_uri` to `config.local.json`: the GNOME location of the
   original folder **containing `allPapers/`**. Add `source_mount_path`: its
   account's `mount_base` printed by the first command below.

```sh
/usr/bin/python3 scripts/list_drive_mounts.py
/usr/bin/python3 scripts/setup.py doctor --migration
/usr/bin/python3 scripts/setup.py prepare
/usr/bin/python3 scripts/setup.py build
/usr/bin/python3 scripts/setup.py commands
```

3. In Zotero, **File → Import** the prepared `output/migration/<collection_name>.ris`.
   **Already imported? Keep that collection and skip the second import.**
4. Open **Tools → Developer → Run JavaScript**; check **Run as async function**.
   Run the printed `preview_repair` command, inspect its report, then run
   `repair_zotero`. Check `output/migration/zotero-repair-report.json` for missing
   PDFs or review cases.
5. Publish the initial organized copies:

```sh
/usr/bin/python3 scripts/publish_gnome_drive.py
```

Export-format details and recovery steps: [migration guide](docs/migration.md).

## 4. Install the bridge

Run the printed `install_drive_bridge` command in Zotero's async JavaScript
window. If updating an existing setup, run `setup.py build` and `setup.py commands`
as above first. Keep your existing configuration, `.state/`, and `output/`.

Check **Tools → Drive PDF sync status** after the first cycle.

## 5. Sync other computers and Android

Sign into the **same Zotero account** everywhere. In Zotero's file-sync settings,
choose **WebDAV** and enter:

- HTTPS server: `app.koofr.net/dav/Koofr`
- Username: your Koofr email
- Password: a Koofr **application-specific password**

Verify the server, then let desktop Zotero finish its initial upload. Configure
the same WebDAV server on Android under **Account → File Syncing**.
Use `https://app.koofr.net/dav/Koofr` if a field expects the full URL.
[Detailed device setup](README.md#5-add-computers-and-android-with-webdav).

## Everyday use

- Add new papers and stored PDFs directly to the configured Zotero collection.
- Annotate in Zotero on desktop or Android; sync Zotero on both devices.
- Keep desktop Zotero open: the bridge refreshes Drive every five minutes.
  Use **Tools → Sync Drive PDF annotations now** for an immediate refresh.
- Drive copies use `LastAuthor/Year-FirstAuthor-TitleInPascalCase.pdf`.
  Supported Drive preview comments also return to Zotero's PDF reader.
- Test with a comment **on a PDF highlight**, rather than a separate library note.

Keep the checkout at its installed location. No repeat RIS import is needed.
