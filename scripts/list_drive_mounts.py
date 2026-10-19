#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""List GNOME's connected Drive roots; no credentials or file contents are read."""
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gi.repository import Gio


def drive_mounts():
    rows = []
    for mount in Gio.VolumeMonitor.get().get_mounts():
        root = mount.get_root()
        uri = root.get_uri()
        if not uri.startswith("google-drive://"):
            continue
        # GVfs get_path() points at the URI's root-ID directory. The indexer
        # needs its mount base so it can append the complete opaque URI path.
        local = root.get_path()
        suffix = unquote(urlsplit(uri).path).rstrip("/")
        base = local[:-len(suffix)] if local and suffix and local.endswith(suffix) else local
        rows.append({"name": mount.get_name(), "root_uri": uri,
                     "mount_base": base, "mounted_root": local})
    return rows


if __name__ == "__main__":
    print(json.dumps(drive_mounts(), indent=2))
