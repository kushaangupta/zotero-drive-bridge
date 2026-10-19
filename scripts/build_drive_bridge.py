#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a portable XPI plus private, machine-specific Zotero launchers."""
import argparse
import json
from pathlib import Path
import zipfile

from bridge_config import PROJECT, load_config, runtime_config


def build(project=PROJECT):
    project = Path(project).resolve()
    config = load_config(project, required=True)
    runtime = runtime_config(config, project)
    state = project / ".state"
    state.mkdir(exist_ok=True)
    runtime_path = state / "runtime-config.json"
    temp = runtime_path.with_suffix(".partial")
    temp.write_text(json.dumps(runtime, indent=2) + "\n")
    temp.replace(runtime_path)

    manifest = json.loads((project / "plugin/manifest.json").read_text())
    output = project / ("output/plugin/zotero-drive-bridge-" + manifest["version"] + ".xpi")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial.xpi")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "bootstrap.js", "engine.js"):
            archive.write(project / "plugin" / name, name)
        archive.write(project / "scripts/import_drive_annotations.js", "import_drive_annotations.js")
        archive.write(project / "LICENSE", "LICENSE")
    temporary.replace(output)

    setup = project / "output/setup"
    setup.mkdir(parents=True, exist_ok=True)
    template = (project / "scripts/install_drive_bridge.js").read_text()
    template = template.replace('"__PAPERPILE_PROJECT_ROOT__"', json.dumps(str(project)))
    template = template.replace('"__PAPERPILE_XPI_PATH__"', json.dumps(str(output)))
    (setup / "install_drive_bridge.js").write_text(template)
    for name, script, options in (
        ("preview_repair", "repair_zotero.js", {"dryRun": True}),
        ("repair_zotero", "repair_zotero.js", {}),
        ("import_drive_annotations", "import_drive_annotations.js", {}),
    ):
        launcher = "(async function () {\nconst PAPERPILE_RUNTIME = " + json.dumps(runtime) + ";\n"
        launcher += "const PAPERPILE_REPAIR_OPTIONS = " + json.dumps(options) + ";\n"
        launcher += "return await eval(await Zotero.File.getContentsAsync(" + json.dumps(str(project / "scripts" / script)) + "));\n})();\n"
        (setup / (name + ".js")).write_text(launcher)

    commands = []
    for name in ("preview_repair", "repair_zotero", "install_drive_bridge"):
        command = "return await eval(await Zotero.File.getContentsAsync(" + json.dumps(str(setup / (name + ".js"))) + "));"
        commands.append(name + ":\n" + command)
    instructions = "In Zotero: Tools → Developer → Run JavaScript. Check Run as async function.\n\n" + "\n\n".join(commands) + "\n"
    (setup / "commands.txt").write_text(instructions)
    return output, instructions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    output, instructions = build()
    print("Built " + str(output))
    print(instructions)
