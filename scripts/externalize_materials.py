#!/usr/bin/env python3
"""Promote meeting materials from body blobs to real external references.

Every slide deck, agenda, minutes file, chat log and bluesheet in the corpus is
carried as a JSON object in the attachment `body`:

    {"purpose": "slides", "encoding": "json",
     "body": "{\"url\": ..., \"mimetype\": ..., \"filename\": ..., \"title\": ...}"}

A conforming vCon reader sees an opaque string where a linked document should
be. draft-ietf-vcon-vcon-core-02 registers `url`, `mediatype`, `filename` and
`content_hash` as attachment parameters for exactly this, so the reference
becomes:

    {"purpose": "slides", "url": ..., "content_hash": "sha512-...",
     "mediatype": ..., "filename": ..., "meta": {"title": ...}}

core-02 requires `url` and `content_hash` together, and the hash needs the
bytes, so this reads every material. It does that from a local rsync mirror of
rsync.ietf.org::proceedings rather than 64,000 HTTP requests. A full mirror is
~60 GB, so meetings are mirrored one at a time and purged once hashed, which
keeps peak disk around 2 GB.

The observed file also settles two things the old code guessed from the
document name: minutes and agendas were labelled application/pdf with a .pdf
filename when most of them are plain text.

Landing pages are left alone. The session page, the collaborative notes and a
draft's own page are mutable HTML; a content_hash on them would assert an
integrity guarantee that does not hold, so they stay body references. Run
migrate_real_urls.py first so those URLs are the real ones.

Idempotent: an attachment that already carries a content_hash is skipped, so a
re-run over a mirrored meeting is a no-op and an interrupted run resumes.

Usage:
    python scripts/externalize_materials.py [--meeting 125] [--dry-run] [-v]
    python scripts/externalize_materials.py --keep-mirror   # leave the rsync tree
"""
import argparse
import importlib.util
import json
import mimetypes
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_ietf2vcon():
    """Borrow the mirror lookup and hash helper from the ietf2vcon checkout.

    Both are subtle enough to be worth sharing rather than reimplementing: the
    lookup has to resolve an unversioned Datatracker name against a mirror that
    stores every revision, and the hash has to be the spec's token form.

    They are loaded straight from their files. Importing the package would drag
    in the converter and with it vcon-lib, httpx and the rest of the conversion
    stack; these two modules are pure stdlib, and this script needs nothing
    else from ietf2vcon.
    """
    src = REPO.parent / "ietf2vcon" / "src" / "ietf2vcon"
    if not src.is_dir():
        sys.exit(
            f"no ietf2vcon checkout at {src}.\n"
            "Clone https://github.com/vcon-dev/ietf2vcon beside this repo."
        )

    def load(name):
        spec = importlib.util.spec_from_file_location(f"_ietf2vcon_{name}", src / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    hashing, mirror = load("hashing"), load("rsync_mirror")
    return hashing.content_hash_token, mirror.find_local_file, mirror.sync_proceedings


content_hash_token, find_local_file, sync_proceedings = _load_ietf2vcon()

# A material file served by the Datatracker, as opposed to a landing page.
MATERIAL_URL_RE = re.compile(
    r"^https://datatracker\.ietf\.org/meeting/(\d+)/materials/([^/?]+)/?$"
)


def externalize(v: dict, meeting: int, mirror: Path, tally: Counter) -> bool:
    """Rewrite one vCon's material attachments in place. True if it changed."""
    changed = False

    for a in v.get("attachments") or []:
        if a.get("content_hash"):
            tally["already external"] += 1
            continue
        body = a.get("body")
        if not isinstance(body, str) or not body.startswith("{"):
            continue
        try:
            ref = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(ref, dict):
            continue

        m = MATERIAL_URL_RE.match(ref.get("url") or "")
        if not m:
            tally["landing page, left as body"] += 1
            continue
        if int(m.group(1)) != meeting:
            # A cross-meeting reference; its bytes are not in this mirror.
            tally["other meeting, skipped"] += 1
            continue

        local = find_local_file(m.group(2), meeting, mirror)
        if local is None:
            tally["not in mirror"] += 1
            continue

        content = local.read_bytes()
        mediatype, _ = mimetypes.guess_type(local.name)

        a["url"] = ref["url"]
        a["content_hash"] = content_hash_token(content)
        # What the file actually is beats what the document name implied.
        a["mediatype"] = mediatype or ref.get("mimetype")
        a["filename"] = local.name
        if ref.get("title"):
            # title has no registered attachment parameter, so it rides in meta.
            a.setdefault("meta", {})["title"] = ref["title"]
        a.pop("body", None)
        a.pop("encoding", None)

        tally["externalized"] += 1
        if mediatype and mediatype != ref.get("mimetype"):
            tally["mediatype corrected"] += 1
        changed = True

    if changed and any(x.get("meta") for x in v.get("attachments") or []):
        extensions = v.setdefault("extensions", [])
        if "meta" not in extensions:
            extensions.append("meta")

    return changed


def meeting_numbers(root: Path, only: int | None) -> list[int]:
    if only:
        return [only]
    return sorted(
        int(p.name[4:]) for p in root.glob("ietf*") if p.is_dir() and p.name[4:].isdigit()
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int, help="limit to one IETF meeting")
    ap.add_argument(
        "--mirror",
        type=Path,
        default=REPO / "downloads",
        help="root of the local proceedings mirror (default: downloads/)",
    )
    ap.add_argument(
        "--keep-mirror",
        action="store_true",
        help="do not delete each meeting's mirror after hashing it",
    )
    ap.add_argument("--dry-run", action="store_true", help="hash but do not write vCons")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    totals = Counter()
    files_changed = 0

    for meeting in meeting_numbers(REPO, args.meeting):
        vcons = sorted((REPO / f"ietf{meeting}").glob("*.vcon.json"))
        if not vcons:
            continue

        print(f"\n=== IETF {meeting} ({len(vcons)} vCons) ===", flush=True)
        if not sync_proceedings(meeting, args.mirror):
            print(f"  rsync failed for IETF {meeting}, skipping", file=sys.stderr)
            totals["meetings skipped"] += 1
            continue

        tally = Counter()
        for path in vcons:
            v = json.loads(path.read_text())
            if not externalize(v, meeting, args.mirror, tally):
                continue
            files_changed += 1
            if args.verbose:
                print(f"  {path.relative_to(REPO)}")
            if not args.dry_run:
                path.write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")

        for label, count in sorted(tally.items()):
            print(f"  {count:>7}  {label}")
        totals.update(tally)

        if not args.keep_mirror:
            shutil.rmtree(args.mirror / "proceedings" / str(meeting), ignore_errors=True)

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {files_changed} vCons")
    for label, count in sorted(totals.items()):
        print(f"  {count:>7}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
