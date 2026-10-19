#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit Git's commit candidates and optionally create a source-only ZIP."""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
PRIVATE_ROOTS = {'.state', 'input', 'output', 'mounts', '.venv', '.git'}
PRIVATE_SUFFIXES = {'.pdf', '.ris', '.bib', '.xpi', '.zip', '.sqlite', '.db', '.log'}
RULES = {
    'personal home path': re.compile(r'/(?:home|Users)/[A-Za-z0-9._-]+/'),
    'machine-specific runtime': re.compile(r'\.cache/' + r'codex-runtimes'),
    'Google OAuth token': re.compile(r'\bya29\.[A-Za-z0-9_-]{10,}'),
    'embedded private Drive link': re.compile(r'drive\.google\.com/(?:file/d/|drive/folders/)[A-Za-z0-9_-]{15,}'),
}
EMAIL = re.compile(r'[A-Za-z0-9._+%-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')


def candidates(project=PROJECT):
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=project, capture_output=True, check=True)
    return sorted(set(p.decode() for p in result.stdout.split(b'\0') if p))


def inspect_files(paths, project=PROJECT):
    errors = []
    for name in paths:
        relative = PurePosixPath(name)
        if (relative.parts[0] in PRIVATE_ROOTS or relative.suffix.lower() in PRIVATE_SUFFIXES
                or relative.name == 'config.local.json' or relative.name.startswith('.env')):
            errors.append((name, 'private/generated file is a commit candidate'))
            continue
        path = Path(project) / name
        if path.is_symlink():
            errors.append((name, 'source packages must not follow symlinks'))
            continue
        try:
            contents = path.read_text()
        except UnicodeDecodeError:
            errors.append((name, 'unexpected binary file in source repository'))
            continue
        for label, pattern in RULES.items():
            if pattern.search(contents):
                errors.append((name, label))
        for match in EMAIL.finditer(contents):
            domain = match.group(1).lower()
            if domain not in ('example.org', 'example.com', 'example.net') and not domain.endswith('.invalid'):
                errors.append((name, 'non-example email address'))
                break
    return errors


def package(paths, project=PROJECT):
    target = Path(project) / 'output/release/zotero-drive-bridge-source.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.partial.zip')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in paths:
            archive.write(Path(project) / name, 'zotero-drive-bridge/' + name)
    temporary.replace(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', action='store_true', help='Also create an audited source ZIP under ignored output/release/')
    args = parser.parse_args()
    paths = candidates()
    errors = inspect_files(paths)
    if errors:
        # Never echo the matched value: it could be a credential.
        for name, reason in errors:
            print(name + ': ' + reason)
        raise SystemExit(1)
    print('Audited ' + str(len(paths)) + ' commit candidates; no private paths, personal emails, Drive IDs, or OAuth tokens detected.')
    if args.package:
        print(package(paths))


if __name__ == '__main__':
    main()
