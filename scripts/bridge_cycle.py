#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Desktop half of the annotation bridge, launched by the Zotero plugin.

No daemon or separate sign-in. Each invocation has a process lock. PDF updates
use the actual Drive v2 ETag and If-Match, verified against the live service.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import re
import shutil
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from gi.repository import Gio

from gnome_drive_api import DriveReader
from drive_annotation_codec import text
from publish_gnome_drive import children, unique, save_json
from bridge_config import PROJECT, configured_path, state_path

PDF_PYTHON = configured_path("pdf_python")


def md5(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "md5").hexdigest()


class DriveWriter(DriveReader):
    def request(self, method, endpoint, data=None, headers=None):
        req = Request("https://www.googleapis.com/" + endpoint, data=data,
                      headers={"Authorization": "Bearer " + self._access_token(), **(headers or {})}, method=method)
        with urlopen(req, timeout=120) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    def version(self, file_id):
        return self.request("GET", "drive/v2/files/" + quote(file_id, safe="") + "?" +
                            urlencode({"fields": "id,etag,md5Checksum,title"}))

    def update_pdf(self, file_id, path, version):
        with Path(path).open("rb") as stream:
            return self.request("PUT", "upload/drive/v2/files/" + quote(file_id, safe="") +
                                "?uploadType=media&fields=id,etag,md5Checksum", stream,
                                {"Content-Type": "application/pdf", "Content-Length": str(Path(path).stat().st_size),
                                 "If-Match": version})

    def update_comment(self, file_id, comment_id, body):
        return self.request("PATCH", "drive/v3/files/" + quote(file_id, safe="") +
                            "/comments/" + quote(comment_id, safe="") + "?fields=id,content,modifiedTime",
                            json.dumps({"content": body}).encode(), {"Content-Type": "application/json"})

    def download_pdf(self, file_id, expected_md5):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
            raise ValueError("Unexpected Drive file ID")
        path = PROJECT / ".state/drive-incoming" / (file_id + ".pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and md5(path) == expected_md5:
            return path
        temporary = path.with_suffix(".partial.pdf")
        request = Request("https://www.googleapis.com/drive/v3/files/" + file_id + "?alt=media",
                          headers={"Authorization": "Bearer " + self._access_token()})
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if md5(temporary) != expected_md5:
            raise ValueError("Drive PDF changed during download; retry on the next sync")
        temporary.replace(path)
        return path


def compare_pdfs(local, remote):
    process = subprocess.run([str(PDF_PYTHON), str(PROJECT / "scripts/pdf_sync_compare.py"),
                              str(local), str(remote)], capture_output=True, text=True, check=True)
    return json.loads(process.stdout)


def current_pdf(drive, file_id, version, record):
    # A verified local copy avoids re-downloading a large, unchanged paper just
    # to compare PDF serialization. Mutable full exports must match its checksum.
    source = Path(record["source"])
    if source.exists() and md5(source) == version["md5Checksum"]:
        return source
    return drive.download_pdf(file_id, version["md5Checksum"])


def push():
    publication_path = state_path("registry")
    registry = json.loads(publication_path.read_text())
    account = registry["destination"].split("//", 1)[1].split("/", 1)[0]
    drive = DriveWriter(account)
    outbox = json.loads((PROJECT / ".state/drive-prepared-outbox.json").read_text())
    acknowledgments_path = PROJECT / ".state/drive-outgoing-ack.json"
    acks = json.loads(acknowledgments_path.read_text()) if acknowledgments_path.exists() else {}
    baseline_path = PROJECT / ".state/drive-sync-baselines.json"
    baselines = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    result = {"comments_updated": 0, "pdfs_updated": 0, "new_pdfs": 0, "unchanged_pdfs": 0,
              "conflicts": [], "errors": []}
    known_ids = {row["remote_uri"].split("/")[-1] for row in registry["files"].values()}
    for packet in outbox.get("comments", []):
        try:
            if packet["file_id"] not in known_ids:
                raise ValueError("Comment does not belong to a published migration PDF")
            comments = drive.comments(packet["file_id"])
            current = next((c for c in comments if c["id"] == packet["comment_id"]), None)
            body = text(packet["body"])
            if not current or current.get("deleted"):
                raise ValueError("Drive comment was deleted; keeping the Zotero edit for review")
            if text(current.get("content")) != body:
                if text(current.get("content")) != packet["expected_body"]:
                    result["conflicts"].append({"kind": "comment", "id": packet["id"]})
                    continue
                # Preserve the exact pre-write record as a private audit trail.
                with (PROJECT / ".state/drive-comment-audit.jsonl").open("a") as audit:
                    audit.write(json.dumps({"file_id": packet["file_id"], "before": current,
                                            "request_id": packet["request_id"]}) + "\n")
                drive.update_comment(packet["file_id"], packet["comment_id"], body)
                result["comments_updated"] += 1
            acks[packet["id"]] = {**packet, "body": body}
            save_json(acknowledgments_path, acks)
        except Exception as error:
            result["errors"].append({"kind": "comment", "id": packet["id"], "error": str(error)})

    root = Gio.File.new_for_uri(registry["destination"])
    root_rows = children(root)
    for key, packet in outbox["files"].items():
        try:
            path = Path(packet["drive_path"]).resolve(strict=True)
            if not path.is_relative_to(PROJECT / "output/sync"):
                raise ValueError("Export is outside the allowed sync directory")
            remote = registry["files"].get(key)
            if remote:
                file_id = remote["remote_uri"].split("/")[-1]
                if remote["relative_path"] != packet["relative_path"]:
                    result["conflicts"].append({"kind": "metadata_path_changed", "attachment_key": key})
                    continue
                version = drive.version(file_id)
                baseline = baselines.get(key)
                if baseline is None:
                    baseline = {"md5": md5(remote["source"])}
                if version["md5Checksum"] == packet["md5"]:
                    result["unchanged_pdfs"] += 1
                else:
                    incoming = current_pdf(drive, file_id, version, remote)
                    comparison = compare_pdfs(packet["full_path"], incoming)
                    if not comparison["same_document"] or comparison["remote_extra_annotations"]:
                        result["conflicts"].append({"kind": "pdf_content_or_annotation_conflict", "attachment_key": key})
                        continue
                    if comparison["same_annotations"]:
                        result["unchanged_pdfs"] += 1
                        baselines[key] = {"md5": version["md5Checksum"], "file_id": file_id}
                        save_json(baseline_path, baselines)
                        continue
                    after = drive.update_pdf(file_id, path, version["etag"])
                    if after.get("md5Checksum") != packet["md5"]:
                        raise ValueError("Drive did not confirm the expected uploaded checksum")
                    result["pdfs_updated"] += 1
                baselines[key] = {"md5": packet["md5"], "file_id": file_id}
            else:
                relative = Path(packet["relative_path"])
                if len(relative.parts) != 2 or any(v in (".", "..") for v in relative.parts):
                    raise ValueError("Invalid last-author export path")
                folder = unique(root_rows, relative.parts[0])
                if folder is None:
                    root.get_child_for_display_name(relative.parts[0]).make_directory(None)
                    root_rows = children(root)
                    folder = unique(root_rows, relative.parts[0])
                if not folder or not folder["directory"]:
                    raise ValueError("Author directory is ambiguous or missing")
                listing = children(folder["file"])
                existing = unique(listing, relative.name)
                if existing is not None:
                    result["conflicts"].append({"kind": "existing_unowned_filename", "attachment_key": key})
                    continue
                Gio.File.new_for_path(str(path)).copy(folder["file"].get_child_for_display_name(relative.name),
                                                     Gio.FileCopyFlags.NONE, None, None, None)
                uploaded = unique(children(folder["file"]), relative.name)
                file_id = uploaded["file"].get_uri().split("/")[-1]
                version = drive.version(file_id)
                if version["md5Checksum"] != packet["md5"]:
                    raise ValueError("New upload checksum differs")
                registry["files"][key] = {"attachment_key": key, "attachment_id": packet["attachment_id"],
                    "zotero_item_id": packet["zotero_item_id"], "relative_path": packet["relative_path"],
                    "source": packet["full_path"], "remote_uri": uploaded["file"].get_uri()}
                baselines[key] = {"md5": packet["md5"], "file_id": file_id}
                save_json(publication_path, registry)
                result["new_pdfs"] += 1
            save_json(baseline_path, baselines)
        except HTTPError as error:
            result["conflicts" if error.code == 412 else "errors"].append(
                {"kind": "file_version_changed" if error.code == 412 else "http_error", "attachment_key": key, "status": error.code})
        except Exception as error:
            result["errors"].append({"kind": "pdf", "attachment_key": key, "error": str(error)})
    save_json(PROJECT / ".state/drive-push-report.json", result)
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in result.items()}))


def check_remote_pdfs():
    registry = json.loads(state_path("registry").read_text())
    account = registry["destination"].split("//", 1)[1].split("/", 1)[0]
    baseline_path = PROJECT / ".state/drive-sync-baselines.json"
    baselines = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    excluded = {}

    def check(key, row):
        file_id = row["remote_uri"].split("/")[-1]
        baseline = baselines.get(key, {}).get("md5") or md5(row["source"])
        reader = DriveWriter(account)
        version = reader.version(file_id)
        if version["md5Checksum"] != baseline:
            incoming = reader.download_pdf(file_id, version["md5Checksum"])
            comparison = compare_pdfs(row["source"], incoming)
            if not comparison["same_document"]:
                raise ValueError("PDF document content changed in Drive; preserving both sides for review")
            baseline = version["md5Checksum"]
        return key, {"md5": baseline, "file_id": file_id}

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(check, key, row): row for key, row in registry["files"].items()}
        for task in as_completed(jobs):
            try:
                key, baseline = task.result()
                baselines[key] = baseline
            except Exception as error:
                file_id = jobs[task]["remote_uri"].split("/")[-1]
                excluded[file_id] = str(error)
    save_json(baseline_path, baselines)
    save_json(PROJECT / ".state/drive-untrusted-files.json", excluded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("pull", "organize", "push"))
    args = parser.parse_args()
    (PROJECT / ".state").mkdir(exist_ok=True)
    with (PROJECT / ".state/bridge-cycle.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == "pull":
            check_remote_pdfs()
            subprocess.run(["/usr/bin/python3", str(PROJECT / "scripts/inspect_drive_comments.py")], check=True)
            subprocess.run([str(PDF_PYTHON), str(PROJECT / "scripts/prepare_drive_annotations.py")], check=True)
        elif args.phase == "organize":
            from organize_author_folders import organize
            plan = organize(apply=True)
            if plan.get("applied", {}).get("errors"):
                raise SystemExit("Author folder changes need review; see .state/author-folder-plan.json")
        else:
            subprocess.run([str(PDF_PYTHON), str(PROJECT / "scripts/prepare_drive_exports.py")], check=True)
            push()


if __name__ == "__main__":
    main()
