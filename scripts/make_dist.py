"""Build the distributable zip and the release notes from the tree.

One folder `arsenal-builder/` the host can drop into any skills directory.
Deterministic (sorted entries, fixed timestamps) so two builds of the same
tag produce byte-identical archives; the old dist zips were built from a
hand-written file list that quietly missed files - the tree is the list now.

    python scripts/make_dist.py            -> dist/arsenal-builder-v<ver>.zip
    python scripts/make_dist.py --notes f  -> also write release notes to f

Version comes from templates/runner.py (RUNNER_VERSION); the release workflow
refuses to publish if that disagrees with the tag.
"""

import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".github", "__pycache__", "dist", ".codebuddy"}
SKIP_FILES = {".gitignore"}
STAMP = (2026, 1, 1, 0, 0, 0)


def runner_version():
    path = os.path.join(ROOT, "templates", "runner.py")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r'^RUNNER_VERSION = "([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("make_dist: RUNNER_VERSION not found in templates/runner.py")
    return match.group(1)


def iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_FILES or name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            yield full, "arsenal-builder/" + rel


def build(out_dir):
    version = runner_version()
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "arsenal-builder-v%s.zip" % version)
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for full, arcname in iter_files():
            info = zipfile.ZipInfo(arcname, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as handle:
                archive.writestr(info, handle.read())
            count += 1
    print("%s (%d files, %d bytes)" % (target, count, os.path.getsize(target)))
    return target, version


def release_notes(version):
    """The CHANGELOG section for this version, verbatim - the why, not a blurb."""
    path = os.path.join(ROOT, "CHANGELOG.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    pattern = re.compile(
        r"^## v%s .*?$(.*?)(?=^## v|\Z)" % re.escape(version),
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return "See CHANGELOG.md."
    body = match.group(1).strip()
    return "## v%s\n\n%s\n" % (version, body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"))
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    _, version = build(args.out)
    if args.notes:
        with open(args.notes, "w", encoding="utf-8") as handle:
            handle.write(release_notes(version))
        print("notes: %s" % args.notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
