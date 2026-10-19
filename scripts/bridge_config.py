#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Shared local settings. No credentials belong in this file or its JSON input."""
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_FILES = {
    "registry": ".state/drive-publication.json",
    "destination_comments": ".state/destination-drive-comments.json",
    "source_comments": ".state/source-drive-comments.json",
}
DEFAULTS = {
    "collection_name": "Research",
    "pdf_python": ".venv/bin/python",
    "desktop_python": "/usr/bin/python3",
    "poll_minutes": 5,
    "paperpile_ris": "input/library.ris",
    "paperpile_json": "input/library.json",
}


def drive_identity(uri):
    """GNOME uses the signed-in account identity as the URI authority."""
    value = urlsplit(uri)
    identity = unquote(value.netloc)
    parts = [unquote(p) for p in value.path.split("/") if p]
    if (value.scheme != "google-drive" or identity.count("@") != 1
            or ":" in identity or value.query or value.fragment
            or len(parts) < 2 or any(p in (".", "..") for p in parts)):
        raise ValueError("Expected a GNOME google-drive://account/ROOT_ID/FOLDER_ID URI")
    return identity


def load_config(project=PROJECT, required=False):
    project = Path(project).resolve()
    path = project / "config.local.json"
    if required and not path.is_file():
        raise ValueError("Create config.local.json with: python3 scripts/setup.py init")
    data = json.loads(path.read_text()) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError("config.local.json must be an object")
    allowed = set(DEFAULTS) | {"source_drive_uri", "source_mount_path",
                              "destination_drive_uri", "state_files"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError("Unknown configuration keys: " + ", ".join(sorted(unknown)))
    config = {**DEFAULTS, **data}
    minutes = config["poll_minutes"]
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
        raise ValueError("poll_minutes must be an integer between 1 and 1440")
    if not isinstance(config["collection_name"], str) or not config["collection_name"].strip():
        raise ValueError("collection_name must be a nonempty string")
    for key in ("source_drive_uri", "destination_drive_uri"):
        if config.get(key):
            drive_identity(config[key])
    if (config.get("source_drive_uri") and config.get("destination_drive_uri")
            and config["source_drive_uri"].rstrip("/") == config["destination_drive_uri"].rstrip("/")):
        raise ValueError("Use separate source and destination Drive folders")
    state_files = {**DEFAULT_STATE_FILES, **config.get("state_files", {})}
    if set(state_files) != set(DEFAULT_STATE_FILES):
        raise ValueError("Unknown state_files key")
    for value in state_files.values():
        path = (project / value).resolve()
        if not path.is_relative_to(project / ".state"):
            raise ValueError("State files must stay inside .state/")
    config["state_files"] = state_files
    return config


def configured_path(key, config=None, project=PROJECT):
    config = config if config is not None else load_config(project)
    # Resolving .venv/bin/python follows its symlink to the system binary and
    # loses the virtual environment's site-packages when that path is executed.
    return Path(os.path.abspath(Path(project) / Path(config[key]).expanduser()))


def state_path(key, config=None, project=PROJECT):
    config = config if config is not None else load_config(project)
    return Path(project) / config["state_files"][key]


def runtime_config(config=None, project=PROJECT):
    config = config if config is not None else load_config(project, required=True)
    return {"project": str(Path(project).resolve()),
            "desktop_python": str(configured_path("desktop_python", config, project)),
            "poll_minutes": config["poll_minutes"],
            "state_files": config["state_files"]}
