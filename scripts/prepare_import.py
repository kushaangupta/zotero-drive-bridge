#!/usr/bin/env python3
"""Prepare an RIS with real mounted attachment paths; leave source exports alone."""

import argparse
import json
from pathlib import Path
import re

from author_folders import plan_authors


def prepare(ris_path, json_path, index_path, output_dir, collection_name=None):
    original = ris_path.read_text()
    records = json.loads(json_path.read_text())
    index = json.loads(index_path.read_text())
    resolved = {row["relative_path"]: row for row in index["resolved"]}
    missing = []
    changed = 0
    lines = []
    for line in original.splitlines():
        if line.startswith("L1  - "):
            relative = line[6:].strip()
            if relative in resolved:
                line = "L1  - " + resolved[relative]["local_path"]
                changed += 1
            else:
                missing.append(relative)
                # Retain the reference and explain why its PDF was not attached.
                # An invalid relative link would silently fail on import.
                line = "N1  - Paperpile PDF needs matching from the original library: " + relative
        lines.append(line)

    rewritten = "\n".join(lines) + "\n"
    expected_count = len(re.findall(r"^TY  - ", original, re.M))
    if expected_count != len(records) or len(re.findall(r"^TY  - ", rewritten, re.M)) != expected_count:
        raise ValueError("Reference counts do not agree; refusing to prepare an import")
    output_dir.mkdir(parents=True, exist_ok=True)
    collection_name = collection_name or ris_path.stem
    ris_name = re.sub(r'[\\/:*?"<>|]', "_", collection_name).strip(". ") or "Paperpile"
    (output_dir / (ris_name + ".ris")).write_text(rewritten)
    author_plan = plan_authors([{
        "attachment_key": record["_id"],
        "firstName": (record.get("author") or [{}])[-1].get("first", ""),
        "lastName": (record.get("author") or [{}])[-1].get("last", ""),
    } for record in records])
    manifest_rows = []
    for record in records:
        manifest_rows.append({
            "paperpile_id": record["_id"],
            "title": record.get("title", ""),
            "doi": record.get("doi", ""),
            "citekey": record.get("citekey", ""),
            "authors": record.get("author", []),
            "author_folder": author_plan["assignments"].get(record["_id"], {}).get("folder"),
            "published": record.get("published", {}),
            "attachments": [{
                **attachment,
                "mounted": resolved.get(attachment["filename"]),
            } for attachment in record.get("attachments", [])],
        })
    manifest = {
        "collection_name": collection_name,
        "references": manifest_rows,
        "folder_pattern": "{last_author_first_initial}. {last_author_surname}",
        "author_review": author_plan["review"],
        "filename_pattern": "{year}-{first_author_surname}-{title_pascal_case}.pdf",
    }
    (output_dir / "migration-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    report = {
        "references": len(records),
        "pdfs_listed_in_export": sum(len(row.get("attachments", [])) for row in records),
        "matched_pdfs": changed,
        "matched_pdf_bytes": sum(row["size"] for row in resolved.values()),
        "unmatched_pdf_paths": missing,
        "references_without_exported_pdfs": sum(not row.get("attachments") for row in records),
        "matched_files_whose_size_differs_from_export": sum(row["size"] != row["expected_size"] for row in resolved.values()),
        "notes": [
            "No Zotero items were created or changed by preparation.",
            "The prepared RIS is for a fresh import, not a second import into an existing collection.",
            "PDFs remain on the mounted Drive until Zotero imports them.",
            "A size difference may reflect annotations; the mounted file is preserved as the source.",
        ],
    }
    (output_dir / "migration-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "unmatched_pdf_paths"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ris", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--collection", help="Exact name of the Zotero collection to repair")
    args = parser.parse_args()
    prepare(args.ris, args.json, args.index, args.output, args.collection)
