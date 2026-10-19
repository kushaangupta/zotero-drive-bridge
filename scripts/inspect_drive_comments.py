#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read comments only on PDFs already indexed in this migration.

Saves private JSON under ignored .state/, printing only counts and anchor field
names. No comment text, credentials, or other Drive files are printed or changed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from gnome_drive_api import DriveReader
from bridge_config import PROJECT, load_config, state_path, drive_identity


def inspect(record, account):
    comments = DriveReader(account).comments(record["file_id"])
    return {**record, "comments": comments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("destination", "source"), default="destination")
    parser.add_argument("--first", action="store_true", help="Read only the first indexed PDF")
    args = parser.parse_args()
    config = load_config()
    if args.source == "destination":
        registry = json.loads(state_path("registry", config).read_text())
        records = [{"attachment_key": key, "file_id": value["remote_uri"].split("/")[-1],
                    "relative_path": value["relative_path"]}
                   for key, value in registry["files"].items()]
        account = drive_identity(registry["destination"])
    else:
        index = json.loads((PROJECT / ".state/drive-index.json").read_text())
        records = [{"file_id": value["uri"].split("/")[-1], "relative_path": value["relative_path"]}
                   for value in index["resolved"]]
        uri = config.get("source_drive_uri") or index.get("source_uri")
        if not uri:
            raise ValueError("Set source_drive_uri in config.local.json")
        account = drive_identity(uri)
    if args.first:
        records = records[:1]
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(inspect, row, account): row for row in records}
        for task in as_completed(futures):
            try:
                results.append(task.result())
            except Exception as error:
                errors.append({"file_id": futures[task]["file_id"], "error": str(error)})
    output = state_path(args.source + "_comments", config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"files": results, "errors": errors}, ensure_ascii=False, indent=2) + "\n")
    comments = [c for row in results for c in row["comments"]]
    anchor_fields = []
    for comment in comments:
        anchor = comment.get("anchor")
        if not anchor:
            continue
        try:
            value = json.loads(anchor)
            anchor_fields.append(list(value) if isinstance(value, dict) else type(value).__name__)
        except ValueError:
            anchor_fields.append("non-JSON anchor")
    print(json.dumps({"files_read": len(results), "comments": len(comments),
                      "anchors": anchor_fields, "errors": len(errors)}))


if __name__ == "__main__":
    main()
