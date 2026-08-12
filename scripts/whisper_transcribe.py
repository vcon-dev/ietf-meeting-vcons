#!/usr/bin/env python3
"""
IETF Meeting vCon Whisper Transcription Tool

This script transcribes IETF meeting audio using local OpenAI Whisper (via
faster-whisper) and stores the results in WTF (World Transcription Format)
as specified in draft-howe-vcon-wtf-extension.

Runs entirely offline once audio is downloaded — no API key required.

Usage:
    python whisper_transcribe.py <vcon_file> [--model large-v3]
    python whisper_transcribe.py --meeting 125 [--group quic] [--model medium]
    python whisper_transcribe.py --all-pending [--model large-v3]
    python whisper_transcribe.py --meeting 125 --dry-run

Requirements:
    pip install faster-whisper yt-dlp
    # FFmpeg must be installed: brew install ffmpeg
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is required. Install with: pip install yt-dlp")
    sys.exit(1)

try:
    import mlx_whisper
    from mlx_whisper.transcribe import ModelHolder as _MlxModelHolder
    import mlx.core as _mx
except ImportError:
    mlx_whisper = None
    _MlxModelHolder = None
    _mx = None
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Error: mlx-whisper or faster-whisper is required.")
        print("  Apple Silicon: pip install mlx-whisper")
        print("  Other:         pip install faster-whisper")
        sys.exit(1)


def preload_mlx_model(model_size: str = "large-v3"):
    """Pre-load the MLX Whisper model into GPU memory before the transcription loop.

    Calling this once means ModelHolder.get_model() is a no-op for every
    subsequent transcribe() call — the model stays resident in Metal memory
    for the entire batch.
    """
    if mlx_whisper is None or _MlxModelHolder is None:
        return
    mlx_model_map = {
        "tiny":     "mlx-community/whisper-tiny-mlx",
        "base":     "mlx-community/whisper-base-mlx",
        "small":    "mlx-community/whisper-small-mlx",
        "medium":   "mlx-community/whisper-medium-mlx",
        "large-v2": "mlx-community/whisper-large-v2-mlx",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
    }
    model_path = mlx_model_map.get(model_size, f"mlx-community/whisper-{model_size}-mlx")
    print(f"Pre-loading MLX model {model_path} into GPU memory...")
    import mlx.core as mx
    # Must use float16 — mlx_whisper.transcribe() defaults to fp16=True,
    # so ModelHolder caches the model as float16. If we pre-load as float32,
    # ModelHolder stores a separate entry and transcribe() loads a new float16
    # model, causing "audio_features has an incorrect dtype: mlx.core.float32".
    _MlxModelHolder.get_model(model_path, mx.float16)
    print("  Model loaded — will be reused for all files.")


def download_youtube_audio(
    youtube_url: str,
    output_path: str,
    cookies_from_browser: Optional[str] = None
) -> str:
    """Download audio from YouTube video.

    Downloads as MP3 at 16kHz mono — optimal for speech recognition.

    Args:
        youtube_url: YouTube video URL
        output_path: Directory to save the audio file
        cookies_from_browser: Browser to extract cookies from (e.g., 'chrome', 'firefox')

    Returns:
        Path to the downloaded audio file
    """
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
        'postprocessor_args': [
            '-ac', '1',      # mono
            '-ar', '16000',  # 16kHz sample rate
        ],
        'outtmpl': os.path.join(output_path, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }

    if cookies_from_browser:
        ydl_opts['cookiesfrombrowser'] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        video_id = info['id']
        audio_path = os.path.join(output_path, f"{video_id}.mp3")
        return audio_path


def transcribe_with_whisper(
    audio_path: str,
    model_size: str = "large-v3",
    language: Optional[str] = None,
    device: str = "auto"
) -> tuple:
    """Transcribe audio using mlx-whisper (Apple Silicon) or faster-whisper fallback.

    Args:
        audio_path: Path to audio file
        model_size: Whisper model size (tiny, base, small, medium, large-v3)
        language: BCP-47 language code, or None for auto-detection
        device: ignored when using mlx-whisper (always uses MPS); used by faster-whisper fallback

    Returns:
        Tuple of (segments list, info object)
    """
    if mlx_whisper is not None:
        # mlx-whisper uses HuggingFace model IDs
        mlx_model_map = {
            "tiny":     "mlx-community/whisper-tiny-mlx",
            "base":     "mlx-community/whisper-base-mlx",
            "small":    "mlx-community/whisper-small-mlx",
            "medium":   "mlx-community/whisper-medium-mlx",
            "large-v2": "mlx-community/whisper-large-v2-mlx",
            "large-v3": "mlx-community/whisper-large-v3-mlx",
        }
        model_path = mlx_model_map.get(model_size, f"mlx-community/whisper-{model_size}-mlx")

        kwargs = {"word_timestamps": False, "verbose": False}
        if language:
            kwargs["language"] = language

        result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=model_path, **kwargs)

        # Flush any pending Metal compute before next file
        if _mx is not None:
            _mx.eval()

        # Normalise to the same (segments, info) shape the rest of the script expects
        class _Info:
            pass

        info = _Info()
        info.language = result.get("language", "en")
        info.duration = result.get("duration", 0.0)

        class _Word:
            def __init__(self, w):
                self.word = w.get("word", "")
                self.start = w.get("start", 0.0)
                self.end = w.get("end", 0.0)
                self.probability = w.get("probability", 1.0)

        class _Segment:
            def __init__(self, s):
                self.start = s.get("start", 0.0)
                self.end = s.get("end", 0.0)
                self.text = s.get("text", "")
                self.avg_logprob = s.get("avg_logprob", -0.5)
                self.words = [_Word(w) for w in s.get("words", [])]

        segments = [_Segment(s) for s in result.get("segments", [])]
        return segments, info

    else:
        # faster-whisper fallback (non-Apple-Silicon)
        if device == "auto":
            try:
                import torch
                if torch.cuda.is_available():
                    device, compute_type = "cuda", "float16"
                else:
                    device, compute_type = "cpu", "int8"
            except ImportError:
                device, compute_type = "cpu", "int8"
        elif device == "cuda":
            compute_type = "float16"
        else:
            compute_type = "int8"

        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
            beam_size=5,
            vad_filter=True,
        )
        return list(segments), info


def transcript_to_wtf(
    segments: list,
    info,
    audio_duration: float,
    model_size: str = "large-v3",
) -> dict:
    """Convert faster-whisper output to WTF (World Transcription Format).

    Follows draft-howe-vcon-wtf-extension specification.
    Includes word-level timestamps and per-segment confidence scores.

    Args:
        segments: List of faster-whisper Segment objects
        info: TranscriptionInfo object from faster-whisper
        audio_duration: Duration of audio in seconds (from vCon dialog)
        model_size: Whisper model name used

    Returns:
        WTF-formatted transcription object
    """
    now = datetime.now(timezone.utc).isoformat()

    # Build full transcript text
    full_text = " ".join(seg.text.strip() for seg in segments)

    # Convert segments to WTF format
    wtf_segments = []
    confidence_scores = []

    for i, seg in enumerate(segments):
        # avg_logprob is negative log probability; convert to 0-1 range
        # logprob of 0 = probability 1.0, logprob of -inf = probability 0.0
        # Clamp to reasonable range (-1.0 to 0.0)
        raw_logprob = getattr(seg, 'avg_logprob', -0.5)
        confidence = min(1.0, max(0.0, 1.0 + raw_logprob))  # roughly maps -1.0..0.0 → 0.0..1.0
        confidence_scores.append(confidence)

        # Word-level data
        words = []
        if hasattr(seg, 'words') and seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word,
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 4),
                })

        wtf_seg = {
            "id": i,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "confidence": round(confidence, 4),
        }
        if words:
            wtf_seg["words"] = words

        wtf_segments.append(wtf_seg)

    avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0
    detected_language = getattr(info, 'language', 'en')
    duration = getattr(info, 'duration', audio_duration) or audio_duration

    wtf = {
        "transcript": {
            "text": full_text,
            "language": detected_language,
            "duration": round(duration, 3),
            "confidence": round(avg_confidence, 4),
        },
        "segments": wtf_segments,
        "metadata": {
            "created_at": now,
            "processed_at": now,
            "provider": "mlx-whisper" if mlx_whisper is not None else "whisper",
            "model": model_size,
            "audio": {
                "duration": round(duration, 3),
            },
            "options": {
                "language": detected_language,
                "word_timestamps": True,
                "vad_filter": True,
            },
        },
        "quality": {
            "average_confidence": round(avg_confidence, 4),
        },
    }

    return wtf


def update_vcon_with_transcription(vcon_path: str, wtf_transcription: dict) -> None:
    """Update a vCon file with WTF transcription from Whisper.

    Args:
        vcon_path: Path to the vCon JSON file
        wtf_transcription: WTF-formatted transcription data
    """
    with open(vcon_path, 'r', encoding='utf-8') as f:
        vcon = json.load(f)

    # Remove any existing Whisper transcription (keep YouTube/Speechmatics)
    if "analysis" in vcon:
        vcon["analysis"] = [
            a for a in vcon["analysis"]
            if not (a.get("type") == "wtf_transcription" and a.get("vendor") in ("whisper", "mlx-whisper"))
        ]
    else:
        vcon["analysis"] = []

    analysis_entry = {
        "type": "wtf_transcription",
        "dialog": 0,
        "vendor": "whisper",
        "encoding": "json",
        "body": wtf_transcription,
    }

    vcon["analysis"].append(analysis_entry)
    vcon["updated_at"] = datetime.now(timezone.utc).isoformat()

    if "extensions" not in vcon:
        vcon["extensions"] = []
    if "wtf_transcription" not in vcon.get("extensions", []):
        vcon["extensions"].append("wtf_transcription")

    with open(vcon_path, 'w', encoding='utf-8') as f:
        json.dump(vcon, f, indent=2, ensure_ascii=False)


def transcribe_vcon(
    vcon_path: str,
    model_size: str = "large-v3",
    language: Optional[str] = None,
    force: bool = False,
    cookies_from_browser: Optional[str] = None,
) -> bool:
    """Transcribe the audio from a vCon file using local Whisper.

    Args:
        vcon_path: Path to the vCon JSON file
        model_size: Whisper model size
        language: Language code (None = auto-detect)
        force: Re-transcribe even if already done
        cookies_from_browser: Browser to extract YouTube cookies from

    Returns:
        True if transcription was performed, False otherwise
    """
    print(f"Processing: {vcon_path}")

    with open(vcon_path, 'r', encoding='utf-8') as f:
        vcon = json.load(f)

    # Check if already has Whisper transcription
    if not force:
        for analysis in vcon.get("analysis", []):
            if (analysis.get("type") == "wtf_transcription" and
                    analysis.get("vendor") == "whisper"):
                print(f"  Already has Whisper transcription, skipping.")
                return False

    # Get YouTube URL from dialog
    youtube_url = None
    duration = 0
    for dialog in vcon.get("dialog", []):
        if dialog.get("type") == "video":
            url = dialog.get("url", "")
            if "youtube.com" in url or "youtu.be" in url:
                youtube_url = url
                duration = dialog.get("duration", 0)
                break

    if not youtube_url:
        print(f"  No YouTube URL found in dialog, skipping.")
        return False

    print(f"  YouTube URL: {youtube_url}")

    # Check for pre-downloaded audio (from download_audio.py)
    base_dir = Path(vcon_path).parent.parent
    meeting_match = Path(vcon_path).stem.split("_")[0]  # e.g. "ietf125"
    predownload_path = base_dir / "audio" / meeting_match / f"{Path(vcon_path).stem}.mp3"

    _tmpdir_obj = None
    if predownload_path.exists():
        print(f"  Using pre-downloaded audio: {predownload_path.name}")
        audio_path = str(predownload_path)
        tmpdir = None
    else:
        import tempfile as _tempfile
        _tmpdir_obj = _tempfile.TemporaryDirectory()
        tmpdir = _tmpdir_obj.name
        print(f"  Downloading audio...")
        try:
            audio_path = download_youtube_audio(youtube_url, tmpdir, cookies_from_browser)
        except Exception as e:
            print(f"  Error downloading audio: {e}")
            _tmpdir_obj.cleanup()
            return False

    try:
        print(f"  Transcribing with Whisper ({model_size})...")

        try:
            segments, info = transcribe_with_whisper(audio_path, model_size, language)
        except Exception as e:
            print(f"  Error transcribing: {e}")
            return False

        print(f"  Converting to WTF format ({len(segments)} segments)...")
        wtf = transcript_to_wtf(segments, info, duration, model_size)

        print(f"  Updating vCon file...")
        update_vcon_with_transcription(vcon_path, wtf)

        lang = wtf["transcript"]["language"]
        conf = wtf["quality"]["average_confidence"]
        print(f"  Done! Language: {lang}, Confidence: {conf:.3f}")
        return True

    finally:
        if _tmpdir_obj is not None:
            _tmpdir_obj.cleanup()


def find_vcons_for_meeting(meeting: int, group: Optional[str] = None) -> list:
    """Find vCon files for a specific IETF meeting."""
    base_dir = Path(__file__).parent.parent
    meeting_dir = base_dir / f"ietf{meeting}"

    if not meeting_dir.exists():
        return []

    pattern = f"ietf{meeting}_{group}_*.vcon.json" if group else "*.vcon.json"
    return sorted(meeting_dir.glob(pattern))


def find_pending_vcons() -> list:
    """Find all vCons that don't have a Whisper transcription."""
    base_dir = Path(__file__).parent.parent
    pending = []

    for meeting_dir in sorted(base_dir.glob("ietf*")):
        if not meeting_dir.is_dir():
            continue

        for vcon_path in sorted(meeting_dir.glob("*.vcon.json")):
            with open(vcon_path, 'r', encoding='utf-8') as f:
                vcon = json.load(f)

            # Only process vCons that have a YouTube recording
            has_youtube = any(
                "youtube.com" in d.get("url", "") or "youtu.be" in d.get("url", "")
                for d in vcon.get("dialog", [])
                if d.get("type") == "video"
            )
            if not has_youtube:
                continue

            has_whisper = any(
                a.get("type") == "wtf_transcription" and a.get("vendor") in ("whisper", "mlx-whisper")
                for a in vcon.get("analysis", [])
            )
            if not has_whisper:
                pending.append(vcon_path)

    return pending


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe IETF meeting vCons using local Whisper"
    )
    parser.add_argument(
        "vcon_file",
        nargs="?",
        help="Path to a specific vCon file to transcribe"
    )
    parser.add_argument(
        "--meeting",
        type=int,
        help="IETF meeting number to transcribe"
    )
    parser.add_argument(
        "--group",
        help="Working group acronym (used with --meeting)"
    )
    parser.add_argument(
        "--all-pending", "--all",
        action="store_true",
        dest="all_pending",
        help="Transcribe all vCons missing Whisper transcription"
    )
    parser.add_argument(
        "--model",
        default="large-v3",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper model size (default: large-v3)"
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language code (default: auto-detect)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-transcribe even if already done"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be transcribed without actually doing it"
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device (default: auto)"
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="Browser to extract YouTube cookies from (e.g., chrome, firefox, safari)"
    )

    args = parser.parse_args()

    # Determine which files to process
    vcon_files = []

    if args.vcon_file:
        vcon_files = [Path(args.vcon_file)]
    elif args.meeting:
        vcon_files = find_vcons_for_meeting(args.meeting, args.group)
        if not vcon_files:
            print(f"No vCon files found for IETF {args.meeting}")
            sys.exit(1)
    elif args.all_pending:
        vcon_files = find_pending_vcons()
        if not vcon_files:
            print("No pending vCons found (all have Whisper transcription)")
            sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)

    print(f"Found {len(vcon_files)} vCon file(s) to process")

    if args.dry_run:
        for f in vcon_files:
            print(f"  {f}")
        sys.exit(0)

    # Pre-load model once into GPU memory before the loop
    preload_mlx_model(args.model)

    success_count = 0
    errors = []

    for vcon_path in vcon_files:
        try:
            result = transcribe_vcon(
                str(vcon_path),
                model_size=args.model,
                language=args.language,
                force=args.force,
                cookies_from_browser=args.cookies_from_browser,
            )
            if result:
                success_count += 1
        except Exception as e:
            errors.append((str(vcon_path), str(e)))
            print(f"  Error: {e}")

    if errors:
        print(f"\nErrors encountered:")
        for path, err in errors[:10]:
            print(f"  {path}: {err}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more errors")

    print(f"\nCompleted: {success_count}/{len(vcon_files)} files transcribed")


if __name__ == "__main__":
    main()
