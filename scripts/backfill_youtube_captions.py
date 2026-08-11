#!/usr/bin/env python3
"""Backfill YouTube auto-caption transcripts into existing meeting vCons.

Meetings 110-124 carry a recording URL but no transcript: `ietf2vcon` used to
skip caption fetching whenever the recording was a `youtu.be` short link, which
is the form the IETF Datatracker publishes. This script fills the gap in place,
without regenerating the vCons (UUIDs and `created_at` are preserved).

The analysis entry it writes is identical in shape to the one `ietf2vcon`
produces, after `scripts/wtf_attachment_to_analysis.py` and
`scripts/migrate_compliance_core02.py` have run:

    {"type": "wtf_transcription", "dialog": 0, "vendor": "youtube",
     "product": "auto-captions", "encoding": "json", "body": "<json string>"}

Only `yt-dlp` on PATH is required. Sessions that already have a YouTube
transcript are skipped, so the script is idempotent and safe to resume after an
interrupt. Transcripts from other vendors (e.g. Whisper) are left alone and can
coexist.

Usage:
    python scripts/backfill_youtube_captions.py --meetings 110-124
    python scripts/backfill_youtube_captions.py --meetings 120 --dry-run
    python scripts/backfill_youtube_captions.py --meetings 110-124 --workers 4
"""
import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

REPO = Path(__file__).resolve().parent.parent
VIDEO_ID = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})")

print_lock = Lock()


def log(msg: str) -> None:
    with print_lock:
        print(msg, flush=True)


def recording_url(vcon: dict) -> tuple[str, int] | None:
    """Return (url, dialog_index) of the first YouTube recording, if any."""
    for i, d in enumerate(vcon.get("dialog") or []):
        url = d.get("url") or ""
        # core-02 renamed the dialog type from "video" to "recording"; accept both.
        if d.get("type") in ("recording", "video") and VIDEO_ID.search(url):
            return url, i
    return None


def has_youtube_transcript(vcon: dict) -> bool:
    return any(
        a.get("type") == "wtf_transcription" and a.get("vendor") == "youtube"
        for a in (vcon.get("analysis") or [])
    )


def fetch_captions(video_id: str, workdir: Path) -> list[dict] | None:
    """Download JSON3 auto-captions for a video. Returns parsed events."""
    out = workdir / video_id
    result = subprocess.run(
        [
            "yt-dlp",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", "en",
            "--sub-format", "json3",
            "--skip-download",
            "--no-warnings",
            "-o", str(out),
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    for candidate in sorted(workdir.glob(f"{video_id}*.json3")):
        return json.loads(candidate.read_text(encoding="utf-8")).get("events", [])

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1][:200] if result.stderr else "yt-dlp failed")
    return None


def events_to_wtf(events: list[dict]) -> dict | None:
    """Convert YouTube JSON3 caption events to a WTF transcription body.

    Mirrors ietf2vcon's YouTubeCaptionLoader so backfilled meetings are
    structurally identical to IETF 126.
    """
    segments: list[dict] = []
    parts: list[str] = []

    for event in events:
        if "segs" not in event:
            continue
        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)

        text = "".join(s.get("utf8", "") for s in event["segs"] if s.get("utf8", "").strip()).strip()
        if not text:
            continue

        segments.append({
            "id": len(segments),
            "start": round(start_ms / 1000.0, 3),
            "end": round((start_ms + duration_ms) / 1000.0, 3),
            "text": text,
            "confidence": 0.95,
        })
        parts.append(text)

    if not segments:
        return None

    now = datetime.now(timezone.utc).isoformat()
    return {
        "transcript": {
            "text": " ".join(parts),
            "language": "en",
            "duration": segments[-1]["end"],
            "confidence": 0.95,
        },
        "segments": segments,
        "metadata": {
            "created_at": now,
            "processed_at": now,
            "provider": "youtube",
            "model": "auto-generated",
        },
    }


def process(path: Path, workdir: Path, dry_run: bool, delay: float = 0.0) -> tuple[str, str]:
    """Returns (status, detail). Status is one of ok/skip/none/error."""
    vcon = json.loads(path.read_text(encoding="utf-8"))

    if has_youtube_transcript(vcon):
        return "skip", "already has YouTube transcript"

    found = recording_url(vcon)
    if not found:
        return "skip", "no recording"

    url, dialog_index = found
    video_id = VIDEO_ID.search(url).group(1)

    if dry_run:
        return "ok", f"would fetch {video_id}"

    # YouTube throttles bulk caption fetches aggressively and, once it starts
    # answering "Sign in to confirm you're not a bot", keeps doing so for a
    # long while. Jittered spacing between requests is what keeps a long run
    # alive; see --delay.
    if delay:
        time.sleep(delay * random.uniform(0.5, 1.5))

    try:
        events = fetch_captions(video_id, workdir)
    except Exception as e:
        return "error", str(e)

    if not events:
        return "none", f"{video_id}: no captions published"

    body = events_to_wtf(events)
    if not body:
        return "none", f"{video_id}: captions empty"

    entry = {
        "type": "wtf_transcription",
        "dialog": dialog_index,
        "vendor": "youtube",
        "product": "auto-captions",
        "encoding": "json",
        # core-02: analysis body MUST be a string.
        "body": json.dumps(body, ensure_ascii=False),
    }

    vcon.setdefault("analysis", []).append(entry)
    vcon["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "wtf_transcription" not in vcon.setdefault("extensions", []):
        vcon["extensions"].append("wtf_transcription")

    path.write_text(json.dumps(vcon, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "ok", f"{len(body['segments'])} segments"


def parse_meetings(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings", required=True, help="e.g. 110-124 or 120,121")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait before each fetch, jittered 0.5x-1.5x (default: 3)",
    )
    ap.add_argument(
        "--give-up-after",
        type=int,
        default=40,
        help="Abort after this many consecutive throttling errors (default: 40)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files: list[Path] = []
    for m in parse_meetings(args.meetings):
        d = REPO / f"ietf{m}"
        if not d.is_dir():
            log(f"skipping ietf{m}: no such directory")
            continue
        files.extend(sorted(d.glob("*.vcon.json")))

    if not files:
        log("No vCon files found")
        sys.exit(1)

    log(f"Scanning {len(files)} vCons across {len(parse_meetings(args.meetings))} meetings")

    counts = {"ok": 0, "skip": 0, "none": 0, "error": 0}
    done = 0
    consecutive_throttled = 0
    aborted = False

    with tempfile.TemporaryDirectory(prefix="ietf-captions-") as tmp:
        workdir = Path(tmp)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process, f, workdir, args.dry_run, args.delay): f
                for f in files
            }
            for fut in as_completed(futures):
                f = futures[fut]
                try:
                    status, detail = fut.result()
                except Exception as e:
                    status, detail = "error", str(e)
                counts[status] += 1
                done += 1

                if status == "error" and "not a bot" in detail:
                    consecutive_throttled += 1
                elif status in ("ok", "none"):
                    consecutive_throttled = 0

                if status in ("ok", "error", "none"):
                    log(f"[{done}/{len(files)}] {status:5s} {f.parent.name}/{f.name}: {detail}")
                elif done % 100 == 0:
                    log(f"[{done}/{len(files)}] ...")

                if consecutive_throttled >= args.give_up_after and not aborted:
                    aborted = True
                    log(
                        f"\nYouTube has been refusing {consecutive_throttled} requests in a row. "
                        "Stopping rather than burning the rest of the queue against a block. "
                        "Wait a few hours and re-run; finished sessions are skipped."
                    )
                    for pending in futures:
                        pending.cancel()

    log(
        f"\nDone. transcribed={counts['ok']} skipped={counts['skip']} "
        f"no-captions={counts['none']} errors={counts['error']}"
    )
    sys.exit(1 if counts["error"] else 0)


if __name__ == "__main__":
    main()
