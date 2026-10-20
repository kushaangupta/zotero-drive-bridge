# Moving an existing library into Zotero

This is a one-time migration guide for the included Paperpile export adapter.
After completing it, add new references and make annotations in Zotero. The
bridge then maintains the Drive copies; it does not monitor the old library.

Start with the checkout, Python environment, and GNOME Drive connection from
[the README](../README.md#1-prepare-the-checkout). Return to the README's
[installation step](../README.md#4-install-the-automatic-bridge) after publication.
If the migration and initial publication are already complete, skip this guide.

The source adapter expects a mounted folder containing `allPapers/`, plus RIS
and JSON exports from the same export session. It copies the original PDFs
into Zotero's storage and preserves the source folder.

It imports annotations **embedded in the PDFs** through Zotero's PDF worker.
Annotations held only in a cloud service first need to be included in the
annotated PDFs you download or export. Metadata JSON alone cannot restore them.

Back up the Zotero data directory first. Leave that database in Zotero's normal
local location. If ZotMoov is installed, disable its automatic moving before
importing: working PDFs must remain stored attachments for mobile file sync.

## 1. Configure the source

Keep the `destination_drive_uri` setting from the README. Set `collection_name`
to the exact import collection name; use the existing name if the RIS is already
imported. This setting scopes only the one-time repair. The installed bridge
watches all of My Library, independently of collection membership.

Add these migration settings to your ignored `config.local.json`:

| Setting | What to enter |
| --- | --- |
| `source_drive_uri` | GNOME URI of the original folder containing `allPapers/` |
| `source_mount_path` | Its account's `mount_base` printed by `scripts/list_drive_mounts.py` |
| `paperpile_ris` | `input/library.ris`, or the path to your RIS export |
| `paperpile_json` | `input/library.json`, or the path to the matching JSON export |

In Files, open the source folder and press **Ctrl+L** to copy its
`google-drive://…` URI. Use a separate destination folder. The two folders can
belong to the same Google account or to different accounts.

GNOME's local mount uses opaque Drive IDs in place of display names. The
indexer reads GIO directory metadata to resolve attachment paths correctly.

Check the source, destination, exports, and Python environment:

```sh
/usr/bin/python3 scripts/setup.py doctor --migration
```

## 2. Prepare and import once

```sh
/usr/bin/python3 scripts/setup.py prepare
/usr/bin/python3 scripts/setup.py build
```

Preparation creates these private outputs:

- `.state/drive-index.json`: resolved mounted attachment paths and unmatched files.
- `output/migration/<collection_name>.ris`: an RIS with usable attachment paths.
- `output/migration/migration-manifest.json`: matching data for the repair.
- `output/migration/migration-report.json`: counts and unresolved paths.

The tested Paperpile JSON format is an array of references with `_id`, `title`,
`author`, `published`, and `attachments` fields. Attachment filenames follow
`allPapers/Author/file.pdf`. Different export formats need an adapter.

Before importing, turn off ZotMoov's automatic moving if it is installed. The
working attachments must remain **stored files in Zotero** for normal mobile/file
sync. Leave automatic moving disabled for this workflow.

**For a fresh Zotero library:** import the prepared RIS using **File → Import**.
Wait for attachment import to finish. Ensure the resulting collection has the
exact `collection_name` configured above. If characters in that name had to be
sanitized for the RIS filename, rename the collection to the configured name.

**If the RIS is already imported:** keep that collection and set
`collection_name` to match it. Skip the second import. The repair below attaches
missing PDFs to existing references; it does not create another reference library.

## 3. Preview, then repair PDFs and annotations

The build prints three commands with the correct absolute paths for this
checkout. Retrieve them at any time with:

```sh
/usr/bin/python3 scripts/setup.py commands
```

In Zotero, open **Tools → Developer → Run JavaScript** and check
**Run as async function**.

First paste the printed **`preview_repair`** command. It matches references by
DOI or normalized title and writes a preview report. Inspect
`output/migration/zotero-repair-preview.json`, especially ambiguous matches.
The preview does not change Zotero items or PDFs.

Then run the printed **`repair_zotero`** command. It:

1. Copies a uniquely matched mounted PDF into Zotero's own storage when needed.
2. Renames the stored copy using `Year-FirstAuthor-TitleInPascalCase.pdf`.
3. Imports embedded PDF annotations into Zotero's database.
4. Exports annotated copies under the last author's first initial and surname
   when the author can be identified; ambiguous names need review.
5. Writes a resumable report with unresolved sources and review cases.

The original mounted PDFs are preserved. A working linked attachment, several
candidate PDFs, or ambiguous reference metadata is left for review rather than
being silently replaced. Zotero references without an available PDF remain in
the library.

Check `output/migration/zotero-repair-report.json` before publishing. Review
counts can overlap other categories; they are not all additional missing PDFs.

## 4. Publish the organized copies

Start with one paper if you want to inspect the naming and folder layout:

```sh
/usr/bin/python3 scripts/publish_gnome_drive.py --limit 1
```

Then publish the remaining staged exports:

```sh
/usr/bin/python3 scripts/publish_gnome_drive.py
```

This initial publisher reads each uploaded file back and verifies its SHA-256
hash. It can resume an interrupted initial publication. Existing files with
different bytes are preserved and reported as conflicts.

The publication registry under `.state/` connects Zotero attachment keys with
Drive file IDs. Keep it with this checkout. After the automatic bridge is in
use, use its sync command for routine updates instead of rerunning migration
or initial publication.

The current bootstrap uses the repair report and publication registry created
above to initialize the Drive file registry. The running bridge then watches
all of My Library; it does not depend on that collection or repair report.
Installing the XPI alone does not initialize the publication registry.

Return to [the README](../README.md#4-install-the-automatic-bridge) to install
the automatic bridge and configure other devices. Keep the original exports
and PDFs as a private archive; routine sync does not need another RIS import.
