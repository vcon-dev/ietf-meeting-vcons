# Session State — IETF vCon Work
_Last saved: 2026-08-11_

## What's Running Right Now

Nothing. No transcription or conversion jobs are in flight.

## Git State
- **Repo**: `/Users/openconserver/Documents/GitHub/vcon-dev/ietf-meeting-vcons`
- **Branch**: `ietf126-vienna`, branched from `1f1b394`. Committed, **not
  pushed** — no PR opened yet.

## What Was Accomplished This Session

Added **IETF 126 (Vienna, 18–24 July 2026)** — 170 vCons in `ietf126/`.

1. Converted all 170 working groups with `ietf2vcon convert-all --meeting 126
   --transcript-source youtube --parallel 4` (~17 min).
2. 142 sessions have a recording; 140 of those carry a YouTube auto-caption
   transcript in WTF format.
3. Normalised transcripts from attachment to `analysis` with the new
   `scripts/wtf_attachment_to_analysis.py`, then ran
   `scripts/migrate_compliance_core02.py --meeting 126`.
4. Validated: **170/170 files are clean** against
   `draft-ietf-vcon-vcon-core/vcon_json_schema.json`, same as IETF 125.

### Upstream fix in `ietf2vcon`
`_try_youtube_captions()` gated on `"youtube.com" in video_url`, but the
Datatracker publishes recordings as `youtu.be/...` short links, so caption
fetching was skipped for every session. Fixed in
`/Users/openconserver/Documents/GitHub/vcon-dev/ietf2vcon`, branch
`fix/youtu-be-captions` (committed, not pushed).

## Known Gaps
- **Meetings 110–124 have no transcripts at all.** The "Refresh all meetings
  (110-125) with yt-dlp YouTube captions" commit (`9bdd037`) landed no analysis
  entries — the `youtu.be` bug above is the likely cause. With that bug fixed, a
  re-run should now populate them. ~1900 sessions, roughly 3 hours at the
  IETF 126 rate.
- `ietf126_bess_35453` and `ietf126_sustain_35566` have recordings but no
  YouTube captions available yet; re-run later or fall back to Whisper.
- IETF 125 transcripts are Whisper (`vendor: whisper`); IETF 126 are YouTube
  (`vendor: youtube`, `product: auto-captions`). Whisper quality is higher and
  the two can coexist in `analysis`.

## Key Commands
```bash
# Convert a whole meeting (YouTube captions, 4-way parallel)
ietf2vcon convert-all --meeting 126 --output-dir ./build126 \
    --transcript-source youtube --parallel 4

# Normalise + validate after copying *.vcon.json into ietf<N>/
python scripts/wtf_attachment_to_analysis.py --meeting 126
python scripts/migrate_compliance_core02.py --meeting 126

# Higher-quality local transcription (slow)
python scripts/whisper_transcribe.py --meeting 126 --model medium
```
