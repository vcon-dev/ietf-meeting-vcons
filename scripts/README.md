# scripts

Helper scripts for this dataset. Release 3.0.0 is in vCon syntax `0.5.0` (draft-ietf-vcon-vcon-core-05). Several scripts here were written for earlier syntax versions of the files and are kept as a record of how earlier releases were made. **Do not run a retired script on the current files**: some of them would damage them.

The files are produced by [ietf2vcon](https://github.com/vcon-dev/ietf2vcon) and two post-processing tools, `chair_determination.py` and `reference_types.py`, not by the older scripts. See "How the dataset is produced" in the top-level README, and [README-tools.md](README-tools.md) for the two tools.

The older scripts below look only for meeting directories named `ietf<N>/` and do not read the `interim-*` directories. The two post-processing tools read both.

## The post-processing tools (new in 3.0.0)

They need only the Python standard library, git and Python 3.11 or later. They are documented in [README-tools.md](README-tools.md), and every option is listed by `--help`.

| Script | What it does |
|---|---|
| `chair_determination.py` | Writes the chair determination record into every file of a completed meeting, and adds the chairs of sessions that three independent sources confirm. Needs the converter's log (`convert-<meeting>.log`) and a completion marker (`<meeting>.done`) for each meeting, the proceedings mirror, and the commit of the previous release. Run it again after any conversion. |
| `reference_types.py` | Corrects the `mimetype` of unhashed material references and adds `recording_note` to files whose recording may belong to another session. Refuses to apply to a git working tree with uncommitted changes, so commit the chair pass first. |
| `check_chair_determination.py` | Independent check of a tree against the commit it was produced from, after the chair pass. |
| `check_reference_types.py` | Independent check of a tree against a copy from before the reference pass. |
| `tests/` | The tools' tests (`python -m pytest tests -q -p no:cacheprovider`, run from `scripts/`). They use invented names and `example.org` addresses and make no network request. |

The order is: convert, chair determination (plan, apply, verify), commit, reference types (plan, apply, check), validate. The converter's own validator (`validate_vcons.py` in the ietf2vcon repository) is run as a separate program.

## Scripts that apply to 0.5.0 files

| Script | What it does | Notes for 0.5.0 files |
|---|---|---|
| `externalize_transcripts.py` | Moves inline WTF transcript bodies out of the vCons into `.wtf.json` files and replaces each with a `url` and `content_hash`. `--verify` re-hashes the references against local files. | Selects entries by `type` (`wtf_transcription`), so the chair determination entry (type `report`) is left alone. Sets `mediatype`. Tested on a copy of an IETF 126 file: externalize, then verify, gave no problems. |
| `download_audio.py` | Downloads the audio of a meeting's video recordings. Writes only audio files. | Reads the recording `url` from `dialog`. |
| `backfill_youtube_captions.py` | Adds auto-caption transcripts to files that have a video recording and no `youtube` transcript. | Works on `dialog` of type `recording` and adds a `wtf_transcription` entry. Two differences from converter output: the new entry has no `mediatype` (every entry in 3.0.0 has one; run `externalize_transcripts.py` on it, or add `"mediatype": "application/json"`), and it is appended after the chair determination entry, so never take the last analysis entry as the transcript. Read, not run, for this note. |
| `transcribe_mi.py` | Transcribes caption-less sessions on-device with the `mi` tool (macOS 26 or later). | Same two differences as the backfill script. Read, not run. |
| `transcribe_audio_authed.py` | The same for the login-gated audio era (IETF 90-94), using the operator's own signed-in browser session. | Same two differences. Read, not run. |
| `requirements.txt` | Python dependencies of the transcription scripts. | |

If you add a transcript to a file, keep `updated_at` current (the transcription scripts do; `externalize_transcripts.py` does not) and keep the chair determination entry.

## Not usable on 0.5.0 files

| Script | Why |
|---|---|
| `transcribe.py` (cloud transcription service) | Looks for a dialog of type `video`. Files have type `recording`, so it skips every file. It also writes `body` as an object rather than a string, with no `mediatype`. No transcript in the dataset came from it. |
| `whisper_transcribe.py` (local speech recognition) | The same dialog-type filter, so it skips every file, and the same `body` and `mediatype` problems. It needs a rewrite before use. |

## Retired one-off migrations

Each of these was run once on a previous release to move the files from one state to another. They are not maintenance tools. The counts below are from a dry run (or, for the last group, a reading) against the 8,179 `ietf<N>` files of 3.0.0.

| Script | What it did | What it would do to a 0.5.0 file |
|---|---|---|
| `migrate_compliance_core02.py` | Brought 0.0.1-era files to core-02: set `vcon` to `0.4.0`, added `party` and `dialog` 0 to every attachment, declared `role` and `meta` in `extensions`, and so on. | **Rewrites the file as 0.4.0.** It would modify all 8,179 files: set `vcon` to `0.4.0`, add `role` to `extensions` (`role` is not a registered extension; 0.5.0 files use `CC`), and add `dialog: 0` to 121,430 attachments, which for a file with no recording is an index past the end of `dialog`. |
| `migrate_vcons.py` | An earlier core-02 migration: `mimetype` to `mediatype`, `type` to `purpose`, add `encoding` to analysis. | Would add `encoding: "json"` to the external transcript reference of 3,919 files. External references carry no `encoding`. |
| `wtf_attachment_to_analysis.py` | Moved WTF transcripts from `attachments` to `analysis`. | Changes nothing (0 files). The converter now writes transcripts to `analysis` itself. |
| `migrate_real_urls.py` | Replaced wrong Datatracker URLs written by an old converter. | Changes nothing (0 files). The converter was fixed. |
| `point_refs_at_proceedings.py` | Pointed hashed references at `www.ietf.org/proceedings/...` and moved the Datatracker page into `meta.datatracker_url`. | Changes nothing now (0 files; all references are already repointed), but it writes `meta` and adds `meta` to `extensions`, neither of which exists in 0.5.0. |
| `externalize_materials.py` | Turned material body references into hashed references, from a local mirror. | Not run for this note (needs a sibling `ietf2vcon` checkout and a mirror). By reading: it would write hashed references that keep the Datatracker material URL, add `meta.title`, and add `meta` to `extensions`. 3.0.0 hashed references point at `www.ietf.org/proceedings/` and carry no `meta`. The converter now does this step. |
| `fix_material_filenames.py` | Re-resolved each hashed reference to the file the Datatracker serves. | Not run for this note. By reading: it matches Datatracker material URLs, which no hashed reference has now, and reads titles from `meta`, which no attachment has now. The converter now does this step. |
| `fix_historical_chairs.py` | Replaced chair parties with the chairs at the time of the session, from the Datatracker's history. | Not run for this note (it needs network access). By reading, it would rewrite `parties` from the newest history snapshot at or before the session, without the live roles, without handling history records that share a timestamp, and without updating `sessions[0].parties`, the Dialog `parties` or the chair determination record. With `--drop-unverifiable` it removes chairs. Chairs are now determined by the converter and the chair pass. |
