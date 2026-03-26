# IETF Meeting vCons

This repository contains [vCon](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-container/) (Virtual Conversation Container) files for IETF working group sessions from meetings 110-125 (March 2021 - March 2026).

## What is vCon?

vCon is an IETF standard format for capturing conversation data. Each vCon file contains:

- **Meeting metadata** - Date, location, working group information
- **Video recording** - YouTube URL for the session recording
- **Transcript** - Full transcript in [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-wtf-transcription/) with word-level timestamps
- **Materials** - Links to slides, agenda, minutes, and other session documents
- **Participants** - Working group chairs and attendee information
- **Lawful basis** - IETF Note Well documentation per [draft-howe-vcon-lawful-basis](https://datatracker.ietf.org/doc/draft-howe-vcon-lawful-basis/)

## Repository Structure

```
ietf-meeting-vcons/
├── ietf110/          # IETF 110 (March 2021, Online)
│   ├── ietf110_6man_28833.vcon.json
│   ├── ietf110_httpbis_28597.vcon.json
│   └── ...
├── ietf111/          # IETF 111 (July 2021, Online)
├── ietf112/          # IETF 112 (November 2021, Online)
├── ietf113/          # IETF 113 (March 2022, Vienna)
├── ietf114/          # IETF 114 (July 2022, Philadelphia)
├── ietf115/          # IETF 115 (November 2022, London)
├── ietf116/          # IETF 116 (March 2023, Yokohama)
├── ietf117/          # IETF 117 (July 2023, San Francisco)
├── ietf118/          # IETF 118 (November 2023, Prague)
├── ietf119/          # IETF 119 (March 2024, Brisbane)
├── ietf120/          # IETF 120 (July 2024, Vancouver)
├── ietf121/          # IETF 121 (November 2024, Dublin)
├── ietf122/          # IETF 122 (March 2025, Bangkok)
├── ietf123/          # IETF 123 (July 2025, Madrid)
├── ietf124/          # IETF 124 (November 2025, Yokohama)
└── ietf125/          # IETF 125 (March 2026, Shenzhen)
```

## File Naming Convention

Files follow the pattern: `ietf{meeting}_{group}_{session_id}.vcon.json`

- `meeting` - IETF meeting number (110-124)
- `group` - Working group acronym (e.g., `httpbis`, `quic`, `tls`)
- `session_id` - Unique session identifier from the IETF Datatracker

## vCon Structure

Each vCon file follows the [draft-ietf-vcon-vcon-container](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-container/) specification:

```json
{
  "vcon": "0.0.1",
  "uuid": "unique-identifier",
  "created_at": "2024-11-07T15:30:00Z",
  "subject": "IETF 121 - QUIC Working Group Session",
  "parties": [
    {"name": "Chair Name", "mailto": "chair@example.com", "role": "chair"}
  ],
  "dialog": [
    {"type": "video", "url": "https://www.youtube.com/watch?v=..."}
  ],
  "attachments": [
    {"type": "agenda", "url": "https://datatracker.ietf.org/..."},
    {"type": "slides", "url": "https://datatracker.ietf.org/..."},
    {"type": "lawful_basis", "body": {"lawful_basis": "legitimate_interests", ...}}
  ],
  "analysis": [
    {"type": "wtf_transcription", "spec": "draft-howe-wtf-transcription-00", "body": {...}}
  ]
}
```

## Data Sources

All data is sourced from public IETF resources:

- **Session metadata**: [IETF Datatracker API](https://datatracker.ietf.org/api/)
- **Video recordings**: [IETF YouTube Channel](https://www.youtube.com/@ietf)
- **Transcripts**: YouTube auto-generated captions
- **Materials**: IETF Meeting Materials Archive

## IETF Note Well

All IETF meeting sessions are conducted under the [IETF Note Well](https://www.ietf.org/about/note-well/), which permits recording, transcription, and publication. This is documented in each vCon's `lawful_basis` attachment.

## Statistics

| Metric | Value |
|--------|-------|
| Meetings | 16 (IETF 110-125) |
| Total vCons | 2,408 |
| Date Range | March 2021 - March 2026 |
| Working Groups | ~50 per meeting |

## Usage Examples

### Python

```python
import json

# Load a vCon
with open("ietf121/ietf121_quic_33502.vcon.json") as f:
    vcon = json.load(f)

# Get session info
print(f"Subject: {vcon['subject']}")
print(f"Video: {vcon['dialog'][0]['url']}")

# Access transcript
for analysis in vcon.get("analysis", []):
    if analysis["type"] == "wtf_transcription":
        transcript = analysis["body"]
        for segment in transcript["segments"][:5]:
            print(f"[{segment['start']:.1f}s] {segment['text']}")
```

### jq (Command Line)

```bash
# Get all video URLs from a meeting
jq -r '.dialog[0].url' ietf121/*.vcon.json

# Extract transcript text
jq -r '.analysis[] | select(.type=="wtf_transcription") | .body.segments[].text' file.vcon.json

# List all working groups in a meeting
ls ietf121/*.vcon.json | sed 's/.*ietf121_\(.*\)_.*/\1/' | sort -u
```

## Generation Tool

These vCons were generated using [ietf2vcon](https://github.com/vcon-dev/ietf2vcon), an open-source tool for converting IETF meeting sessions to vCon format.

To generate additional vCons:

```bash
pip install ietf2vcon
ietf2vcon convert --meeting 125 --group quic
```

## Speechmatics Transcription

This repository includes tools to re-transcribe IETF meeting audio using [Speechmatics](https://www.speechmatics.com/) for higher-quality transcriptions with speaker diarization.

### Prerequisites

1. **Speechmatics API Key**: Sign up at [speechmatics.com](https://www.speechmatics.com/) and obtain an API key.

2. **FFmpeg**: Required for audio processing.
   - macOS: `brew install ffmpeg`
   - Ubuntu/Debian: `apt install ffmpeg`
   - Windows: Download from [ffmpeg.org](https://ffmpeg.org/download.html)

3. **Python dependencies**:
   ```bash
   pip install -r scripts/requirements.txt
   ```

### Usage

Set your API key as an environment variable:
```bash
export SPEECHMATICS_API_KEY="your-api-key-here"
```

**Transcribe a single vCon file:**
```bash
python scripts/transcribe.py ietf121/ietf121_quic_33502.vcon.json
```

**Transcribe all sessions from a specific meeting:**
```bash
python scripts/transcribe.py --meeting 121
```

**Transcribe a specific working group:**
```bash
python scripts/transcribe.py --meeting 121 --group quic
```

**Transcribe all vCons missing Speechmatics transcription:**
```bash
python scripts/transcribe.py --all-pending
```

**Preview which files would be transcribed:**
```bash
python scripts/transcribe.py --all-pending --dry-run
```

### Transcription Output

The script:
1. Downloads audio from the YouTube recording linked in each vCon
2. Submits the audio to Speechmatics for transcription with speaker diarization
3. Converts the result to [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-wtf-transcription/)
4. Updates the vCon file with the new transcription in the `analysis` array

The Speechmatics transcription is stored alongside any existing YouTube transcription, with `"vendor": "speechmatics"` to distinguish it.

### WTF Format Features

The Speechmatics transcription includes:
- **Word-level timestamps**: Precise timing for each word
- **Speaker diarization**: Identification of different speakers
- **Confidence scores**: Per-word and per-segment confidence metrics
- **Segments**: Logical groupings of speech (sentences/phrases)
- **Quality metrics**: Overall transcription quality assessment

## Local Whisper Transcription

This repository also includes a tool for transcribing IETF meetings using [OpenAI Whisper](https://github.com/openai/whisper) locally via [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No API key or cloud service is required.

### Prerequisites

1. **FFmpeg**: Required for audio processing.
   - macOS: `brew install ffmpeg`
   - Ubuntu/Debian: `apt install ffmpeg`

2. **Python dependencies**:
   ```bash
   pip install -r scripts/requirements.txt
   ```

### Usage

**Transcribe a single vCon file:**
```bash
python scripts/whisper_transcribe.py ietf125/ietf125_quic_XXXXX.vcon.json
```

**Transcribe all sessions from a specific meeting:**
```bash
python scripts/whisper_transcribe.py --meeting 125
```

**Transcribe a specific working group:**
```bash
python scripts/whisper_transcribe.py --meeting 125 --group quic
```

**Use a faster (smaller) model:**
```bash
python scripts/whisper_transcribe.py --meeting 125 --model medium
```

**Transcribe all vCons missing Whisper transcription:**
```bash
python scripts/whisper_transcribe.py --all-pending
```

**Preview which files would be transcribed:**
```bash
python scripts/whisper_transcribe.py --meeting 125 --dry-run
```

### Model Selection

| Model | Size | Speed | Quality |
|-------|------|-------|---------|
| `tiny` | 39M | Fastest | Low |
| `base` | 74M | Fast | Fair |
| `small` | 244M | Moderate | Good |
| `medium` | 769M | Moderate | Better |
| `large-v3` | 1.5G | Slow | Best (default) |

### Transcription Output

The script:
1. Downloads audio from the YouTube recording linked in each vCon
2. Transcribes locally using faster-whisper with word-level timestamps
3. Converts the result to [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-wtf-transcription/)
4. Updates the vCon file with the new transcription in the `analysis` array

The Whisper transcription is stored with `"vendor": "whisper"` to distinguish it from YouTube auto-captions and Speechmatics transcriptions. It includes real word-level timestamps and per-segment confidence scores.

## Related Specifications

- [draft-ietf-vcon-vcon-container](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-container/) - vCon container format
- [draft-howe-wtf-transcription](https://datatracker.ietf.org/doc/draft-howe-wtf-transcription/) - World Transcription Format
- [draft-howe-vcon-lawful-basis](https://datatracker.ietf.org/doc/draft-howe-vcon-lawful-basis/) - Lawful basis extension

## Contributing

Contributions are welcome! Please open an issue or pull request if you:

- Find errors in the vCon data
- Want to add vCons for additional meetings
- Have suggestions for improvements

## License

This data is made available under the BSD-3-Clause License. See [LICENSE](LICENSE) for details.

The underlying IETF meeting content is subject to the [IETF Trust Legal Provisions](https://trustee.ietf.org/documents/trust-legal-provisions/).

## Acknowledgments

- IETF for making meeting recordings and materials publicly available
- The vCon working group for developing the conversation container standard
- YouTube for hosting IETF meeting recordings with auto-generated captions
