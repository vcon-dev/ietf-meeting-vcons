#!/usr/bin/env python3
"""Transcribe the login-gated IETF audio era (IETF 90-94) on-device.

The audio-only recordings at `www.ietf.org/audio/...` moved behind IETF SSO in
2026: an anonymous request gets a Cloudflare 403 and an `auth.ietf.org` OIDC
redirect. Fetching them needs two things a plain client can't offer:

  1. a browser-grade TLS fingerprint, to pass Cloudflare (curl_cffi impersonate)
  2. a live authenticated session, to pass the OIDC gate (the user's own
     browser cookies)

So this reuses the operator's logged-in Chrome session — it never handles a
password. The user signs in and opens one audio URL in Chrome to complete the
`get.ietf.org` OIDC flow; this script reads the resulting cookies with
browser_cookie3 and presents them through a curl_cffi session. Cookies are
re-read on each download so Cloudflare clearance rotation (and OIDC refresh)
are picked up automatically while the browser session stays alive.

Transcription is identical to `scripts/transcribe_mi.py` (Apple `mi`, vendor
`apple`, product `speechanalyzer`); the two share `run_mi`/`to_wtf`. Idempotent
and resumable: sessions that already have a transcript are skipped.

Requires: `pip install curl_cffi browser_cookie3` (in the ietf2vcon venv).

Usage:
    python scripts/transcribe_audio_authed.py --meetings 90-93
    python scripts/transcribe_audio_authed.py --meetings 92 --limit 2 --keep-audio
"""
import argparse
import glob
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe_mi import run_mi, to_wtf  # noqa: E402

try:
    import browser_cookie3
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Install into the ietf2vcon venv: pip install curl_cffi browser_cookie3")
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
LOGIN_HOST = "auth.ietf.org"


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


def audio_dialog(vcon: dict) -> tuple[str, int] | None:
    for i, d in enumerate(vcon.get("dialog") or []):
        if d.get("mediatype") == "audio/mpeg" and d.get("url"):
            return d["url"], i
    return None


def has_transcript(vcon: dict) -> bool:
    return any(a.get("type") == "wtf_transcription" for a in (vcon.get("analysis") or []))


def _session():
    """A curl_cffi session carrying the current Chrome IETF cookies."""
    s = cffi_requests.Session(impersonate="chrome")
    for c in browser_cookie3.chrome():
        if "ietf.org" in c.domain:
            s.cookies.set(c.name, c.value, domain=c.domain, path=c.path or "/")
    return s


def download(url: str, dest: Path, attempts: int = 3) -> None:
    """Fetch a gated MP3, re-reading cookies on an auth bounce."""
    last = ""
    for attempt in range(1, attempts + 1):
        s = _session()  # fresh cookies each attempt (clearance rotates)
        try:
            r = s.get(url, allow_redirects=True, timeout=600)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * attempt)
            continue
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and "audio" in ctype and r.content:
            dest.write_bytes(r.content)
            return
        if LOGIN_HOST in r.url:
            raise RuntimeError(
                "redirected to IETF login — the browser session has expired. "
                "Re-open an audio URL in Chrome to refresh it, then resume."
            )
        last = f"status {r.status_code} ctype {ctype!r} len {len(r.content)}"
        time.sleep(2 * attempt)
    raise RuntimeError(f"after {attempts} attempts: {last}")


def transcribe_robust(audio: Path, locale: str, workdir: Path) -> dict | None:
    """Transcribe with `mi`, transcoding first if `mi` can't read the container.

    Some IETF audio in this era is Ogg Vorbis mislabelled with a .mp3 extension,
    which Apple's Speech framework rejects ("mi.CLIError error 1"). ffmpeg to
    16 kHz mono WAV makes it readable. Also used when a first pass returns an
    empty transcript, which is another symptom of the same mislabelling.
    """
    try:
        wtf = to_wtf(run_mi(audio, locale), locale)
        if wtf:
            return wtf
    except RuntimeError:
        wtf = None

    wav = workdir / (audio.stem + ".wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio), "-ac", "1", "-ar", "16000", str(wav)],
        capture_output=True, timeout=300,
    )
    if not wav.exists():
        raise RuntimeError("ffmpeg transcode failed for unreadable audio")
    try:
        return to_wtf(run_mi(wav, locale), locale)
    finally:
        wav.unlink(missing_ok=True)


def process(path: Path, workdir: Path, locale: str, keep_audio: bool):
    vcon = json.loads(path.read_text(encoding="utf-8"))
    if has_transcript(vcon):
        return "skip", "already has a transcript"
    found = audio_dialog(vcon)
    if not found:
        return "skip", "no audio recording"
    url, dialog_index = found

    audio = workdir / (path.stem.replace(".vcon", "") + ".mp3")
    download(url, audio)
    try:
        wtf = transcribe_robust(audio, locale, workdir)
    finally:
        if not keep_audio:
            audio.unlink(missing_ok=True)

    if not wtf:
        return "none", "empty transcript"

    vcon.setdefault("analysis", []).append({
        "type": "wtf_transcription",
        "dialog": dialog_index,
        "vendor": "apple",
        "product": "speechanalyzer",
        "encoding": "json",
        "body": json.dumps(wtf, ensure_ascii=False),
    })
    vcon["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "wtf_transcription" not in vcon.setdefault("extensions", []):
        vcon["extensions"].append("wtf_transcription")
    path.write_text(json.dumps(vcon, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "ok", f"{len(wtf['segments'])} segments"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings", required=True, help="e.g. 90-93")
    ap.add_argument("--locale", default="en_US")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--keep-audio", action="store_true")
    args = ap.parse_args()

    files: list[Path] = []
    for m in parse_meetings(args.meetings):
        files.extend(sorted((REPO / f"ietf{m}").glob("*.vcon.json")))
    if not files:
        print("No vCon files found")
        sys.exit(1)

    counts = {"ok": 0, "skip": 0, "none": 0, "error": 0}
    done = 0
    with tempfile.TemporaryDirectory(prefix="ietf-audio-") as tmp:
        workdir = Path(tmp)
        for f in files:
            if args.limit and counts["ok"] >= args.limit:
                break
            try:
                status, detail = process(f, workdir, args.locale, args.keep_audio)
            except Exception as e:
                status, detail = "error", str(e)
            counts[status] += 1
            done += 1
            if status in ("ok", "none", "error"):
                print(f"[{done}/{len(files)}] {status:5s} {f.parent.name}/{f.name}: {detail}", flush=True)
            # An expired session aborts the whole run; stop cleanly to be resumed.
            if status == "error" and "login" in detail:
                print("\nStopping: browser session expired. Refresh login and re-run.")
                break

    print(f"\ntranscribed={counts['ok']} skipped={counts['skip']} "
          f"no-audio={counts['none']} errors={counts['error']}")
    sys.exit(1 if counts["error"] else 0)


if __name__ == "__main__":
    main()
