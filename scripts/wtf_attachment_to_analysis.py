#!/usr/bin/env python3
"""Move WTF transcriptions from `attachments` into `analysis`.

`ietf2vcon` writes YouTube-caption transcripts as an attachment with
`purpose: "wtf_transcription"`, but draft-howe-vcon-wtf-extension defines WTF
as an *analysis* type, and every transcript already in this repository lives in
`analysis`. This script normalises freshly converted meetings to that layout.

The analysis entry follows the shape used in the WTF draft and by
`scripts/whisper_transcribe.py`:

    {"type": "wtf_transcription", "dialog": 0, "vendor": ..., "product": ...,
     "encoding": "json", "body": {...}}

The vendor is taken from the WTF `metadata.provider` field when present.

Usage:
    python scripts/wtf_attachment_to_analysis.py --meeting 126 [--dry-run] [-v]
"""
import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PRODUCTS = {
    "youtube": "auto-captions",
    "whisper": "large-v3",
    "mlx-whisper": "large-v3",
}


def convert(v: dict) -> bool:
    """Move any WTF attachment into analysis. Returns True if changed."""
    attachments = v.get("attachments") or []
    moved = False

    remaining = []
    for a in attachments:
        if not isinstance(a, dict) or a.get("purpose") != "wtf_transcription":
            remaining.append(a)
            continue

        body = a.get("body")
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            remaining.append(a)
            continue

        vendor = (body.get("metadata") or {}).get("provider") or "unknown"
        entry = {
            "type": "wtf_transcription",
            "dialog": a.get("dialog", 0),
            "vendor": vendor,
            "encoding": "json",
            "body": body,
        }
        product = PRODUCTS.get(vendor) or (body.get("metadata") or {}).get("model")
        if product and product != "unknown":
            entry["product"] = product

        analysis = v.setdefault("analysis", [])
        # Replace an existing entry from the same vendor rather than duplicate.
        analysis[:] = [
            e
            for e in analysis
            if not (e.get("type") == "wtf_transcription" and e.get("vendor") == vendor)
        ]
        analysis.append(entry)
        moved = True

    if moved:
        v["attachments"] = remaining
        v["updated_at"] = datetime.now(timezone.utc).isoformat()
        if "wtf_transcription" not in v.setdefault("extensions", []):
            v["extensions"].append("wtf_transcription")

    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    pat = base / (f"ietf{args.meeting}" if args.meeting else "ietf*") / "*.vcon.json"
    files = sorted(glob.glob(str(pat)))
    if not files:
        print(f"No files matching {pat}")
        sys.exit(1)

    modified = 0
    for fp in files:
        data = json.loads(Path(fp).read_text(encoding="utf-8"))
        if convert(data):
            modified += 1
            if args.verbose:
                print(f"{'WOULD MOVE' if args.dry_run else 'MOVED'} {Path(fp).name}")
            if not args.dry_run:
                Path(fp).write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

    print(f"{'Would modify' if args.dry_run else 'Modified'} {modified}/{len(files)} files")


if __name__ == "__main__":
    main()
