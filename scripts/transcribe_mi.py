#!/usr/bin/env python3
"""Transcribe caption-less IETF sessions on-device with Apple's `mi` CLI.

Some sessions have a YouTube recording but no captions (IETF 95-98 predate the
IETF channel's auto-captioning; audio-era meetings have none at all). YouTube
therefore yields nothing to fetch, and the audio has to be transcribed.

`mi transcribe` (Apple's on-device Speech framework, macOS 26+) runs on the
Neural Engine: ~90x real time, a few hundred MB of memory, no model download,
no cloud call. It accepts compressed audio directly, so the pipeline is just
download → transcribe. See `mi doctor` for locale support.

For each target session this writes a WTF transcription analysis entry inline,
matching the shape `scripts/whisper_transcribe.py` produces, with
`vendor: "apple"`, `product: "speechanalyzer"`. Externalise afterwards with
`scripts/externalize_transcripts.py`.

Unlike Whisper, `mi` reports segment start/end and text but no per-word timing
and no confidence, so those fields are omitted rather than fabricated. The WTF
extension makes them optional.

Idempotent and resumable: a session that already has any wtf_transcription
entry is skipped, so re-running only fills gaps.

Usage:
    python scripts/transcribe_mi.py --meetings 95-98
    python scripts/transcribe_mi.py --meetings 95 --limit 3 --keep-audio
    python scripts/transcribe_mi.py --meetings 95-98 --dry-run
"""
import argparse
import glob
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VIDEO_ID = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})")

# Prefer the small 48k m4a audio track, falling back to any m4a / best audio.
YT_FORMAT = "139/bestaudio[ext=m4a]/bestaudio"


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


def has_transcript(vcon: dict) -> bool:
    return any(a.get("type") == "wtf_transcription" for a in (vcon.get("analysis") or []))


def youtube_dialog(vcon: dict) -> tuple[str, int] | None:
    for i, d in enumerate(vcon.get("dialog") or []):
        url = d.get("url") or ""
        if VIDEO_ID.search(url):
            return url, i
    return None


def download_audio(video_id: str, workdir: Path, attempts: int = 3) -> Path | None:
    """Download the audio track, retrying transient failures.

    YouTube's JS "n-sig" challenge intermittently fails to solve, and when it
    does yt-dlp reports "Requested format is not available" for a format that
    actually exists. A retry almost always clears it.
    """
    out = workdir / f"{video_id}.m4a"
    last = ""
    for attempt in range(1, attempts + 1):
        r = subprocess.run(
            ["yt-dlp", "--no-warnings", "-f", YT_FORMAT, "-o", str(out),
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=600,
        )
        for cand in sorted(workdir.glob(f"{video_id}.*")):
            return cand
        last = (r.stderr.strip().splitlines() or ["yt-dlp failed"])[-1][:200]
        if attempt < attempts:
            time.sleep(2 * attempt)
    raise RuntimeError(f"after {attempts} attempts: {last}")


def run_mi(audio: Path, locale: str) -> dict:
    r = subprocess.run(
        ["mi", "transcribe", str(audio), "--json", "--locale", locale],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["mi failed"])[-1][:200])
    return json.loads(r.stdout)


def to_wtf(mi_out: dict, locale: str) -> dict | None:
    raw = mi_out.get("segments") or []
    segments = []
    for i, s in enumerate(raw):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        # mi reports start/end in seconds; no per-word timing, no confidence.
        segments.append({
            "id": len(segments),
            "start": round(float(s.get("start", 0.0)), 3),
            "end": round(float(s.get("end", 0.0)), 3),
            "text": text,
        })
    if not segments:
        return None

    now = datetime.now(timezone.utc).isoformat()
    text = mi_out.get("text") or " ".join(s["text"] for s in segments)
    return {
        "transcript": {
            "text": text,
            "language": locale.split("_")[0],
            "duration": segments[-1]["end"],
            # mi provides no confidence score; omitted rather than fabricated.
        },
        "segments": segments,
        "metadata": {
            "created_at": now,
            "processed_at": now,
            "provider": "apple",
            "model": "speechanalyzer",
            "locale": locale,
        },
    }


def process(path: Path, workdir: Path, locale: str, dry_run: bool, keep_audio: bool):
    """Returns (status, detail): ok / skip / none / error."""
    vcon = json.loads(path.read_text(encoding="utf-8"))
    if has_transcript(vcon):
        return "skip", "already has a transcript"

    found = youtube_dialog(vcon)
    if not found:
        return "skip", "no YouTube recording"
    url, dialog_index = found
    video_id = VIDEO_ID.search(url).group(1)

    if dry_run:
        return "ok", f"would transcribe {video_id}"

    audio = download_audio(video_id, workdir)
    if not audio:
        return "none", f"{video_id}: audio unavailable"
    try:
        wtf = to_wtf(run_mi(audio, locale), locale)
    finally:
        if not keep_audio:
            for f in workdir.glob(f"{video_id}.*"):
                f.unlink(missing_ok=True)

    if not wtf:
        return "none", f"{video_id}: empty transcript"

    entry = {
        "type": "wtf_transcription",
        "dialog": dialog_index,
        "vendor": "apple",
        "product": "speechanalyzer",
        "encoding": "json",
        "body": json.dumps(wtf, ensure_ascii=False),
    }
    vcon.setdefault("analysis", []).append(entry)
    vcon["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "wtf_transcription" not in vcon.setdefault("extensions", []):
        vcon["extensions"].append("wtf_transcription")

    path.write_text(json.dumps(vcon, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "ok", f"{len(wtf['segments'])} segments"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings", required=True, help="e.g. 95-98")
    ap.add_argument("--locale", default="en_US")
    ap.add_argument("--limit", type=int, help="stop after N transcriptions (for testing)")
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files: list[Path] = []
    for m in parse_meetings(args.meetings):
        files.extend(sorted((REPO / f"ietf{m}").glob("*.vcon.json")))
    if not files:
        print("No vCon files found")
        sys.exit(1)

    counts = {"ok": 0, "skip": 0, "none": 0, "error": 0}
    done = 0
    with tempfile.TemporaryDirectory(prefix="mi-audio-") as tmp:
        workdir = Path(tmp)
        for f in files:
            if args.limit and counts["ok"] >= args.limit:
                break
            try:
                status, detail = process(f, workdir, args.locale, args.dry_run, args.keep_audio)
            except Exception as e:
                status, detail = "error", str(e)
            counts[status] += 1
            done += 1
            if status in ("ok", "none", "error"):
                print(f"[{done}/{len(files)}] {status:5s} {f.parent.name}/{f.name}: {detail}", flush=True)

    print(f"\ntranscribed={counts['ok']} skipped={counts['skip']} "
          f"no-audio={counts['none']} errors={counts['error']}")
    sys.exit(1 if counts["error"] else 0)


if __name__ == "__main__":
    main()
