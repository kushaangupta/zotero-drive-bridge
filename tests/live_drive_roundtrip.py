#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Opt-in Drive integration test with a synthetic PDF, never a library paper.

Creates, modifies, and permanently removes its own temporary test PDF and test
comment in the configured destination. Requires --run-live-test. Not run by CI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge_config import PROJECT, configured_path, load_config, drive_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live-test", action="store_true",
                        help="Explicitly allow a temporary upload, edits, and removal of the synthetic test file")
    args = parser.parse_args()
    if not args.run_live_test:
        parser.error("This changes the live destination. Pass --run-live-test to opt in.")
    from gi.repository import Gio
    from bridge_cycle import DriveWriter
    from publish_gnome_drive import children, unique

    config = load_config(required=True)
    destination = config["destination_drive_uri"]
    drive = DriveWriter(drive_identity(destination))
    root_id = destination.rstrip("/").rsplit("/", 1)[1]
    permissions = drive.get(root_id, fields="permissions(type,role)").get("permissions", [])
    if not permissions or any(p.get("type") != "user" or p.get("role") != "owner" for p in permissions):
        raise ValueError("Use a private, owner-only test destination")

    name = ".AnnotationBridgeVerification-" + uuid.uuid4().hex + ".pdf"
    temporary = PROJECT / ".state" / name
    updated = temporary.with_suffix(".updated.pdf")
    temporary.parent.mkdir(exist_ok=True)
    result = {"stale_version_rejected": False, "pdf_comment_updated": False,
              "drive_comment_preserved": False, "temporary_file_removed": False}
    file_id = None
    try:
        code = '''import sys
from pypdf import PdfWriter
from pypdf.annotations import Highlight
from pypdf.generic import ArrayObject,FloatObject,NameObject,TextStringObject
for path,body in zip(sys.argv[1:],["Initial synthetic annotation","Updated synthetic annotation"]):
 w=PdfWriter()
 w.add_blank_page(width=200,height=300)
 a=Highlight(rect=[10,10,150,30],quad_points=ArrayObject([FloatObject(v) for v in [10,30,150,30,10,10,150,10]]))
 a[NameObject("/Contents")]=TextStringObject(body)
 a[NameObject("/Zotero:Key")]=TextStringObject("TEST0001")
 w.add_annotation(0,a)
 w.write(path)
'''
        subprocess.run([str(configured_path("pdf_python", config)), "-c", code,
                        str(temporary), str(updated)], check=True)
        root = Gio.File.new_for_uri(destination)
        Gio.File.new_for_path(str(temporary)).copy(root.get_child_for_display_name(name),
                                                  Gio.FileCopyFlags.NONE, None, None, None)
        row = unique(children(root), name)
        if row is None:
            raise ValueError("Temporary upload could not be resolved; check the test folder")
        file_id = row["file"].get_uri().rsplit("/", 1)[1]
        anchor = [None, None, [[[0, [[0.05, 0.05, 0.75, 0.10]]]]]]
        comment = drive.request("POST", "drive/v3/files/" + quote(file_id, safe="") + "/comments?fields=id",
            json.dumps({"content": "Synthetic bridge verification", "anchor": json.dumps(anchor)}).encode(),
            {"Content-Type": "application/json"})
        previous = drive.version(file_id)
        drive.request("PATCH", "drive/v2/files/" + quote(file_id, safe="") + "?fields=id,etag",
                      json.dumps({"title": name.replace(".pdf", "-renamed.pdf")}).encode(),
                      {"Content-Type": "application/json", "If-Match": previous["etag"]})
        try:
            drive.update_pdf(file_id, updated, previous["etag"])
        except HTTPError as error:
            result["stale_version_rejected"] = error.code == 412
        if not result["stale_version_rejected"]:
            raise ValueError("Drive did not reject the deliberately stale version")
        current = drive.version(file_id)
        drive.update_pdf(file_id, updated, current["etag"])
        result["drive_comment_preserved"] = any(c["id"] == comment["id"] and not c.get("deleted")
                                                  for c in drive.comments(file_id))
        result["pdf_comment_updated"] = drive.version(file_id)["md5Checksum"] == hashlib.md5(updated.read_bytes()).hexdigest()
    finally:
        if file_id:
            try:
                current = drive.version(file_id)
                drive.request("DELETE", "drive/v2/files/" + quote(file_id, safe=""),
                              headers={"If-Match": current["etag"]})
                result["temporary_file_removed"] = True
            except Exception as error:
                result["cleanup_error"] = str(error)
        temporary.unlink(missing_ok=True)
        updated.unlink(missing_ok=True)
        (PROJECT / ".state/drive-roundtrip-verification.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    if not all(result[k] for k in ("stale_version_rejected", "pdf_comment_updated",
                                   "drive_comment_preserved", "temporary_file_removed")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
