#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build Drive variants without duplicating Drive's own comment threads."""
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, TextStringObject

PROJECT = Path(__file__).resolve().parents[1]


def main():
    box = json.loads((PROJECT / ".state/drive-outbox.json").read_text())
    for key, row in box["files"].items():
        source = Path(row["full_path"]).resolve(strict=True)
        if not source.is_relative_to(PROJECT / "output/sync/annotated-pdfs"):
            raise ValueError("Unexpected full export location")
        excluded = set(row.get("exclude_keys", []))
        overrides = row.get("comment_overrides", {})
        pdf = PdfReader(source)
        needs_filter = False
        for page in pdf.pages:
            for reference in page.get("/Annots", []):
                annotation = reference.get_object()
                annotation_key = str(annotation.get("/Zotero:Key", ""))
                if annotation_key in excluded or (annotation_key in overrides
                    and str(annotation.get("/Contents", "")) != overrides[annotation_key]):
                    needs_filter = True
        if needs_filter:
            target = PROJECT / "output/sync/drive-pdfs" / (key + ".pdf")
            target.parent.mkdir(parents=True, exist_ok=True)
            writer = PdfWriter(clone_from=source)
            for page in writer.pages:
                retained = ArrayObject()
                for reference in page.get("/Annots", []):
                    annotation = reference.get_object()
                    annotation_key = str(annotation.get("/Zotero:Key", ""))
                    parent = annotation.get("/Parent")
                    parent_key = str(parent.get_object().get("/Zotero:Key", "")) if parent else ""
                    if annotation_key in excluded or parent_key in excluded:
                        continue
                    if annotation_key in overrides:
                        annotation[NameObject("/Contents")] = TextStringObject(overrides[annotation_key])
                    retained.append(reference)
                page[NameObject("/Annots")] = retained
            temporary = target.with_suffix(".partial.pdf")
            writer.write(temporary)
            temporary.replace(target)
        else:
            target = source
        with target.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "md5").hexdigest()
        row["drive_path"], row["md5"] = str(target), checksum
    path = PROJECT / ".state/drive-prepared-outbox.json"
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(box, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
    print(json.dumps({"prepared_exports": len(box["files"])}))


if __name__ == "__main__":
    main()
