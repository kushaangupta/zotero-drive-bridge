#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Publish staged annotated PDFs through GNOME's native Google Drive backend.

This is a resumable initial publication, not an automatic sync engine. It never
overwrites or deletes an existing remote file. A rerun verifies identical files
and reports changed ones for review, preserving annotations made on either side.
Use /usr/bin/python3 on the desktop session (PyGObject/GIO required).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import threading

from gi.repository import Gio
from bridge_config import PROJECT, load_config, state_path

ATTRS = "standard::name,standard::display-name,standard::type,standard::size"


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".partial")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def children(folder):
    rows = folder.enumerate_children(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
    try:
        return [{"name": row.get_display_name(),
                 "file": folder.get_child(row.get_name()),
                 "directory": row.get_file_type() == Gio.FileType.DIRECTORY,
                 "size": row.get_size() if row.has_attribute("standard::size") else None}
                for row in rows]
    finally:
        rows.close(None)


def unique(rows, name):
    matches = [row for row in rows if row["name"] == name]
    if len(matches) > 1:
        raise ValueError("Duplicate remote display names; review required: " + name)
    return matches[0] if matches else None


def remote_hash(file):
    digest = hashlib.sha256()
    stream = file.read(None)
    try:
        while chunk := stream.read_bytes(1024 * 1024, None).get_data():
            digest.update(chunk)
    finally:
        stream.close(None)
    return digest.hexdigest()


def local_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_exports(report, root):
    rows, paths, keys = [], set(), set()
    root = root.resolve()
    for record in report["results"]:
        if not record.get("export_path"):
            continue
        path = Path(record["export_path"]).resolve(strict=True)
        relative = path.relative_to(root)
        if len(relative.parts) != 2 or path.suffix.lower() != ".pdf":
            raise ValueError("Unexpected export layout: " + str(relative))
        key = record["attachment_key"]
        if str(relative) in paths or key in keys:
            raise ValueError("Duplicate attachment or export path in repair report")
        paths.add(str(relative))
        keys.add(key)
        rows.append({"attachment_key": key, "attachment_id": record["attachment_id"],
                     "zotero_item_id": record["zotero_item_id"],
                     "source": str(path), "relative_path": str(relative),
                     "size": path.stat().st_size})
    return rows


def publish_author(root, author, records, root_rows, folder_lock, completed):
    # Serializing folder creation prevents two workers creating duplicate names.
    with folder_lock:
        row = unique(root_rows, author)
        if row is None:
            root.get_child_for_display_name(author).make_directory(None)
            root_rows[:] = children(root)
            row = unique(root_rows, author)
        if row is None or not row["directory"]:
            raise ValueError("Expected a single author folder: " + author)
        folder = row["file"]
    listing = children(folder)
    for record in records:
        result = {**record, "checked": now()}
        try:
            path = Path(record["source"])
            expected = local_hash(path)
            result["local_sha256"] = expected
            name = path.name
            existing = unique(listing, name)
            created = existing is None
            if created:
                # NONE intentionally refuses to overwrite any existing file.
                Gio.File.new_for_path(str(path)).copy(
                    folder.get_child_for_display_name(name),
                    Gio.FileCopyFlags.NONE, None, None, None)
                listing = children(folder)
                existing = unique(listing, name)
            if existing is None or existing["directory"]:
                raise ValueError("Uploaded file could not be resolved")
            result["remote_uri"] = existing["file"].get_uri()
            result["remote_size"] = existing["size"]
            actual = remote_hash(existing["file"])
            result["remote_sha256"] = actual
            if actual != expected:
                result["status"] = "conflict_preserved"
            else:
                result["status"] = "copied_verified" if created else "existing_verified"
        except Exception as error:
            result["status"] = "error"
            result["error"] = str(error)
        completed(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", help="Overrides destination_drive_uri in local configuration")
    parser.add_argument("--limit", type=int, help="Copy at most this many staged PDFs")
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 5))
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    config = load_config()
    args.destination = args.destination or config.get("destination_drive_uri")
    if not args.destination:
        parser.error("Set destination_drive_uri in config.local.json or pass --destination")
    registry_path = state_path("registry", config)
    report_path = PROJECT / "output/migration/drive-publication-report.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with (registry_path.parent / "drive-publish.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repair = json.loads((PROJECT / "output/migration/zotero-repair-report.json").read_text())
        records = validate_exports(repair, PROJECT / "output/migration/annotated-pdfs")
        if args.limit is not None:
            records = records[:args.limit]
        root = Gio.File.new_for_uri(args.destination)
        root_info = root.query_info("standard::display-name,access::can-write",
                                    Gio.FileQueryInfoFlags.NONE, None)
        if not root_info.get_attribute_boolean("access::can-write"):
            raise ValueError("Destination is not writable")
        previous = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        if previous and previous.get("destination") != args.destination:
            raise ValueError("Publication registry belongs to a different destination")
        state = {"version": 1, "destination": args.destination,
                 "files": previous.get("files", {})}
        report = {"started": now(), "destination": args.destination,
                  "status": "running", "expected": len(records), "results": []}
        root_rows = children(root)
        save_lock, folder_lock = threading.Lock(), threading.Lock()

        def completed(result):
            with save_lock:
                report["results"].append(result)
                key = result["attachment_key"]
                # Keep the published baseline when Drive or the local copy changed.
                if result["status"] in ("copied_verified", "existing_verified"):
                    state["files"][key] = result
                save_json(registry_path, state)
                save_json(report_path, report)
                count = len(report["results"])
                if count % 5 == 0 or count == len(records) or result["status"] in ("error", "conflict_preserved"):
                    print(json.dumps({"completed": count, "expected": len(records),
                                      "last_status": result["status"],
                                      "errors": sum(x["status"] == "error" for x in report["results"]),
                                      "conflicts": sum(x["status"] == "conflict_preserved" for x in report["results"])}), flush=True)

        groups = {}
        for row in records:
            groups.setdefault(Path(row["relative_path"]).parts[0], []).append(row)
        print(json.dumps({"destination": root_info.get_display_name(),
                          "pdfs": len(records), "author_folders": len(groups),
                          "bytes": sum(x["size"] for x in records)}), flush=True)
        save_json(report_path, report)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = {pool.submit(publish_author, root, author, rows, root_rows,
                                folder_lock, completed): rows for author, rows in groups.items()}
            for task in as_completed(jobs):
                try:
                    task.result()
                except Exception as error:
                    for row in jobs[task]:
                        completed({**row, "status": "error", "error": str(error)})
        report["finished"] = now()
        report["status"] = "finished" if all(x["status"] in ("copied_verified", "existing_verified")
                                             for x in report["results"]) else "needs_review"
        report["summary"] = {status: sum(x["status"] == status for x in report["results"])
                             for status in ("copied_verified", "existing_verified", "conflict_preserved", "error")}
        save_json(report_path, report)
        print(json.dumps({"status": report["status"], **report["summary"]}), flush=True)


if __name__ == "__main__":
    main()
