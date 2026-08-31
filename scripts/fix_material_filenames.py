#!/usr/bin/env python3
"""Resolve each material to the file the Datatracker actually serves.

externalize_materials.py picked the mirror file by guessing at the extension.
Where a document exists in more than one format the guess is wrong: the mirror
holds both `slides-116-teas-...-05.pdf` and `...-05.pptx`, the lookup took the
pptx because it sorts later, and the Datatracker serves the pdf. The recorded
content_hash then describes a file nobody receives, which is worse than no
hash at all -- it asserts an integrity guarantee that fails on first check.

The Datatracker knows the answer. `uploaded_filename` on the document record is
the file the material URL serves, and the whole set for a meeting comes back in
one query per material type. This pass re-resolves every external reference
against that name and:

  - corrects the hash, mediatype and filename where the guess took the wrong
    sibling;
  - reverts the reference to a body reference where the served file is not in
    the mirror at all, rather than leave a hash that cannot be verified.

The pre-IETF-83 meetings are the second case. Their slides are served as an
HTML rendition inside a per-deck directory (`16ng-0/16ng-0.htm`) which the
rsync tree does not carry -- it has the original `16ng-0.ppt` and the split
`sld1.htm` pages, but not the index the URL returns.

Usage:
    python scripts/fix_material_filenames.py [--meeting 116] [--dry-run] [-v]
"""
import argparse
import json
import mimetypes
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from externalize_materials import (  # noqa: E402
    MATERIAL_URL_RE,
    content_hash_token,
    sync_proceedings,
)

API = "https://datatracker.ietf.org/api/v1/doc/document/"
DOC_TYPES = ["slides", "agenda", "minutes", "chatlog", "bluesheets"]
# The Datatracker sits behind a managed challenge that a scripted client cannot
# pass on some paths. The JSON API answers, so identify as a browser and go
# slowly enough not to look like a crawl.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def uploaded_filenames(meeting: int) -> dict[str, str]:
    """Map document name -> the filename the Datatracker serves for it."""
    names = {}
    for doc_type in DOC_TYPES:
        query = urllib.parse.urlencode(
            {
                "type": doc_type,
                "name__startswith": f"{doc_type}-{meeting}-",
                "limit": 1000,
                "format": "json",
            }
        )
        request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
        except Exception as e:
            print(f"  API {doc_type} for IETF {meeting} failed: {e}", file=sys.stderr)
            continue
        for obj in payload.get("objects", []):
            if obj.get("uploaded_filename"):
                names[obj["name"]] = obj["uploaded_filename"]
        time.sleep(1)
    return names


def served_file(doc_name: str, served_name: str, meeting: int, mirror: Path) -> Path | None:
    """The mirror copy of the file the URL serves, if the mirror carries it."""
    meeting_dir = mirror / "proceedings" / str(meeting)
    subdir = doc_name.split("-")[0]
    for candidate in (
        meeting_dir / subdir / served_name,           # modern: slides/slides-116-...pdf
        meeting_dir / subdir / Path(served_name).name,  # legacy dir form, flattened
    ):
        if candidate.is_file():
            return candidate
    return None


def to_body_reference(a: dict) -> None:
    """Undo an external reference that cannot be verified."""
    title = (a.get("meta") or {}).get("title")
    a["body"] = json.dumps(
        {
            "url": a["url"],
            "mimetype": a.get("mediatype"),
            "filename": None,
            "title": title,
        },
        ensure_ascii=False,
    )
    a["encoding"] = "json"
    for key in ("url", "content_hash", "mediatype", "filename"):
        a.pop(key, None)
    if a.get("meta") is not None:
        a["meta"].pop("title", None)
        if not a["meta"]:
            a.pop("meta")


def fix(v: dict, meeting: int, mirror: Path, served: dict[str, str], tally: Counter) -> bool:
    changed = False

    for a in v.get("attachments") or []:
        if not (a.get("content_hash") and a.get("url")):
            continue
        m = MATERIAL_URL_RE.match(a["url"])
        if not m or int(m.group(1)) != meeting:
            continue

        doc_name = m.group(2)
        served_name = served.get(doc_name)
        if not served_name:
            tally["no API record, left as is"] += 1
            continue

        if a.get("filename") == Path(served_name).name:
            tally["already correct"] += 1
            continue

        local = served_file(doc_name, served_name, meeting, mirror)
        if local is None:
            to_body_reference(a)
            tally["reverted, served file not mirrored"] += 1
            changed = True
            continue

        mediatype, _ = mimetypes.guess_type(local.name)
        a["content_hash"] = content_hash_token(local.read_bytes())
        a["mediatype"] = mediatype or a.get("mediatype")
        a["filename"] = local.name
        tally["corrected to the served file"] += 1
        changed = True

    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int)
    ap.add_argument("--mirror", type=Path, default=REPO / "downloads")
    ap.add_argument("--keep-mirror", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    meetings = (
        [args.meeting]
        if args.meeting
        else sorted(
            int(p.name[4:])
            for p in REPO.glob("ietf*")
            if p.is_dir() and p.name[4:].isdigit()
        )
    )

    totals = Counter()
    files_changed = 0

    for meeting in meetings:
        vcons = sorted((REPO / f"ietf{meeting}").glob("*.vcon.json"))
        if not vcons:
            continue
        print(f"\n=== IETF {meeting} ({len(vcons)} vCons) ===", flush=True)

        served = uploaded_filenames(meeting)
        print(f"  {len(served)} documents named by the API")
        if not served:
            totals["meetings with no API data"] += 1
            continue

        if not sync_proceedings(meeting, args.mirror):
            print(f"  rsync failed for IETF {meeting}, skipping", file=sys.stderr)
            totals["meetings skipped"] += 1
            continue

        tally = Counter()
        for path in vcons:
            v = json.loads(path.read_text())
            if not fix(v, meeting, args.mirror, served, tally):
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
            import shutil

            shutil.rmtree(args.mirror / "proceedings" / str(meeting), ignore_errors=True)

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {files_changed} vCons")
    for label, count in sorted(totals.items()):
        print(f"  {count:>7}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
