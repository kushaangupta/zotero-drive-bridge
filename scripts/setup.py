#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Initialize, check, and prepare a Zotero/Google Drive bridge checkout."""
import argparse
import json
import subprocess
import sys

from bridge_config import PROJECT, configured_path, load_config, drive_identity


def initialize():
    path = PROJECT / "config.local.json"
    if path.exists():
        print("config.local.json already exists; left it unchanged.")
        return
    with path.open("x") as output:
        output.write((PROJECT / "config.example.json").read_text())
    path.chmod(0o600)
    (PROJECT / "input").mkdir(exist_ok=True)
    print("Created ignored config.local.json and input/. Edit the example values before preparing a library.")


def doctor(migration=False):
    config = load_config(required=True)
    failures = []

    def check(name, action):
        try:
            action()
            print("OK   " + name)
        except Exception as error:
            failures.append(name)
            print("FAIL " + name + ": " + str(error))

    def python_check():
        subprocess.run([str(configured_path("pdf_python", config)), "-c",
                        "import pypdf; print('pypdf ' + pypdf.__version__)"], check=True)
    check("PDF Python environment", python_check)

    def session_check():
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        objects = bus.call_sync("org.gnome.OnlineAccounts", "/org/gnome/OnlineAccounts",
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None, None,
            Gio.DBusCallFlags.NONE, 10000, None).unpack()[0]
        identities = {data.get("org.gnome.OnlineAccounts.Account", {}).get("Identity")
                      for data in objects.values()
                      if "org.gnome.OnlineAccounts.OAuth2Based" in data}
        folder_keys = ("source_drive_uri", "destination_drive_uri") if migration else ("destination_drive_uri",)
        for key in folder_keys:
            uri = config.get(key)
            if not uri or "FOLDER_ID" in uri or "ACCOUNT_ROOT_ID" in uri:
                raise ValueError("Replace " + key + " in config.local.json")
            if drive_identity(uri) not in identities:
                raise ValueError("The account for " + key + " is not connected in GNOME Settings")
            info = Gio.File.new_for_uri(uri).query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
            if info.get_file_type() != Gio.FileType.DIRECTORY:
                raise ValueError(key + " does not name a folder")
    check("GNOME accounts and configured folders (read only)", session_check)

    if migration:
        for key in ("paperpile_ris", "paperpile_json", "source_mount_path"):
            def path_check(key=key):
                if not config.get(key) or not configured_path(key, config).exists():
                    raise ValueError("Set an existing " + key)
            check(key, path_check)
    if failures:
        print("Fix the failed checks before continuing.")
        return 1
    print("Checks passed. No Zotero items, Drive files, or settings were changed.")
    return 0


def prepare():
    config = load_config(required=True)
    for key in ("source_drive_uri", "source_mount_path"):
        if not config.get(key):
            raise ValueError("Set " + key + " in config.local.json")
    index = PROJECT / ".state/drive-index.json"
    subprocess.run([str(configured_path("desktop_python", config)), str(PROJECT / "scripts/index_gnome_drive.py"),
                    "--json", str(configured_path("paperpile_json", config)),
                    "--uri", config["source_drive_uri"],
                    "--mount", str(configured_path("source_mount_path", config)),
                    "--output", str(index)], check=True)
    subprocess.run([str(configured_path("pdf_python", config)), str(PROJECT / "scripts/prepare_import.py"),
                    "--ris", str(configured_path("paperpile_ris", config)),
                    "--json", str(configured_path("paperpile_json", config)),
                    "--index", str(index), "--output", str(PROJECT / "output/migration"),
                    "--collection", config["collection_name"]], check=True)
    print("Prepared the RIS and matching manifest under output/migration/. Zotero has not been changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create ignored local config without overwriting it")
    check = sub.add_parser("doctor", help="Read-only prerequisites check")
    check.add_argument("--migration", action="store_true", help="Also check the original RIS/JSON inputs and source mount")
    sub.add_parser("prepare", help="Index mounted PDFs and prepare RIS/manifest")
    sub.add_parser("build", help="Build XPI and print Zotero launcher commands")
    sub.add_parser("commands", help="Print previously generated Zotero launcher commands")
    args = parser.parse_args()
    if args.command == "init":
        initialize()
    elif args.command == "doctor":
        return doctor(args.migration)
    elif args.command == "prepare":
        prepare()
    elif args.command == "build":
        from build_drive_bridge import build
        output, instructions = build()
        print("Built " + str(output))
        print(instructions)
    else:
        path = PROJECT / "output/setup/commands.txt"
        if not path.exists():
            raise ValueError("Run python3 scripts/setup.py build first")
        print(path.read_text())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
