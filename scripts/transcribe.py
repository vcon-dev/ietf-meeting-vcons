#!/usr/bin/env python3
"""
IETF Meeting vCon Transcription Tool

This script transcribes IETF meeting audio using Speechmatics and stores
the results in WTF (World Transcription Format) as specified in
draft-howe-vcon-wtf-extension.

Usage:
    python transcribe.py <vcon_file> [--api-key KEY] [--language LANG]
    python transcribe.py --meeting 121 [--group 6lo] [--api-key KEY]
    python transcribe.py --all-pending [--api-key KEY]

Requirements:
    pip install speechmatics-batch yt-dlp

Environment:
    SPEECHMATICS_API_KEY - Your Speechmatics API key
"""

import argparse
import asyncio
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
    from speechmatics.batch import (
        AsyncClient,
        TranscriptionConfig,
        FormatType,
    )
except ImportError:
    print("Error: speechmatics-batch is required. Install with: pip install speechmatics-batch")
    sys.exit(1)


# Speechmatics batch API URL
SPEECHMATICS_API_URL = "https://asr.api.speechmatics.com/v2"


def get_api_key(args_key: Optional[str] = None) -> str:
    """Get Speechmatics API key from args or environment."""
    key = args_key or os.environ.get("SPEECHMATICS_API_KEY")
    if not key:
        raise ValueError(
            "Speechmatics API key required. Set SPEECHMATICS_API_KEY environment "
            "variable or use --api-key argument."
        )
    return key


def download_youtube_audio(youtube_url: str, output_path: str) -> str:
    """Download audio from YouTube video.

    Downloads as MP3 to keep file size manageable for the Speechmatics API.
    Uses mono audio at 16kHz sample rate which is optimal for speech recognition.

    Args:
        youtube_url: YouTube video URL
        output_path: Directory to save the audio file

    Returns:
        Path to the downloaded audio file
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',  # 64kbps is sufficient for speech
        }],
        # Convert to mono 16kHz for optimal speech recognition
        'postprocessor_args': [
            '-ac', '1',      # mono
            '-ar', '16000',  # 16kHz sample rate
        ],
        'outtmpl': os.path.join(output_path, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        video_id = info['id']
        audio_path = os.path.join(output_path, f"{video_id}.mp3")
        return audio_path


async def transcribe_with_speechmatics(
    audio_path: str,
    api_key: str,
    language: str = "en"
) -> dict:
    """Transcribe audio using the speechmatics-batch SDK.

    Args:
        audio_path: Path to audio file
        api_key: Speechmatics API key
        language: Language code (default: en)

    Returns:
        Speechmatics transcription result as dict with full details
    """
    async with AsyncClient(api_key=api_key, url=SPEECHMATICS_API_URL) as client:
        config = TranscriptionConfig(
            language=language,
            diarization="speaker",
            enable_entities=True,
        )

        # Submit job and wait
        job = await client.submit_job(audio_path, transcription_config=config)
        print(f"  Submitted job: {job.id}")

        await client.wait_for_completion(job.id)

        # Get transcript as Transcript object
        transcript = await client.get_transcript(job.id, format_type=FormatType.JSON)

        return {
            "transcript_text": transcript.transcript_text,
            "confidence": transcript.confidence,
            "job_id": job.id,
        }


def transcript_to_wtf(
    transcript_text: str,
    confidence: Optional[float],
    audio_duration: float,
    language: str = "en",
    job_id: Optional[str] = None
) -> dict:
    """Convert Speechmatics Transcript to WTF (World Transcription Format).

    This follows the draft-howe-vcon-wtf-extension specification.

    Note: The speechmatics-batch SDK returns a simplified Transcript object.
    For full word-level timestamps, we need to use the raw API.

    Args:
        transcript_text: Full transcript text
        confidence: Overall confidence score
        audio_duration: Duration of audio in seconds
        language: BCP-47 language code
        job_id: Speechmatics job ID

    Returns:
        WTF-formatted transcription object
    """
    now = datetime.now(timezone.utc).isoformat()

    # Build basic WTF structure per draft-howe-vcon-wtf-extension
    # The SDK doesn't give us word-level timestamps, so we create segments
    # from the full text (sentence-based)

    # Split into sentences for segments
    import re
    sentences = re.split(r'(?<=[.!?])\s+', transcript_text.strip())

    segments = []
    for i, sentence in enumerate(sentences):
        if sentence.strip():
            segments.append({
                "id": i,
                "start": 0,  # No timing info available from SDK
                "end": 0,
                "text": sentence.strip(),
                "confidence": confidence,
            })

    wtf = {
        "transcript": {
            "text": transcript_text,
            "language": language,
            "duration": audio_duration,
            "confidence": confidence
        },
        "segments": segments,
        "metadata": {
            "created_at": now,
            "processed_at": now,
            "provider": "speechmatics",
            "model": "enhanced",
            "audio": {
                "duration": audio_duration
            },
            "options": {
                "language": language,
                "diarization": "speaker"
            }
        },
        "extensions": {
            "speechmatics": {
                "job_id": job_id,
            }
        }
    }

    # Add quality metrics
    if confidence is not None:
        wtf["quality"] = {
            "average_confidence": confidence,
        }

    return wtf


def update_vcon_with_transcription(vcon_path: str, wtf_transcription: dict) -> None:
    """Update a vCon file with WTF transcription.

    Args:
        vcon_path: Path to the vCon JSON file
        wtf_transcription: WTF-formatted transcription data
    """
    with open(vcon_path, 'r', encoding='utf-8') as f:
        vcon = json.load(f)

    # Remove existing Speechmatics transcription if present (keep YouTube)
    if "analysis" in vcon:
        vcon["analysis"] = [
            a for a in vcon["analysis"]
            if not (a.get("type") == "wtf_transcription" and a.get("vendor") == "speechmatics")
        ]
    else:
        vcon["analysis"] = []

    # Add the new Speechmatics transcription
    analysis_entry = {
        "type": "wtf_transcription",
        "dialog": 0,
        "vendor": "speechmatics",
        "spec": "draft-howe-wtf-transcription-00",
        "body": wtf_transcription
    }

    vcon["analysis"].append(analysis_entry)

    # Update the vcon timestamp
    vcon["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Add wtf_transcription to extensions if not present
    if "extensions" not in vcon:
        vcon["extensions"] = []
    if "wtf_transcription" not in vcon.get("extensions", []):
        vcon["extensions"].append("wtf_transcription")

    # Write back
    with open(vcon_path, 'w', encoding='utf-8') as f:
        json.dump(vcon, f, indent=2, ensure_ascii=False)


async def transcribe_vcon(
    vcon_path: str,
    api_key: str,
    language: str = "en",
    force: bool = False
) -> bool:
    """Transcribe the audio from a vCon file using Speechmatics.

    Args:
        vcon_path: Path to the vCon JSON file
        api_key: Speechmatics API key
        language: Language code
        force: Re-transcribe even if already done

    Returns:
        True if transcription was performed, False otherwise
    """
    print(f"Processing: {vcon_path}")

    # Load vCon
    with open(vcon_path, 'r', encoding='utf-8') as f:
        vcon = json.load(f)

    # Check if already has Speechmatics transcription
    if not force:
        for analysis in vcon.get("analysis", []):
            if (analysis.get("type") == "wtf_transcription" and
                analysis.get("vendor") == "speechmatics"):
                print("  Already has Speechmatics transcription, skipping.")
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
        print("  No YouTube URL found in dialog, skipping.")
        return False

    print(f"  YouTube URL: {youtube_url}")

    # Download audio
    with tempfile.TemporaryDirectory() as tmpdir:
        print("  Downloading audio...")
        try:
            audio_path = download_youtube_audio(youtube_url, tmpdir)
        except Exception as e:
            print(f"  Error downloading audio: {e}")
            return False

        print(f"  Downloaded: {audio_path}")

        # Transcribe
        print("  Transcribing with Speechmatics...")
        try:
            result = await transcribe_with_speechmatics(audio_path, api_key, language)
        except Exception as e:
            print(f"  Error transcribing: {e}")
            return False

        # Convert to WTF
        print("  Converting to WTF format...")
        wtf = transcript_to_wtf(
            transcript_text=result["transcript_text"],
            confidence=result.get("confidence"),
            audio_duration=duration,
            language=language,
            job_id=result.get("job_id"),
        )

        # Update vCon
        print("  Updating vCon file...")
        update_vcon_with_transcription(vcon_path, wtf)

        print("  Done!")
        return True


def find_vcons_for_meeting(meeting: int, group: Optional[str] = None) -> list:
    """Find vCon files for a specific IETF meeting.

    Args:
        meeting: IETF meeting number
        group: Optional working group acronym

    Returns:
        List of vCon file paths
    """
    base_dir = Path(__file__).parent.parent
    meeting_dir = base_dir / f"ietf{meeting}"

    if not meeting_dir.exists():
        return []

    pattern = f"ietf{meeting}_{group}_*.vcon.json" if group else "*.vcon.json"
    return list(meeting_dir.glob(pattern))


def find_pending_vcons() -> list:
    """Find all vCons that don't have Speechmatics transcription.

    Returns:
        List of vCon file paths needing transcription
    """
    base_dir = Path(__file__).parent.parent
    pending = []

    for meeting_dir in sorted(base_dir.glob("ietf*")):
        if not meeting_dir.is_dir():
            continue

        for vcon_path in meeting_dir.glob("*.vcon.json"):
            with open(vcon_path, 'r', encoding='utf-8') as f:
                vcon = json.load(f)

            # Check for YouTube URL
            has_youtube = any(
                "youtube.com" in d.get("url", "") or "youtu.be" in d.get("url", "")
                for d in vcon.get("dialog", [])
                if d.get("type") == "video"
            )

            if not has_youtube:
                continue

            # Check for existing Speechmatics transcription
            has_speechmatics = any(
                a.get("type") == "wtf_transcription" and a.get("vendor") == "speechmatics"
                for a in vcon.get("analysis", [])
            )

            if not has_speechmatics:
                pending.append(vcon_path)

    return pending


async def main():
    parser = argparse.ArgumentParser(
        description="Transcribe IETF meeting vCons using Speechmatics"
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
        "--all-pending",
        action="store_true",
        help="Transcribe all vCons missing Speechmatics transcription"
    )
    parser.add_argument(
        "--api-key",
        help="Speechmatics API key (or set SPEECHMATICS_API_KEY env var)"
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code (default: en)"
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
            print("No pending vCons found (all have Speechmatics transcription)")
            sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)

    print(f"Found {len(vcon_files)} vCon file(s) to process")

    if args.dry_run:
        for f in vcon_files:
            print(f"  {f}")
        sys.exit(0)

    # Get API key
    try:
        api_key = get_api_key(args.api_key)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Process files
    success_count = 0
    for vcon_path in vcon_files:
        try:
            result = await transcribe_vcon(
                str(vcon_path),
                api_key,
                args.language,
                args.force
            )
            if result:
                success_count += 1
        except Exception as e:
            print(f"Error processing {vcon_path}: {e}")

    print(f"\nCompleted: {success_count}/{len(vcon_files)} files transcribed")


if __name__ == "__main__":
    asyncio.run(main())
