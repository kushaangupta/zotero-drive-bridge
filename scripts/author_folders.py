# SPDX-License-Identifier: MIT
"""Stable author identities for initial-and-surname folders; no fuzzy matching."""
import copy
import hashlib
import re
import unicodedata


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(c for c in value if c.isalnum())


def describe(row):
    surname = " ".join(str(row.get("lastName") or "").split())
    # Middle names/initials do not determine the folder. Periods and hyphens
    # separate given-name tokens, so J., John R., and John share the same bucket.
    tokens = re.findall(r"[^\W\d_]+", str(row.get("firstName") or ""), re.UNICODE)
    given = tokens[0] if tokens else ""
    family = normalized(surname)
    first = normalized(given)
    organization = bool(row.get("fieldMode"))
    return {"surname": surname, "family": family, "given": given, "first": first,
            "middle": [normalized(token) for token in tokens[1:]],
            "initial": first[:1], "organization": organization,
            "alias": ("org:" if organization else "person:") + family + "|" + first}


def safe_folder(value):
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", value).strip(" .")
    if not value or value in (".", ".."):
        raise ValueError("An author folder must be a nonempty path component")
    return value


def plan_authors(records, previous=None):
    """Return assignments and persisted identities, keeping ambiguous names aside.

    Exact full first names, initials, and missing middle names can be reconciled
    only when the available candidates are unique. Different full first names
    are never merged just because they start with the same letter.
    """
    state = copy.deepcopy(previous or {"version": 1, "authors": {}, "aliases": {}, "attachments": {}})
    if state.get("version") != 1:
        raise ValueError("Unsupported author-map version")
    for key in ("authors", "aliases", "attachments"):
        state.setdefault(key, {})
    assignments, review = {}, []
    rows = [(str(r["attachment_key"]), describe(r)) for r in records]
    if len({key for key, _ in rows}) != len(rows):
        raise ValueError("Duplicate attachment keys in author metadata")

    def compatible(author, name):
        return (author["family"] == name["family"]
                and author.get("organization", False) == name["organization"]
                and all(a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))
                        for a, b in zip(author.get("middle", []), name["middle"]))
                and (not name["first"] or not author["first"]
                     or (len(name["first"]) == 1 and author["first"].startswith(name["first"]))
                     or (len(author["first"]) == 1 and name["first"].startswith(author["first"]))
                     or author["first"] == name["first"]))

    def create(name):
        identity = hashlib.sha256(name["alias"].encode()).hexdigest()[:16]
        folder = name["surname"] if name["organization"] or not name["first"] else name["given"][0].upper() + ". " + name["surname"]
        occupied = {a["folder"].casefold() for a in state["authors"].values()}
        if safe_folder(folder).casefold() in occupied:
            if len(name["first"]) <= 1:
                return None
            folder += " (" + name["given"] + ")"
        folder = safe_folder(folder)
        if folder.casefold() in occupied:
            return None
        state["authors"][identity] = {**name, "folder": folder}
        return identity

    # Resolve full names first, regardless of item order. A saved initial-only
    # identity can be enriched when exactly one full name fits it.
    full_names = {}
    for _, name in rows:
        if len(name["first"]) > 1:
            full_names.setdefault((name["family"], name["initial"]), set()).add(name["first"])
    for _, name in sorted(rows, key=lambda pair: (len(pair[1]["first"]) <= 1, pair[1]["alias"])):
        if not name["family"] or (not name["organization"] and len(name["first"]) <= 1):
            continue
        identity = state["aliases"].get(name["alias"])
        if not identity:
            candidates = [key for key, a in state["authors"].items() if compatible(a, name)]
            candidates = [key for key in candidates if state["authors"][key]["first"] == name["first"]
                          or (len(state["authors"][key]["first"]) <= 1
                              and len(full_names.get((name["family"], name["initial"]), ())) == 1)]
            identity = candidates[0] if len(candidates) == 1 else create(name)
            if identity:
                author = state["authors"][identity]
                if len(author["first"]) < len(name["first"]):
                    author.update({key: name[key] for key in ("first", "given")})
                state["aliases"][name["alias"]] = identity
        if identity and compatible(state["authors"][identity], name):
            current = state["authors"][identity]
            if sum(map(len, name["middle"])) > sum(map(len, current.get("middle", []))):
                current["middle"] = name["middle"]

    for key, name in rows:
        reason = None
        identity = state["attachments"].get(key)
        if identity and (identity not in state["authors"] or not compatible(state["authors"][identity], name)):
            reason = "author_changed_requires_review"
        elif identity and len(state["authors"][identity]["first"]) <= 1 and len(full_names.get((name["family"], name["initial"]), ())) > 1:
            reason = "ambiguous_author_name"
        elif not name["family"]:
            reason = "missing_last_author"
        elif not identity:
            candidates = [author_id for author_id, a in state["authors"].items() if compatible(a, name)]
            if len(name["first"]) > 1 or name["organization"]:
                identity = state["aliases"].get(name["alias"])
            elif len(candidates) == 1:
                identity = candidates[0]
            elif not candidates and name["first"]:
                identity = create(name)
            elif not candidates and not name["first"]:
                reason = "missing_given_name"
            if not identity and not reason:
                reason = "ambiguous_author_name"
        if identity and not reason and not compatible(state["authors"][identity], name):
            reason = "conflicting_given_names"
        if reason or not identity:
            review.append({"attachment_key": key, "reason": reason or "folder_collision",
                           "firstName": name["given"], "lastName": name["surname"]})
            continue
        state["attachments"][key] = identity
        assignments[key] = {"author_id": identity, "folder": state["authors"][identity]["folder"]}
    return {"version": 1, "assignments": assignments, "review": review, "state": state}
