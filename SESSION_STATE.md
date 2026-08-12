# Session State — IETF vCon Work
_Last saved: 2026-08-11 (later session)_

## What's Running Right Now

Nothing. No transcription or conversion jobs are in flight.

## Blocking Next Step

**Two stale branches still pin the pre-rewrite history**, which is why a fresh
clone is still ~513 MB rather than ~21 MB. Both are merged and both are
preserved in the history bundle. Deleting them is the last step of the rewrite:

```bash
git push origin --delete fix/core02-compliance speechmatics
```

`fix/core02-compliance` (991b2bc) and `speechmatics` (7d596b9) each reach
9c5ae15, the commit carrying a 676 MB tree. Clones fetch all branches, so those
two drag the whole old history along. `refs/pull/*` will still pin the objects
server-side, but clones do not fetch those.

## Git State
- **Repo**: `/Users/openconserver/Documents/GitHub/vcon-dev/ietf-meeting-vcons`
- **Branch**: `main` at `b216dd5`, the post-rewrite history (3 commits: a
  synthetic root plus the two IETF 126 work commits). Force-pushed and live.
- All 16 `transcripts-ietf1{10..25}` tags retargeted to the new history;
  releases and their 1,947 assets unaffected.
- `ietf2vcon` PR #1 (the youtu.be fix) is merged.
- IETF 95-109 lands on branch `ietf95-109-backwards`.

## What Was Accomplished This Session

### 1. IETF 126 (Vienna, 18-24 July 2026)
170 vCons in `ietf126/`, one per working group, from 239 sessions. 142 have a
recording, 140 of those have a transcript. Generated with `ietf2vcon convert-all
--meeting 126 --transcript-source youtube --parallel 4` (~17 min), then
normalised with `scripts/wtf_attachment_to_analysis.py` and
`scripts/migrate_compliance_core02.py`.

### 2. Fixed the bug that had suppressed every transcript
`_try_youtube_captions()` in `ietf2vcon` gated on `"youtube.com" in video_url`,
but the Datatracker publishes recordings as `youtu.be/...` short links. Caption
fetching was therefore skipped for every session ever converted, which is why
meetings 110-124 had **zero** transcripts despite commit `9bdd037` claiming a
YouTube caption refresh.

### 3. Backfilled 110-124
`scripts/backfill_youtube_captions.py` patches transcripts into existing vCons
in place, preserving UUIDs. 1,570 sessions transcribed. Coverage is now complete:
2,087 transcripts against 2,089 recordings across all 17 meetings.

A stale `yt-dlp` (2026.3.17) failed the first attempt with "Sign in to confirm
you're not a bot" on every request, which reads as an IP ban but was not.
`brew upgrade yt-dlp` to 2026.7.4 fixed it outright.

### 4. Externalized transcripts for 110-125
Inline WTF bodies would have made the repo ~670 MB. `draft-ietf-vcon-vcon-core-02`
allows an Analysis Object to carry `url` + `content_hash` instead of
`body` + `encoding`, so `scripts/externalize_transcripts.py` extracts the bodies
and leaves SHA-512-verified references. Repo is ~70 MB instead. IETF 126 stays
inline on purpose, as a self-contained reference example.

Verified: 2,578/2,578 schema-clean, 1,947 references re-hash correctly, and
re-inlining reproduces the originals byte for byte (proven on IETF 126 before
applying).

### 5. Extended backwards to IETF 95-109
15 more meetings, 2,031 vCons, converted with the same pipeline in ~54 minutes.
1,465 have a recording and 1,260 have a transcript. All schema-clean, all
externalized, all published as releases.

The dataset is now **IETF 95-126: 32 consecutive meetings, 4,609 vCons,
3,347 transcripts**, April 2016 to July 2026.

The 198-session gap in IETF 95-98 is not a pipeline failure: those 2016-2017
videos have no captions on YouTube at all (`yt-dlp` reports "has no automatic
captions"). Local Whisper is the only route.

## Known Gaps
- `ietf126_bess_35453` and `ietf126_sustain_35566` have recordings but YouTube
  has published no auto-captions. Retried after the yt-dlp upgrade; still none.
  Re-run later or transcribe locally with Whisper.
- IETF 125 is Whisper (`vendor: whisper`), everything else is YouTube
  (`vendor: youtube`). Whisper is higher quality; both can coexist in `analysis`.
- Meetings 110-124 could be re-transcribed with Whisper for quality. ~1,900
  sessions, so budget days rather than hours.

## Next Steps Backwards

Remaining eras, in the order they get harder:

- **IETF 90-94** (2014-2015): audio MP3 only, on `ietf.org/audio/`. No captions
  exist. Needs an audio-dialog adapter plus local Whisper. 5 meetings.
- **IETF 66-89** (2006-2014): materials only, no recordings (87, 88, 89 return
  zero). Dialog-less vCons, which is fine: a vCon does not require a dialog.
  24 meetings.
- **IETF 1-65** (1986-2006): nothing in the Datatracker (0 minutes at IETF 64
  and below), but web proceedings exist (`ietf.org/proceedings/50/` is live).
  Needs HTML scraping. Highest effort, most historically interesting.

Two anomalies worth chasing: **IETF 107** has zero recording documents despite
certainly being recorded, so its recordings live somewhere the Datatracker does
not index. **IETF 86** has exactly one recording, which smells like a data-entry
artifact.

## Candidate Next Datasets
Researched adjacent organisations. RIPE is the strongest: it self-hosts video
and publishes **human stenography transcripts**, at stable paths
(`meetings.ripe.net/archives/video|chat|steno/<id>`), so a RIPE adapter avoids
YouTube throttling entirely and yields better transcripts. NANOG is second.
The RTC circuit (TADSummit, Kamailio World, ClueCon, CommCon) is YouTube-channel
shaped with no per-session index, so each needs a scraper.

## Key Commands
```bash
# Add a new meeting
ietf2vcon convert-all --meeting 127 --output-dir ./build127 \
    --transcript-source youtube --parallel 4
python scripts/wtf_attachment_to_analysis.py --meeting 127
python scripts/migrate_compliance_core02.py --meeting 127

# Backfill missing transcripts (keep yt-dlp current)
python scripts/backfill_youtube_captions.py --meetings 127 --workers 2 --delay 2

# Externalize, then verify
python scripts/externalize_transcripts.py --meeting 127 \
    --url-prefix https://github.com/vcon-dev/ietf-meeting-vcons/releases/download/transcripts-ietf127 \
    --body-dir transcripts/ietf127
python scripts/externalize_transcripts.py --meeting 127 \
    --body-dir transcripts/ietf127 --verify
```
