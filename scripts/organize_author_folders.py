#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Preview or apply stable initial-and-surname folders without replacing PDFs."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

from author_folders import plan_authors, safe_folder
from bridge_config import PROJECT, state_path

FOLDER = "application/vnd.google-apps.folder"
FIELDS = "id,title,mimeType,etag,parents(id),md5Checksum,labels(trashed)"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def load(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback


def make_client(identity):
    from gnome_drive_api import DriveReader
    from urllib.request import Request, urlopen

    class Client(DriveReader):
        def request(self, method, endpoint, data=None, etag=None, **query):
            headers = {"Authorization": "Bearer " + self._access_token()}
            if data is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(data).encode()
            if etag:
                headers["If-Match"] = etag
            url = "https://www.googleapis.com/drive/v2/" + endpoint + "?" + urlencode(query)
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=45) as response:
                return json.load(response)

        def metadata(self, file_id):
            return self.request("GET", "files/" + quote(file_id, safe=""), fields=FIELDS)

        def children(self, folder_id):
            rows, token = [], None
            while True:
                query = {"q": "'" + folder_id + "' in parents and trashed = false",
                         "fields": "nextPageToken,items(" + FIELDS + ")", "maxResults": 1000}
                if token:
                    query["pageToken"] = token
                page = self.request("GET", "files", **query)
                rows.extend(page.get("items", []))
                token = page.get("nextPageToken")
                if not token:
                    return rows

        def rename(self, row, title):
            return self.request("PATCH", "files/" + quote(row["id"], safe=""),
                                {"title": title}, row["etag"], fields=FIELDS)

        def create(self, root_id, title):
            return self.request("POST", "files", {"title": title, "mimeType": FOLDER,
                                "parents": [{"id": root_id}]}, fields=FIELDS)

        def move(self, row, old_parent, new_parent):
            return self.request("PATCH", "files/" + quote(row["id"], safe=""), {}, row["etag"],
                                addParents=new_parent, removeParents=old_parent, fields=FIELDS)

        def trash(self, row):
            return self.request("POST", "files/" + quote(row["id"], safe="") + "/trash",
                                etag=row["etag"], fields=FIELDS)

    return Client(identity)


def apply_plan(registry, plan, client, persist):
    """Rename single-author folders; split mixed folders by moving the same IDs.

    Each successful operation is checkpointed. A retry can recognize an already
    renamed/moved file after interruption, using IDs rather than display paths.
    """
    destination = registry["destination"].rstrip("/")
    root_id = destination.rsplit("/", 1)[-1]
    roots = client.children(root_id)
    grouped = defaultdict(list)
    result = {"folders_renamed": 0, "files_moved": 0, "folders_created": 0,
              "empty_folders_trashed": 0, "errors": []}
    for key, assignment in plan["assignments"].items():
        row = registry["files"].get(key)
        if row:
            relative = Path(row["relative_path"])
            if len(relative.parts) != 2:
                raise ValueError("Expected an author folder and a PDF filename")
            folder = safe_folder(assignment["folder"])
            if folder != assignment["folder"]:
                raise ValueError("Unsafe author folder")
            uri = row["remote_uri"]
            if not uri.startswith(destination + "/") or len(uri[len(destination) + 1:].split("/")) != 2:
                raise ValueError("Registered file is outside the destination")
            if relative.parts[0] != folder:
                grouped[uri.split("/")[-2]].append((key, row, folder))

    def target_folder(title):
        matches = [r for r in roots if r["title"].casefold() == title.casefold()]
        if len(matches) > 1 or (matches and matches[0]["mimeType"] != FOLDER):
            raise ValueError("Ambiguous destination author folder: " + title)
        if not matches:
            matches = [client.create(root_id, title)]
            roots.extend(matches)
            result["folders_created"] += 1
        return matches[0]

    def checkpoint(key, row, folder, folder_id):
        row["relative_path"] = folder + "/" + Path(row["relative_path"]).name
        row["remote_uri"] = destination + "/" + folder_id + "/" + row["remote_uri"].rsplit("/", 1)[-1]
        row["author_id"] = plan["assignments"][key]["author_id"]
        persist(registry)

    for folder_id, records in grouped.items():
        try:
            folder = client.metadata(folder_id)
            if folder["mimeType"] != FOLDER or root_id not in [p["id"] for p in folder.get("parents", [])]:
                raise ValueError("Author folder is no longer inside the destination")
            contents = client.children(folder_id)
            desired = {name for _, _, name in records}
            owned = {row["remote_uri"].rsplit("/", 1)[-1] for _, row, _ in records}
            matches = [r for r in roots if len(desired) == 1
                       and r["title"].casefold() == next(iter(desired)).casefold() and r["id"] != folder_id]
            # Rename only if every child is one of the registered files being
            # reorganized, and nobody else already owns the target name.
            if len(desired) == 1 and {r["id"] for r in contents} == owned and not matches:
                title = next(iter(desired))
                if folder["title"] != title:
                    folder = client.rename(folder, title)
                    if folder["id"] != folder_id or folder["title"] != title:
                        raise ValueError("Folder rename verification failed")
                    result["folders_renamed"] += 1
                roots[:] = [folder if r["id"] == folder_id else r for r in roots]
                for key, row, name in records:
                    checkpoint(key, row, name, folder_id)
                continue
            for key, row, name in records:
                file_id = row["remote_uri"].rsplit("/", 1)[-1]
                current = client.metadata(file_id)
                if current.get("labels", {}).get("trashed"):
                    raise ValueError("Registered PDF is in the trash")
                target = target_folder(name)
                others = [r for r in client.children(target["id"])
                          if r["title"] == current["title"] and r["id"] != file_id]
                if others:
                    raise ValueError("Target already contains another PDF with this name")
                parents = [p["id"] for p in current.get("parents", [])]
                if target["id"] not in parents:
                    if folder_id not in parents:
                        raise ValueError("PDF moved outside its registered folder; review required")
                    changed = client.move(current, folder_id, target["id"])
                    changed_parents = [p["id"] for p in changed.get("parents", [])]
                    if (changed["id"] != file_id or changed.get("md5Checksum") != current.get("md5Checksum")
                            or target["id"] not in changed_parents or folder_id in changed_parents):
                        raise ValueError("Move verification failed")
                    result["files_moved"] += 1
                checkpoint(key, row, target["title"], target["id"])
            if not client.children(folder_id):
                client.trash(client.metadata(folder_id))
                result["empty_folders_trashed"] += 1
                roots[:] = [r for r in roots if r["id"] != folder_id]
        except Exception as error:
            result["errors"].append({"folder_id": folder_id, "error": str(error)})
        print(json.dumps({"phase": "author folders", "folders_renamed": result["folders_renamed"],
                          "files_moved": result["files_moved"], "errors": len(result["errors"])}), flush=True)
    return result


def organize(metadata_path=None, apply=False, project=PROJECT):
    project = Path(project)
    state_dir = project / ".state"
    metadata_path = Path(metadata_path) if metadata_path else state_dir / "author-folder-metadata.json"
    author_path = state_dir / "author-folders.json"
    plan_path = state_dir / "author-folder-plan.json"
    registry_path = state_path("registry", project=project)
    registry = load(registry_path, None)
    if not registry:
        raise ValueError("The publication registry is missing; complete the one-time migration first")
    metadata = load(metadata_path, None)
    if not metadata:
        raise ValueError("No author metadata snapshot. Run a cycle with the updated Zotero bridge first.")
    previous = load(author_path, None)
    plan = plan_authors(metadata["authors"], previous)
    changes = sum(key in registry["files"] and Path(registry["files"][key]["relative_path"]).parts[0] != a["folder"]
                  for key, a in plan["assignments"].items())
    plan["summary"] = {"assigned": len(plan["assignments"]), "authors": len(plan["state"]["authors"]),
                       "files_to_reorganize": changes, "review": len(plan["review"])}
    if apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if changes:
            backup = state_dir / "author-folder-backups" / (stamp + ".json")
            save(backup, {"registry": registry, "author_state": previous})
        save(author_path, plan["state"])
        if changes:
            client = make_client(unquote(urlsplit(registry["destination"]).netloc))
            plan["applied"] = apply_plan(registry, plan, client, lambda r: save(registry_path, r))
        else:
            plan["applied"] = {"errors": []}
    save(plan_path, plan)
    print(json.dumps({**plan["summary"], "applied": apply,
                      "errors": len(plan.get("applied", {}).get("errors", []))}), flush=True)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="Author snapshot produced by Zotero")
    parser.add_argument("--apply", action="store_true", help="Reorganize Drive files in place; default is preview")
    args = parser.parse_args()
    (PROJECT / ".state").mkdir(exist_ok=True)
    with (PROJECT / ".state/bridge-cycle.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = organize(args.metadata, args.apply)
    if plan.get("applied", {}).get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
