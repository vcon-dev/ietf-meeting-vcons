#!/usr/bin/env python3
"""Move WTF transcript bodies out of the vCons and reference them externally.

Transcripts dominate this repository: an inline WTF body runs 200-400 KB, so a
meeting of ~140 transcribed sessions is ~45 MB and the full 110-126 set is
~670 MB. draft-ietf-vcon-vcon-core-02 anticipates exactly this. Per the
Analysis Object section:

    The Analysis Object SHOULD contain the body and encoding parameters
    or the url and content_hash parameters.

so an analysis entry may point at externally stored content instead of carrying
it, as long as it also carries a SHA-512 `content_hash` covering that content.
The hash is itself covered by the vCon's integrity signature, so externalising
a transcript does not weaken tamper-evidence: a substituted transcript fails
verification exactly as an edited inline one would.

This script rewrites inline WTF analysis entries into external references:

    {"type": "wtf_transcription", "dialog": 0, "vendor": "youtube",
     "product": "auto-captions",
     "url": "<prefix>/ietf126_vcon_35521.wtf.json",
     "content_hash": "sha512-<base64url, unpadded>"}

The extracted bodies are written as standalone .wtf.json files for hosting
(GitHub Releases, S3, a conserver). The content_hash format matches
`vcon.compute_content_hash` in the vcon library: "<algorithm>-<base64url of
digest, no padding>".

Usage:
    # Prototype into a separate tree, leaving the source untouched
    python scripts/externalize_transcripts.py --meeting 126 \
        --url-prefix https://example.org/transcripts/ietf126 \
        --dest-dir /tmp/ietf126-thin --body-dir /tmp/ietf126-bodies

    # Rewrite in place
    python scripts/externalize_transcripts.py --meeting 126 \
        --url-prefix https://example.org/transcripts/ietf126 \
        --body-dir transcripts/ietf126

    # Check that every reference still resolves against its local body file
    python scripts/externalize_transcripts.py --meeting 126 \
        --dest-dir /tmp/ietf126-thin --body-dir /tmp/ietf126-bodies --verify
"""
import argparse
import base64
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def content_hash(data: bytes, algorithm: str = "sha512") -> str:
    """Spec-formatted content hash: "<algorithm>-<base64url digest, unpadded>"."""
    digest = hashlib.new(algorithm, data).digest()
    return f"{algorithm}-{base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')}"


def body_filename(vcon_path: Path) -> str:
    """ietf126_vcon_35521.vcon.json -> ietf126_vcon_35521.wtf.json"""
    return vcon_path.name.replace(".vcon.json", "") + ".wtf.json"


def externalize(path: Path, dest: Path, body_dir: Path, url_prefix: str) -> tuple[bool, int]:
    """Rewrite one vCon. Returns (changed, bytes_extracted)."""
    vcon = json.loads(path.read_text(encoding="utf-8"))
    extracted = 0
    changed = False

    for entry in vcon.get("analysis") or []:
        if entry.get("type") != "wtf_transcription" or "body" not in entry:
            continue

        body = entry["body"]
        # Bodies are stored as JSON strings (core-02 requires a string body);
        # hash exactly the bytes that will be served.
        raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")

        name = body_filename(path)
        body_dir.mkdir(parents=True, exist_ok=True)
        (body_dir / name).write_bytes(raw)

        entry.pop("body", None)
        entry.pop("encoding", None)
        entry["url"] = f"{url_prefix.rstrip('/')}/{name}"
        entry["content_hash"] = content_hash(raw)
        # Well-known media type, per "If a well known media type is defined, it
        # SHOULD be used" in the Analysis Object section.
        entry.setdefault("mediatype", "application/json")

        extracted += len(raw)
        changed = True

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(vcon, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed, extracted


def verify(path: Path, body_dir: Path) -> tuple[int, list[str]]:
    """Re-hash each external reference against its local body file."""
    vcon = json.loads(path.read_text(encoding="utf-8"))
    checked, problems = 0, []

    for entry in vcon.get("analysis") or []:
        if entry.get("type") != "wtf_transcription" or "url" not in entry:
            continue
        name = entry["url"].rsplit("/", 1)[-1]
        blob = body_dir / name
        if not blob.exists():
            problems.append(f"{path.name}: missing body {name}")
            continue
        actual = content_hash(blob.read_bytes())
        if actual != entry.get("content_hash"):
            problems.append(f"{path.name}: hash mismatch for {name}")
        checked += 1

    return checked, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int, required=True)
    ap.add_argument("--url-prefix", help="Base URL the bodies will be served from")
    ap.add_argument("--body-dir", type=Path, required=True)
    ap.add_argument("--dest-dir", type=Path, help="Write rewritten vCons here (default: in place)")
    ap.add_argument("--verify", action="store_true", help="Check references instead of rewriting")
    args = ap.parse_args()

    src_dir = REPO / f"ietf{args.meeting}"
    if not src_dir.is_dir():
        print(f"No such meeting directory: {src_dir}")
        sys.exit(1)

    if args.verify:
        check_dir = args.dest_dir or src_dir
        checked, problems = 0, []
        for f in sorted(check_dir.glob("*.vcon.json")):
            c, p = verify(f, args.body_dir)
            checked += c
            problems.extend(p)
        for p in problems[:20]:
            print("  " + p)
        print(f"verified {checked} external references, {len(problems)} problems")
        sys.exit(1 if problems else 0)

    if not args.url_prefix:
        print("--url-prefix is required unless --verify")
        sys.exit(1)

    dest_dir = args.dest_dir or src_dir
    files = sorted(src_dir.glob("*.vcon.json"))
    changed = total = 0

    for f in files:
        # Files with no transcript are copied through unchanged so the output
        # tree is a complete meeting, not just the rewritten subset.
        did, n = externalize(f, dest_dir / f.name, args.body_dir, args.url_prefix)
        changed += did
        total += n

    print(f"externalized {changed}/{len(files)} vCons, {total / 1e6:.1f} MB of bodies -> {args.body_dir}")


if __name__ == "__main__":
    main()
