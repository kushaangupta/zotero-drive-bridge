#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare validated Drive-comment imports; does not change PDFs or Zotero.

Run with the Python environment containing pypdf, after fetching comments using the
desktop's /usr/bin/python3. Private plans stay under the ignored .state/ folder.
"""
import json
from pathlib import Path

from pypdf import PdfReader
from drive_annotation_codec import embedded_annotations, convert_comment
from bridge_config import PROJECT, state_path


def main():
    registry = json.loads(state_path("registry").read_text())
    snapshot = json.loads(state_path("destination_comments").read_text())
    by_id = {value["remote_uri"].split("/")[-1]: value for value in registry["files"].values()}
    outbox_path = PROJECT / ".state/drive-outbox.json"
    outbox = json.loads(outbox_path.read_text()).get("files", {}) if outbox_path.exists() else {}
    mapping_path = PROJECT / ".state/drive-zotero-state.json"
    state = json.loads(mapping_path.read_text()) if mapping_path.exists() else {"mappings": {}}
    migrated_state = False
    excluded_path = PROJECT / ".state/drive-untrusted-files.json"
    excluded = json.loads(excluded_path.read_text()) if excluded_path.exists() else {}
    plan = {"version": 1, "files": [], "errors": list(snapshot.get("errors", []))}
    for remote in snapshot["files"]:
        if remote["file_id"] in excluded:
            plan["errors"].append({"file_id": remote["file_id"], "error": excluded[remote["file_id"]]})
            continue
        record = by_id.get(remote["file_id"])
        if not record or record["attachment_key"] != remote["attachment_key"]:
            raise ValueError("Comment snapshot does not match the publication registry")
        path = Path(outbox.get(record["attachment_key"], {}).get("full_path", record["source"])).resolve(strict=True)
        if not any(path.is_relative_to(root) for root in (
            PROJECT / "output/migration/annotated-pdfs", PROJECT / "output/sync/annotated-pdfs")):
            raise ValueError("PDF is outside the allowed export directories")
        pdf = PdfReader(path)
        native = embedded_annotations(pdf)
        legacy = [m for m in state["mappings"].values() if m.get("attachment_key") == record["attachment_key"] and not m.get("origin")]
        if legacy:
            baseline_keys = {a["key"] for a in embedded_annotations(PdfReader(record["source"]))}
            for mapping in legacy:
                mapping["origin"] = "zotero" if mapping["zotero_key"] in baseline_keys else "drive"
            migrated_state = True
        target = {"file_id": remote["file_id"], "attachment_key": record["attachment_key"],
                  "attachment_id": record["attachment_id"], "zotero_item_id": record["zotero_item_id"],
                  "export_path": str(path), "entries": []}
        for comment in remote["comments"]:
            try:
                target["entries"].extend(convert_comment(comment, pdf, native))
            except Exception as error:
                plan["errors"].append({"file_id": remote["file_id"],
                                       "comment_id": comment["id"], "error": str(error)})
        plan["files"].append(target)
    output = PROJECT / ".state/drive-annotation-plan.json"
    temp = output.with_suffix(".partial")
    temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    temp.replace(output)
    if migrated_state:
        temporary_state = mapping_path.with_suffix(".partial")
        temporary_state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        temporary_state.replace(mapping_path)
    entries = [e for f in plan["files"] for e in f["entries"]]
    print(json.dumps({"pdfs": len(plan["files"]), "comment_pages": len(entries),
                      "matched_existing": sum(bool(e["matched_zotero_key"]) for e in entries),
                      "new_candidates": sum(not e["matched_zotero_key"] for e in entries),
                      "errors": len(plan["errors"])}))


if __name__ == "__main__":
    main()
