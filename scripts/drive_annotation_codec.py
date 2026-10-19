# SPDX-License-Identifier: MIT
"""Convert the observed Drive PDF anchor shape into PDF coordinates.

Drive's preview anchor encoding is undocumented. Accept only the exact structure
observed in this library, validate all page/rectangle bounds, and reject other
shapes. Never guess a page or put an unplaced comment at an arbitrary position.
"""
import html
import json
import math
import re


def anchor_rectangles(anchor, page_count):
    value = json.loads(anchor)
    if not isinstance(value, list) or len(value) < 3 or not isinstance(value[2], list):
        raise ValueError("Unrecognized Drive anchor structure")
    result = {}
    for group in value[2]:
        if not isinstance(group, list):
            raise ValueError("Unrecognized Drive anchor group")
        for entry in group:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ValueError("Unrecognized Drive anchor entry")
            index, rectangles = entry
            if type(index) is not int or not 0 <= index < page_count or not isinstance(rectangles, list):
                raise ValueError("Invalid Drive page index")
            for rect in rectangles:
                if not isinstance(rect, list) or len(rect) != 4 or not all(
                    type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in rect
                ) or rect[2] <= rect[0] or rect[3] <= rect[1]:
                    raise ValueError("Invalid normalized Drive rectangle")
                result.setdefault(index, []).append(rect)
    if not result:
        raise ValueError("Drive comment has no page rectangles")
    return result


def pdf_rect(rect, page):
    crop, media = page.cropbox, page.mediabox
    x0, y0 = max(float(crop.left), float(media.left)), max(float(crop.bottom), float(media.bottom))
    x1, y1 = min(float(crop.right), float(media.right)), min(float(crop.top), float(media.top))
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        raise ValueError("Invalid PDF page box")
    rotation = page.rotation % 360

    def point(x, y):
        if rotation == 0:
            return x0 + x * width, y1 - y * height
        if rotation == 90:
            return x0 + y * width, y0 + x * height
        if rotation == 180:
            return x1 - x * width, y0 + y * height
        if rotation == 270:
            return x1 - y * width, y1 - x * height
        raise ValueError("Unsupported PDF page rotation")

    points = [point(x, y) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    return [round(min(p[0] for p in points), 3), round(min(p[1] for p in points), 3),
            round(max(p[0] for p in points), 3), round(max(p[1] for p in points), 3)]


def bounds(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects)]


def overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


def text(value):
    # Match the rich-text tags stripped by Zotero's PDF export, retaining other
    # literal angle brackets rather than treating a comment as arbitrary HTML.
    return html.unescape(re.sub(r"</?(?:i|b|sub|sup)>", "", str(value or ""))).replace("\r\n", "\n").strip()


def embedded_annotations(pdf):
    rows = []
    for page_index, page in enumerate(pdf.pages):
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            key = annotation.get("/Zotero:Key")
            if not key or not annotation.get("/Rect"):
                continue
            rows.append({"key": str(key), "page": page_index,
                         "comment": text(annotation.get("/Contents")),
                         "rect": [float(v) for v in annotation["/Rect"]]})
    return rows


def convert_comment(comment, pdf, native):
    if comment.get("deleted"):
        return []
    by_page = anchor_rectangles(comment.get("anchor", ""), len(pdf.pages))
    body = text(comment.get("content"))
    replies = [r for r in comment.get("replies", []) if not r.get("deleted")]
    combined = body
    for reply in replies:
        author = reply.get("author", {}).get("displayName", "Reply")
        combined += "\n\n" + author + ": " + text(reply.get("content"))
    quote = text(comment.get("quotedFileContent", {}).get("value"))
    rows = []
    for index, rectangles in by_page.items():
        rects = [pdf_rect(r, pdf.pages[index]) for r in rectangles]
        candidates = [a for a in native if a["page"] == index and a["comment"] == body
                      and overlap(a["rect"], bounds(rects)) > 0.7]
        if len(candidates) > 1:
            raise ValueError("Ambiguous existing annotation: refusing to duplicate it")
        rows.append({"drive_comment_id": comment["id"], "page_index": index,
                     "matched_zotero_key": candidates[0]["key"] if candidates else None,
                     "drive_modified": comment.get("modifiedTime"),
                     "drive_body": body, "drive_replies": replies,
                     "drive_owned": bool(comment.get("author", {}).get("me")),
                     "annotation": {"type": "highlight", "isExternal": False,
                                    "authorName": comment.get("author", {}).get("displayName", ""),
                                    "text": quote, "comment": combined,
                                    "color": "#ffd400", "pageLabel": str(index + 1),
                                    "sortIndex": f"{index:05d}|000000|{round(rectangles[0][1] * 10000):05d}",
                                    "position": {"pageIndex": index, "rects": rects}, "tags": []}})
    return rows
