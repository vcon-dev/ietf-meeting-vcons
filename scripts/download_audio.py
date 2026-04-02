#!/usr/bin/env python3
"""
Download audio for IETF meeting vCons in parallel.

Separates the download step from transcription so audio can be pre-fetched
while transcription runs, or re-used across multiple transcription runs.

Audio is saved as: audio/{meeting}/{vcon_stem}.mp3
  e.g. audio/ietf125/ietf125_6lo_35225.mp3

Usage:
    python scripts/download_audio.py --meeting 125
    python scripts/download_audio.py --meeting 125 --workers 8
    python scripts/download_audio.py --meeting 125 --group quic
    python scripts/download_audio.py --meeting 125 --dry-run
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is required. Install with: pip install yt-dlp")
    sys.exit(1)


def get_youtube_url(vcon_path: Path) -> str | None:
    with open(vcon_path) as f:
        vcon = json.load(f)
    for dialog in vcon.get("dialog", []):
        url = dialog.get("url", "")
        if "youtube.com" in url or "youtu.be" in url:
            return url
    return None


def audio_dest(vcon_path: Path, audio_dir: Path) -> Path:
    """Return the target MP3 path for a given vCon file."""
    return audio_dir / f"{vcon_path.stem}.mp3"


def already_downloaded(vcon_path: Path, audio_dir: Path) -> bool:
    return audio_dest(vcon_path, audio_dir).exists()


def download_audio(vcon_path: Path, audio_dir: Path,
                   cookies_from_browser: str | None = None) -> tuple[Path, bool, str]:
    """Download audio for one vCon. Returns (vcon_path, success, message)."""
    youtube_url = get_youtube_url(vcon_path)
    if not youtube_url:
        return vcon_path, False, "no YouTube URL"

    dest = audio_dest(vcon_path, audio_dir)
    if dest.exists():
        return vcon_path, True, f"already exists: {dest.name}"

    # yt-dlp: download best audio, convert to mono 16kHz MP3
    # Use the vCon stem as the output filename so it's easy to match back
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
        'postprocessor_args': ['-ac', '1', '-ar', '16000'],
        'outtmpl': str(audio_dir / f"{vcon_path.stem}.%(ext)s"),
        'quiet': True,
        'no_warnings': True,
    }
    if cookies_from_browser:
        ydl_opts['cookiesfrombrowser'] = (cookies_from_browser,)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])
        if dest.exists():
            size_mb = dest.stat().st_size / 1_048_576
            return vcon_path, True, f"downloaded {size_mb:.1f} MB → {dest.name}"
        else:
            return vcon_path, False, "file missing after download"
    except Exception as e:
        return vcon_path, False, str(e)


def find_vcons(meeting: int, group: str | None, base_dir: Path) -> list[Path]:
    meeting_dir = base_dir / f"ietf{meeting}"
    if not meeting_dir.exists():
        return []
    pattern = f"ietf{meeting}_{group}_*.vcon.json" if group else "*.vcon.json"
    return sorted(meeting_dir.glob(pattern))


def main():
    parser = argparse.ArgumentParser(
        description="Pre-download audio for IETF meeting vCons"
    )
    parser.add_argument("--meeting", type=int, required=True,
                        help="IETF meeting number (e.g. 125)")
    parser.add_argument("--group",
                        help="Only download a specific working group")
    parser.add_argument("--audio-dir", type=Path,
                        help="Where to store MP3s (default: audio/ietf{meeting}/)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel downloads (default: 4)")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help="Browser to pull YouTube cookies from (chrome, firefox, safari)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List files that would be downloaded without doing it")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    audio_dir = args.audio_dir or (base_dir / "audio" / f"ietf{args.meeting}")
    audio_dir.mkdir(parents=True, exist_ok=True)

    vcons = find_vcons(args.meeting, args.group, base_dir)
    if not vcons:
        print(f"No vCon files found for IETF {args.meeting}")
        sys.exit(1)

    pending = [v for v in vcons if not already_downloaded(v, audio_dir)]
    already_done = len(vcons) - len(pending)

    print(f"IETF {args.meeting}: {len(vcons)} sessions, "
          f"{already_done} already downloaded, {len(pending)} to fetch")
    print(f"Audio dir: {audio_dir}")

    if not pending:
        print("Nothing to do.")
        return

    if args.dry_run:
        for v in pending:
            print(f"  {v.name}")
        return

    success, skipped, errors = 0, 0, []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_audio, v, audio_dir, args.cookies_from_browser): v
            for v in pending
        }
        for future in as_completed(futures):
            vcon_path, ok, msg = future.result()
            name = vcon_path.stem
            if ok:
                if "already exists" in msg:
                    skipped += 1
                else:
                    success += 1
                    print(f"  ✓ {name}  ({msg})")
            else:
                errors.append((name, msg))
                print(f"  ✗ {name}  ({msg})", file=sys.stderr)

    print(f"\nDone: {success} downloaded, {skipped} skipped, {len(errors)} errors")
    if errors:
        print("\nErrors:")
        for name, msg in errors:
            print(f"  {name}: {msg}")


if __name__ == "__main__":
    main()
