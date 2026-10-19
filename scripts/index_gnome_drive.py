#!/usr/bin/env python3
"""Resolve Paperpile attachment titles to the IDs used by GNOME's Drive mount.

Reads directory metadata only. Does not download, modify, or rename PDFs.
Run with the host's /usr/bin/python3, which provides PyGObject/GIO.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from gi.repository import Gio


def list_directory(uri):
    directory = Gio.File.new_for_uri(uri)
    rows = directory.enumerate_children(
        "standard::name,standard::display-name,standard::type,standard::size",
        Gio.FileQueryInfoFlags.NONE, None,
    )
    try:
        return [{
            "name": row.get_display_name(),
            "id": row.get_name(),
            "size": row.get_size() if row.has_attribute("standard::size") else None,
            "directory": row.get_file_type() == Gio.FileType.DIRECTORY,
            "uri": directory.get_child(row.get_name()).get_uri(),
        } for row in rows]
    finally:
        rows.close(None)


def unique_named(rows, name):
    matching = [row for row in rows if row["name"] == name]
    if len(matching) != 1:
        raise ValueError(f"Expected one entry named {name!r}; found {len(matching)}")
    return matching[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    records = json.loads(args.json.read_text())
    wanted = [attachment for record in records for attachment in record.get("attachments", [])]
    all_papers = unique_named(list_directory(args.uri), "allPapers")
    author_directories = list_directory(all_papers["uri"])
    children_by_author = {}
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {}
        for folder in author_directories:
            if folder["directory"]:
                tasks[pool.submit(list_directory, folder["uri"])] = folder["name"]
        for index, task in enumerate(as_completed(tasks), 1):
            author = tasks[task]
            try:
                children_by_author[author] = task.result()
            except Exception as error:
                errors.append({"folder": author, "error": str(error)})
            if index % 25 == 0 or index == len(tasks):
                print(f"Indexed {index}/{len(tasks)} author folders", flush=True)

    resolved = []
    for attachment in wanted:
        relative = PurePosixPath(attachment["filename"])
        try:
            if len(relative.parts) != 3 or relative.parts[0] != "allPapers":
                raise ValueError("Unexpected attachment layout")
            exact = [row for row in children_by_author.get(relative.parts[1], []) if row["name"] == relative.name]
            match_method = "exact_path"
            if len(exact) == 1:
                row = exact[0]
            else:
                candidates = [row for children in children_by_author.values() for row in children if row["name"] == relative.name]
                row = unique_named(candidates, relative.name)
                match_method = "unique_filename"
            local = args.mount / unquote(urlsplit(row["uri"]).path).lstrip("/")
            resolved.append({
                "relative_path": str(relative),
                "local_path": str(local),
                "uri": row["uri"],
                "size": row["size"] if row["size"] is not None else local.stat().st_size,
                "expected_size": attachment.get("filesize"),
                "exists": local.is_file(),
                "match_method": match_method,
            })
        except Exception as error:
            errors.append({"file": str(relative), "error": str(error)})
    report = {
        "source_uri": args.uri,
        "mount_path": str(args.mount),
        "references": len(records),
        "expected_attachments": len(wanted),
        "resolved": resolved,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "references": len(records), "expected_attachments": len(wanted),
        "resolved": len(resolved), "errors": len(errors),
        "size_bytes": sum(row["size"] for row in resolved),
        "missing_mounted_files": sum(not row["exists"] for row in resolved),
        "size_mismatches": sum(row["size"] != row["expected_size"] for row in resolved),
    }), flush=True)


if __name__ == "__main__":
    main()
