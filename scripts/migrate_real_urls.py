#!/usr/bin/env python3
"""Replace the invented Datatracker URLs in the published corpus with real ones.

`ietf2vcon` built two URLs that look plausible and 404. Both shipped into every
vCon here, so the corpus points readers at pages that do not exist:

  1. The session page. Written as /meeting/{n}/agenda/{wg}/, which 404s. The
     page that actually carries the agenda is /meeting/{n}/session/{wg}/.
     One per session, so the whole corpus.

  2. Drafts and RFCs discussed in session. Written as
     /meeting/{n}/materials/{draft-name}, as though a draft were a meeting
     material. It is not: it has its own page at /doc/{name}/. The material
     form 404s.

Both corrected targets are mutable HTML landing pages, so they stay body
references rather than gaining a content_hash that could not hold. A draft is
also retyped `document` and text/html, dropping the .pdf filename that the old
code guessed for it.

This is the corpus-side half of ietf2vcon 66f1b11, which fixed the generator.
It only rewrites references, never bytes, so it is safe to re-run: a second
pass reports no changes.

Usage:
    python scripts/migrate_real_urls.py [--dry-run] [--meeting 124] [-v]
"""
import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE = "https://datatracker.ietf.org"

# /meeting/{n}/agenda/{wg}/ -- the session page under its wrong name.
SESSION_PAGE_RE = re.compile(rf"^{re.escape(BASE)}/meeting/(\d+)/agenda/([^/]+)/?$")

# /meeting/{n}/materials/{draft-*|rfc\d+} -- a document under a materials path.
# The name test matches ietf2vcon's DOC_NAME_RE.
DOC_MATERIAL_RE = re.compile(
    rf"^{re.escape(BASE)}/meeting/\d+/materials/((?:draft-[^/]+|rfc\d+))/?$"
)


def migrate(v: dict) -> list[str]:
    """Rewrite invented URLs in one vCon. Returns a list of change labels."""
    changes = []

    for a in v.get("attachments") or []:
        body = a.get("body")
        if not isinstance(body, str) or not body.startswith("{"):
            continue
        try:
            ref = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(ref, dict) or not ref.get("url"):
            continue

        url = ref["url"]
        new = None

        m = SESSION_PAGE_RE.match(url)
        if m:
            meeting, group = m.groups()
            new = f"{BASE}/meeting/{meeting}/session/{group}/"
            changes.append("agenda -> session page")

        m = DOC_MATERIAL_RE.match(url)
        if m:
            new = f"{BASE}/doc/{m.group(1)}/"
            # It is a document landing page, not a slide deck PDF.
            ref["mimetype"] = "text/html"
            ref["filename"] = None
            if a.get("purpose") != "document":
                a["purpose"] = "document"
                changes.append("material -> document")
            changes.append("draft material -> doc page")

        if new and new != url:
            ref["url"] = new
            a["body"] = json.dumps(ref, ensure_ascii=False)

    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int, help="limit to one IETF meeting")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    pattern = f"ietf{args.meeting}/*.vcon.json" if args.meeting else "ietf*/*.vcon.json"
    files = sorted(root.glob(pattern))
    if not files:
        print(f"no vCons matched {pattern}", file=sys.stderr)
        return 1

    tally = Counter()
    touched = 0

    for path in files:
        v = json.loads(path.read_text())
        changes = migrate(v)
        if not changes:
            continue
        touched += 1
        tally.update(changes)
        if args.verbose:
            print(f"{path.relative_to(root)}: {', '.join(sorted(set(changes)))}")
        if not args.dry_run:
            path.write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {touched} of {len(files)} vCons")
    for label, count in sorted(tally.items()):
        print(f"  {count:>7}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
