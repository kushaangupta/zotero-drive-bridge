# Author folders that stay together

Drive folders use the **last author in publication order**, formatted as
`FirstInitial. Surname`. The paper filename remains
`Year-FirstAuthorSurname-TitleInPascalCase.pdf`.

```text
E. Moser/
└── 2024-FirstAuthor-AnExampleTitle.pdf
M. Moser/
└── 2025-FirstAuthor-AnotherExampleTitle.pdf
```

The first initial comes from the first given name. Middle initials are not part
of the folder name, so adding or omitting a middle name does not create a new
folder by itself.

## How names match

- `Ila Fiete`, `Ila R. Fiete`, and `I. Fiete` share a folder when the available
  metadata identifies one person.
- Case, punctuation, and diacritic differences are normalized for matching.
  The saved folder spelling stays stable.
- Different full first names are kept separate. A folder-name collision can
  produce a label such as `J. Smith (Jane)` alongside `J. Smith`.
- An initial-only name matching several people needs review. Conflicting middle
  names also need review; omitted middle names are allowed.
- Organizations retain their full name. Papers without usable author metadata
  stay in their existing folders; new unassigned papers are not published yet.

This is a conservative metadata match, not a universal author identification
service. Nicknames, transliteration changes, and two people with the same name
can require a manual decision. The bridge does not guess based on similar spelling.

## Existing libraries

The updated add-on takes an author snapshot across My Library each cycle,
regardless of collection membership. It saves identities in
the ignored `.state/author-folders.json` and reorganizes registered Drive copies
when needed. Later papers reuse those identities and folders.

For a folder containing only one identified author, the organizer renames the
folder. For mixed folders, it moves the existing PDFs into the appropriate
author folders. It keeps PDF file IDs and contents, so file links and comment
threads stay with each paper. Unregistered files are left alone. Empty source
folders are moved to Drive's trash after a successful split.

The operations use Drive's [file and folder APIs](https://developers.google.com/workspace/drive/api/guides/folder).
The organizer checks parents and destination-name conflicts, uses conditional
writes, and checkpoints the local registry. A retry recovers using file IDs.
Private backups are saved under `.state/author-folder-backups/` before changes.

To preview the latest snapshot without changing Drive:

```sh
.venv/bin/python scripts/organize_author_folders.py
```

Inspect `.state/author-folder-plan.json` for assignments and review cases.
The running add-on applies the plan automatically. A manual apply, from the
logged-in GNOME session, is also available:

```sh
/usr/bin/python3 scripts/organize_author_folders.py --apply
```

Both manual and automatic operations share the bridge's process lock. If a
cycle is already running, wait until it finishes before running the command.

## Correcting an author

Use consistent full given and family names in Zotero for confirmed identities.
An already assigned PDF whose last author changes is flagged instead of silently
switching identities. For an intentional correction, disable the add-on, back up
`.state/author-folders.json`, and remove only the affected attachment key from
its `attachments` object. Keep the `authors` and `aliases` objects. Re-enable
the add-on and check the next plan before accepting its assignment.

Keep this state privately with the checkout. Deleting it loses the naming history.
