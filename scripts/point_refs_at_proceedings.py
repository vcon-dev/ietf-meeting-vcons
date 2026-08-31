#!/usr/bin/env python3
"""Point every hashed reference at the URL that actually serves those bytes.

The external references carry a content_hash of the published file alongside
the Datatracker material URL. That URL is not a file: it is a display endpoint,
and what it returns depends on the era and the format.

    agenda-122-nfsv4      .md  -> rendered as an HTML page (2,045 bytes)
    slides-124-opsarea... .pptx -> converted to PDF (203,067 bytes)
    slides-116-netconf... .pdf  -> the PDF verbatim

So a hash of the published file disagrees with that URL for anything the
Datatracker renders or converts, and there is no way to tell which from the
document record alone.

The IETF also publishes every file verbatim, at the location the rsync tree
mirrors:

    https://www.ietf.org/proceedings/{meeting}/{type}/{filename}

Those bytes are the bytes that were hashed, they are the same bytes rsync
carries, and the path is not behind the managed challenge that blocks scripted
access to older Datatracker material URLs. That is what a content_hash should
be attached to, so the reference points there and the Datatracker page moves to
`meta.datatracker_url`, which is the right home for a human-facing landing page.

No network and no mirror: the filename is already recorded on each reference.

Usage:
    python scripts/point_refs_at_proceedings.py [--meeting 124] [--dry-run] [-v]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROCEEDINGS = "https://www.ietf.org/proceedings"

# The material type doubles as the proceedings subdirectory.
SUBDIR = {
    "slides": "slides",
    "agenda": "agenda",
    "minutes": "minutes",
    "chatlog": "chatlog",
    "bluesheets": "bluesheets",
}
DATATRACKER_MATERIAL_RE = re.compile(
    r"^https://datatracker\.ietf\.org/meeting/(\d+)/materials/([^/?]+)/?$"
)


def repoint(v: dict, meeting: int, tally: Counter) -> bool:
    changed = False

    for a in v.get("attachments") or []:
        if not (a.get("content_hash") and a.get("url") and a.get("filename")):
            continue
        m = DATATRACKER_MATERIAL_RE.match(a["url"])
        if not m:
            tally["already repointed"] += 1
            continue
        subdir = SUBDIR.get(a.get("purpose"))
        if not subdir:
            tally[f"no subdir for purpose {a.get('purpose')}"] += 1
            continue

        a.setdefault("meta", {})["datatracker_url"] = a["url"]
        a["url"] = f"{PROCEEDINGS}/{meeting}/{subdir}/{a['filename']}"
        tally["repointed"] += 1
        changed = True

    if changed:
        extensions = v.setdefault("extensions", [])
        if "meta" not in extensions:
            extensions.append("meta")

    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    pattern = f"ietf{args.meeting}/*.vcon.json" if args.meeting else "ietf*/*.vcon.json"
    files = sorted(REPO.glob(pattern))
    if not files:
        print(f"no vCons matched {pattern}", file=sys.stderr)
        return 1

    tally = Counter()
    touched = 0
    for path in files:
        meeting = int(re.match(r"ietf(\d+)", path.parent.name).group(1))
        v = json.loads(path.read_text())
        if not repoint(v, meeting, tally):
            continue
        touched += 1
        if args.verbose:
            print(f"  {path.relative_to(REPO)}")
        if not args.dry_run:
            path.write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {touched} of {len(files)} vCons")
    for label, count in sorted(tally.items()):
        print(f"  {count:>7}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
