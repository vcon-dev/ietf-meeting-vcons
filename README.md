# IETF Meeting vCons

This repository contains [vCon](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/) (Virtual Conversation Container) files for IETF working group sessions from meetings 66-126 (July 2006 - July 2026): 61 consecutive meetings, 8,179 sessions.

## What is vCon?

vCon is an IETF standard format for capturing conversation data. Each vCon file contains:

- **Meeting metadata** - Date, location, working group information
- **Video recording** - YouTube URL for the session recording
- **Transcript** - Full transcript in [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/) with word-level timestamps
- **Materials** - Links to slides, agenda, minutes, and other session documents
- **Participants** - The working group chairs who were serving at the time of
  the session, plus an attendees party
- **Lawful basis** - IETF Note Well documentation per [draft-howe-vcon-lawful-basis](https://datatracker.ietf.org/doc/draft-howe-vcon-lawful-basis/)

## Repository Structure

One directory per meeting, `ietf<N>/`, holding one vCon per working group
session:

```
ietf-meeting-vcons/
├── ietf66/           # IETF 66 (July 2006, Montreal)
│   ├── ietf66_dnsop_1234.vcon.json
│   └── ...
├── ietf67/
├── ...
└── ietf126/          # IETF 126 (July 2026, Vienna)
```

The dataset spans four eras, which differ in what the IETF published at the
time rather than in how the vCons are built:

| Meetings | Period | What exists | Dialog |
|---|---|---|---|
| 66-89 | 2006-2014 | Agendas, minutes, slides. No recordings. | none |
| 90-94 | 2014-2015 | Audio MP3 on ietf.org, some YouTube video | `audio/mpeg` or `video/mp4` |
| 95-125 | 2016-2026 | YouTube video, most with auto-captions | `video/mp4` |
| 126 | July 2026 | YouTube video with auto-captions | `video/mp4` |

A vCon is not required to have a dialog. The 66-89 meetings have no recordings,
but every one of those sessions carries its agenda, minutes and slides, which
is still a record of the session worth having.

Transcript bodies for meetings 66-125 are **not** in this repository. They are
published as GitHub Release assets and referenced from the vCons by URL and
hash. See [Transcripts](#transcripts) below.

## Getting the data

**Git** — the whole dataset with history:

```bash
git clone https://github.com/vcon-dev/ietf-meeting-vcons.git
```

**S3** — anonymous read, no AWS account or credentials required. Useful for
pulling a single meeting, or for streaming the set without a 140 MB clone:

```
s3://ietf-meeting-vcons/{meeting}/{filename}.vcon.json
https://ietf-meeting-vcons.s3.amazonaws.com/{meeting}/{filename}.vcon.json
```

```bash
# one session, over plain HTTPS
curl -O "https://ietf-meeting-vcons.s3.amazonaws.com/ietf126/ietf126_vcon_35521.vcon.json"

# one meeting
aws s3 sync s3://ietf-meeting-vcons/ietf126/ ./ietf126 --no-sign-request

# browse or mirror the whole set (--no-sign-request = no credentials)
aws s3 ls s3://ietf-meeting-vcons/ --recursive --no-sign-request
aws s3 sync s3://ietf-meeting-vcons/ ./ietf-vcons --no-sign-request
```

The bucket is read-only to the public: `s3:GetObject` and `s3:ListBucket` are
granted anonymously, writes are denied. It carries the 8,179 vCons and nothing
else — transcript bodies stay on GitHub Releases, which is where the vCons
reference them.

## Deploying this dataset

`dataset.json` at the repo root declares the whole repository as a single
dataset, so a loader can ingest it without being told how the files are laid
out:

```json
{
  "name": "ietf-meeting-vcons",
  "version": "2.0.0",
  "vcon_dir": ".",
  "count": 8179,
  "spec": "0.4.0"
}
```

`vcon_dir` is the root, and vCons live in per-meeting subdirectories, so a
loader should walk it recursively. `count` is a checksum on completeness: a
partial or truncated checkout can be rejected instead of silently ingesting
fewer sessions than the dataset claims.

## File Naming Convention

Files follow the pattern: `ietf{meeting}_{group}_{session_id}.vcon.json`

- `meeting` - IETF meeting number (66-126)
- `group` - Working group acronym (e.g., `httpbis`, `quic`, `tls`)
- `session_id` - Unique session identifier from the IETF Datatracker

## vCon Structure

Each vCon file follows the [draft-ietf-vcon-vcon-core](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/) specification:

```json
{
  "vcon": "0.4.0",
  "uuid": "019d3273-6d8e-877c-9dd8-dd37220d739c",
  "created_at": "2026-07-22T12:00:00+00:00",
  "subject": "IETF 126 - VCON Working Group Session",
  "extensions": ["lawful_basis", "role", "meta", "wtf_transcription"],
  "parties": [
    {"name": "Chair Name", "mailto": "chair@example.com", "role": "chair"}
  ],
  "dialog": [
    {"type": "recording", "mediatype": "video/mp4", "url": "https://youtu.be/..."}
  ],
  "attachments": [
    {"purpose": "agenda", "url": "https://www.ietf.org/proceedings/126/agenda/agenda-126-vcon-00.md",
     "content_hash": "sha512-...", "mediatype": "text/markdown", "filename": "agenda-126-vcon-00.md",
     "meta": {"title": "VCON Agenda", "datatracker_url": "https://datatracker.ietf.org/meeting/126/materials/agenda-126-vcon"},
     "party": 0, "dialog": 0},
    {"purpose": "slides", "url": "https://www.ietf.org/proceedings/126/slides/slides-126-vcon-chair-slides-00.pdf",
     "content_hash": "sha512-...", "mediatype": "application/pdf", "party": 0, "dialog": 0},
    {"purpose": "lawful_basis", "encoding": "json", "body": "{\"lawful_basis\": \"legitimate_interests\", ...}"}
  ],
  "analysis": [
    {"type": "wtf_transcription", "dialog": 0, "vendor": "youtube", "encoding": "json", "body": "{...}"}
  ]
}
```

Note the details that follow from `draft-ietf-vcon-vcon-core-02`, the revision this
dataset targets (the draft is now at -03): the syntax
parameter is `0.4.0`, recordings use dialog type `recording` rather than
`video`, attachments use `purpose` rather than `type`, and every `body` is a
**string** rather than an object, with `encoding` declaring how to read it.

### Why materials point at www.ietf.org rather than the Datatracker

A material attachment carries `url` + `content_hash`, so the URL has to return
the hashed bytes. The Datatracker's `/meeting/N/materials/<doc>` is a display
endpoint, not a file: it renders Markdown agendas as HTML pages and converts
PowerPoint decks to PDF, so a hash of the published file cannot describe what it
serves. `https://www.ietf.org/proceedings/{meeting}/{type}/{filename}` serves
each file verbatim, is the tree `rsync.ietf.org::proceedings` mirrors, and is
reachable by scripted clients across all eras. The Datatracker page is still the
right thing for a human to open, so it is kept in `meta.datatracker_url`.

Landing pages -- the session page, the collaborative notes, a draft's own page --
are mutable HTML and carry no hash. They stay body references, since a
`content_hash` on them would assert an integrity guarantee that does not hold.

## Transcripts

There are 4,059 transcripts in
[WTF](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/) format,
covering every session with a recording except the gaps noted under
[Coverage gaps](#coverage-gaps). They are stored in one of two ways.

**IETF 126 is inline.** The transcript body sits in the vCon, so each file is
self-contained and needs no network access to read. This is the simplest form
and works well for a single meeting.

**IETF 66-125 are externally referenced.** A WTF body runs 200-400 KB, so
inlining all of them would make this repository well over a gigabyte. The core spec
anticipates this: the Analysis Object section states that it *"SHOULD contain
the body and encoding parameters **or** the url and content_hash parameters."*
Those meetings therefore reference their transcripts instead of carrying them:

```json
{
  "type": "wtf_transcription",
  "dialog": 0,
  "vendor": "youtube",
  "product": "auto-captions",
  "url": "https://github.com/vcon-dev/ietf-meeting-vcons/releases/download/transcripts-ietf126/ietf126_vcon_35521.wtf.json",
  "content_hash": "sha512-09FC_f0yKzzG1V6tih0kmrJm2Fd4HBHJPd7zqYXe4aiZcmnj0-1XEbReZmty25QCkIDhpgxOAxAkGA773ORZjA",
  "mediatype": "application/json"
}
```

This keeps the repository around 120 MB while preserving integrity: the
`content_hash` is a SHA-512 digest of the exact bytes served, formatted as
`sha512-` plus unpadded base64url, and it is covered by the vCon's own
signature. A substituted transcript fails verification exactly as an edited
inline one would.

### Reading a transcript either way

```python
import base64, hashlib, json, urllib.request

def load_transcript(analysis):
    """Return the WTF body whether it is inline or externally referenced."""
    if "body" in analysis:
        return json.loads(analysis["body"])

    raw = urllib.request.urlopen(analysis["url"]).read()

    algorithm, _, expected = analysis["content_hash"].partition("-")
    digest = hashlib.new(algorithm, raw).digest()
    actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if actual != expected:
        raise ValueError(f"content_hash mismatch for {analysis['url']}")

    return json.loads(raw)


with open("ietf126/ietf126_vcon_35521.vcon.json") as f:
    vcon = json.load(f)

for analysis in vcon.get("analysis", []):
    if analysis["type"] == "wtf_transcription":
        wtf = load_transcript(analysis)
        for segment in wtf["segments"][:5]:
            print(f"[{segment['start']:.1f}s] {segment['text']}")
```

Always check the hash before trusting a fetched body. That check is the only
thing separating an external reference from an unverified download.

### Regenerating the split

```bash
# Externalize a meeting's transcripts (writes bodies to transcripts/ietf<N>/)
python scripts/externalize_transcripts.py --meeting 125 \
    --url-prefix https://github.com/vcon-dev/ietf-meeting-vcons/releases/download/transcripts-ietf125 \
    --body-dir transcripts/ietf125

# Confirm every reference resolves against its local body file
python scripts/externalize_transcripts.py --meeting 125 \
    --body-dir transcripts/ietf125 --verify
```

`transcripts/` is gitignored. Publishing a meeting means uploading that
directory's `.wtf.json` files as assets on a release tagged
`transcripts-ietf<N>`, matching the URL prefix baked into the vCons.

## Chairs

Chair parties name the people who chaired the session **on the date it was
held**, not today's chairs. This matters: `ietf2vcon` reads the Datatracker's
current group record, which would otherwise stamp 2026 chairs on a 2006
session. `scripts/fix_historical_chairs.py` resolves them per date from
`grouphistory` and `rolehistory`.

The Datatracker's role history begins **2011-12-09**, when the feature was
switched on, so sessions before IETF 83 (March 2012) cannot be resolved. Rather
than assert today's chairs on a session from 2006, those vCons list **no chair
party at all** and keep only the attendees party. 2,006 vCons are in that state.
Absence of a claim is preferable to a false one; the chairs are recoverable from
the IETF proceedings pages for anyone who wants to do that work.

## Data Sources

All data is sourced from public IETF resources:

- **Session metadata**: [IETF Datatracker API](https://datatracker.ietf.org/api/)
- **Video recordings**: [IETF YouTube Channel](https://www.youtube.com/@ietf)
- **Transcripts**: three provenances, distinguished by `vendor` and able to
  coexist in `analysis`:
  - `youtube` — YouTube auto-generated captions, the default where they exist.
  - `whisper` — local Whisper (IETF 125), higher quality with word-level timing.
  - `apple` (`product: speechanalyzer`) — Apple's on-device Speech framework via
    the `mi` CLI, used where no captions exist: the audio era (IETF 90-94) and
    the caption-less 2016-2017 videos (IETF 95-98). Segment-level timing, no
    per-word timestamps or confidence.
- **Materials**: IETF Meeting Materials Archive

## IETF Note Well

All IETF meeting sessions are conducted under the [IETF Note Well](https://www.ietf.org/about/note-well/), which permits recording, transcription, and publication. This is documented in each vCon's `lawful_basis` attachment.

## Statistics

| Metric | Value |
|--------|-------|
| Meetings | 61 (IETF 66-126, consecutive) |
| Total vCons | 8,179 |
| Sessions with a recording | 4,077 (3,652 video, 425 audio) |
| Transcripts | 4,059 (140 inline, 3,919 externally referenced) |
| Transcript coverage | 4,059 of 4,077 recordings (99.6%) |
| Date Range | July 2006 - July 2026 |
| Working Groups | ~50 per meeting |
| Repository size | ~140 MB (plus ~1.1 GB of transcript bodies published separately) |

### Coverage gaps

18 of 4,077 recorded sessions have no transcript. Nearly all are YouTube videos
that have since been **removed or made private**, so there is no longer any
source to transcribe; a few are recordings that are silent or non-speech.

Everything else with a recording is transcribed. The audio era (IETF 90-94),
whose recordings moved behind IETF SSO in 2026, was fetched through an
authenticated session and transcribed on-device (see
[Transcribing without captions](#transcribing-without-captions)); the
caption-less 2016-2017 videos (IETF 95-98) the same way.

**IETF 66-89 has no recordings by design.** The IETF did not publish session
recordings in that era, so those 2,887 vCons are materials-only. They are not
empty: they carry 20,855 slide decks, 5,721 agendas and 5,634 minutes, a median
of 9 documents per session.

`ietf107` (March 2020, the first virtual meeting) is a special case within the
recorded era: it has **129 vCons and no recordings at all**, because the Datatracker holds no
recording documents for it. Its sessions are materials-only. A vCon is not
required to have a dialog; the agenda, slides and minutes still make these a
useful record of the session.

## Usage Examples

### Python

```python
import json

# Load a vCon
with open("ietf121/ietf121_quic_33502.vcon.json") as f:
    vcon = json.load(f)

# Get session info
print(f"Subject: {vcon['subject']}")
print(f"Recording: {vcon['dialog'][0]['url']}")

# Chairs
for party in vcon["parties"]:
    if party.get("role") == "chair":
        print(f"Chair: {party['name']}")
```

For transcripts see [Reading a transcript either way](#reading-a-transcript-either-way);
meetings 66-125 reference them by URL rather than carrying them inline.

### jq (Command Line)

```bash
# Get all recording URLs from a meeting
jq -r '.dialog[0].url // empty' ietf121/*.vcon.json

# Extract transcript text (IETF 126, inline bodies; note body is a JSON string)
jq -r '.analysis[] | select(.type=="wtf_transcription") | .body | fromjson | .segments[].text' \
    ietf126/ietf126_vcon_35521.vcon.json

# List transcript URLs for an externally referenced meeting
jq -r '.analysis[]? | select(.type=="wtf_transcription") | .url' ietf121/*.vcon.json

# List all working groups in a meeting
ls ietf121/*.vcon.json | sed 's/.*ietf121_\(.*\)_.*/\1/' | sort -u
```

## Generation Tool

These vCons were generated using [ietf2vcon](https://github.com/vcon-dev/ietf2vcon), an open-source tool for converting IETF meeting sessions to vCon format.

To generate additional vCons:

```bash
pip install ietf2vcon
ietf2vcon convert --meeting 126 --group quic
```

### Adding a New Meeting

A whole meeting is converted, normalised and validated in three steps. IETF 126
was produced this way:

```bash
ietf2vcon convert-all --meeting 126 --output-dir ./build126 \
    --transcript-source youtube --parallel 4
```

`ietf2vcon` stores WTF transcripts as an attachment, while this repository (and
[draft-howe-vcon-wtf-extension](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/),
which defines WTF as an *analysis* type) keeps them in `analysis`. After copying
the `*.vcon.json` files into `ietf126/`, normalise and bring them into
`draft-ietf-vcon-vcon-core-02` compliance:

```bash
python scripts/wtf_attachment_to_analysis.py --meeting 126
python scripts/migrate_compliance_core02.py --meeting 126
```

Both scripts accept `--dry-run` and `-v`, and both are idempotent: re-running
them on an already-migrated meeting is a no-op.

### Backfilling Transcripts

If sessions end up with a recording but no transcript, fetch the YouTube
auto-captions directly rather than regenerating the vCons, which would churn
their UUIDs:

```bash
python scripts/backfill_youtube_captions.py --meetings 110-124 --workers 2 --delay 2
```

It skips sessions that already have a YouTube transcript, so it is idempotent
and resumes cleanly after an interrupt. Two operational notes, both learned the
hard way:

- **Keep `yt-dlp` current.** A stale build fails with "Sign in to confirm you're
  not a bot" on every request, which looks exactly like an IP ban but is not.
  `brew upgrade yt-dlp` (or `pip install -U yt-dlp`) fixes it.
- **Do not raise `--workers` much.** YouTube throttles bulk caption fetches. The
  `--give-up-after` guard aborts on sustained throttling rather than burning
  through the queue against a block.

Sessions whose recording genuinely has no published captions are reported as
`no-captions` rather than as errors.

### Transcribing without captions

Where no captions exist — the audio era (IETF 90-94) and the caption-less
2016-2017 videos (IETF 95-98) — the audio is transcribed on-device with Apple's
Speech framework via the `mi` CLI (macOS 26+). It runs on
the Neural Engine at roughly 90x real time, needs no model download or API key,
and reads compressed audio directly. Output carries `vendor: apple`,
`product: speechanalyzer`; it has segment-level timing but no per-word
timestamps or confidence, so those fields are omitted rather than fabricated.

For sessions with a YouTube recording (extract audio, then transcribe):

```bash
python scripts/transcribe_mi.py --meetings 95-98
```

For the audio era, whose `ietf.org/audio/*.mp3` recordings moved **behind IETF
SSO** in 2026 (an anonymous request gets a Cloudflare 403 and an `auth.ietf.org`
redirect), fetching needs a browser-grade TLS fingerprint *and* an authenticated
session. `scripts/transcribe_audio_authed.py` reuses the operator's logged-in
Chrome session — it never handles a password:

```bash
# 1. Sign in at datatracker.ietf.org in Chrome, then open one audio URL
#    (e.g. https://www.ietf.org/audio/ietf92/<file>.mp3) to complete the
#    get.ietf.org OIDC flow until the file plays.
# 2. Then:
pip install curl_cffi browser_cookie3   # into the ietf2vcon venv
python scripts/transcribe_audio_authed.py --meetings 90-93
```

It re-reads Chrome cookies on every download to ride through Cloudflare
clearance rotation, and stops cleanly (resumably) if the browser session
expires. Both scripts are idempotent — a session that already has a transcript
is skipped — and both retry transient YouTube JS-challenge failures. Some
audio-era files are Ogg Vorbis mislabelled as `.mp3`, which Apple's decoder
rejects; the authed script transcodes those with ffmpeg and retries.

## Speechmatics Transcription

This repository includes tools to re-transcribe IETF meeting audio using [Speechmatics](https://www.speechmatics.com/) for higher-quality transcriptions with speaker diarization.

**No Speechmatics transcripts are currently in the dataset.** Every published transcript is either a YouTube auto-caption or, for IETF 125, local Whisper output. This section documents a tool that is available, not data you will find in the files.

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
3. Converts the result to [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/)
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
3. Converts the result to [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/)
4. Updates the vCon file with the new transcription in the `analysis` array

The Whisper transcription is stored with `"vendor": "whisper"` to distinguish it from YouTube auto-captions and Speechmatics transcriptions. It includes real word-level timestamps and per-segment confidence scores.

## Related Specifications

- [draft-ietf-vcon-vcon-core](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/) - vCon container format (supersedes the expired draft-ietf-vcon-vcon-container)
- [draft-howe-vcon-wtf-extension](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/) - World Transcription Format
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
