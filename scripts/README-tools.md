# Post-conversion tools

Two tools turn the converter's output into the published files, and two scripts check their work. They use only the Python standard library and git, and need Python 3.11 or later (tested with 3.14). Nothing here imports the converter. The converter's own validator is run as a separate program.

| File | What it does |
|---|---|
| `chair_determination.py` | Records, in every file, how the chairs of the session were determined, and adds the chairs of sessions that three independent sources confirm. |
| `reference_types.py` | Corrects the `mimetype` of unhashed material references and adds `recording_note` to files whose recording may belong to another session. |
| `check_chair_determination.py` | Independent check of a tree against the commit it was produced from, after the chair pass. |
| `check_reference_types.py` | Independent check of a tree against a copy from before the reference pass. |

Run `--help` on either tool for every option. Both tools default to a plan: they read everything, write only reports, and change no dataset file.

## Order of operations

1. **Convert** each meeting with the converter, keeping its log (see "Inputs").
2. **Mark** each meeting that converted cleanly as complete (see "Inputs").
3. **Chair determination**: plan, read the reports, apply, verify.
4. **Commit.** The reference pass refuses to run on a working tree with uncommitted changes, so commit the chair pass first.
5. **Reference types**: plan, apply, check.
6. **Validate** with the converter's validator, then commit.

**Run the chair pass again after any conversion of a meeting.** A conversion rebuilds `parties` without the chairs that the chair pass confirmed from minutes. It keeps the existing determination record only when it builds no new analysis of its own; a conversion that builds a new transcript drops the record. Applying the chair pass again puts the chairs back and leaves exactly one record. Run the reference pass again afterwards too: a conversion writes the converter's default reference types again.

## Inputs the tools cannot guess

| Option | What it is | Used by |
|---|---|---|
| `--dataset-root` | The directory holding the `ietf<N>/` and `interim-*/` directories. Default: the repository root, found relative to the script. | both |
| `--previous-rev` | The commit of the previous release, read through git. Required. For the chair pass it must be the commit of the release whose chairs are recorded as `previous-release` candidates: the tool checks that `dataset.json` at that commit says `--previous-version` (default `2.1.0`). For release 3.0.0 the value was `3b17026b`. | both |
| `--proceedings` | The proceedings mirror root: the directory holding `<meeting>/<group>.html` and `<meeting>/minutes/`, `<meeting>/agenda/`. Required by the chair pass. Optional for the reference pass (without it rule 3 is skipped; it resolved nothing in 3.0.0). | both |
| `--log-dir` | The converter's logs, one `convert-<meeting>.log` per meeting. Required by the chair pass. | chair |
| `--state-dir` | One `<meeting>.done` marker per completed meeting, and `<meeting>.failed` for a failed one. Required by the chair pass. | chair |
| `--cache-dir` | A cache of Datatracker responses. Default: a directory under the user's cache location (`$XDG_CACHE_HOME`, else `~/.cache`), never inside the repository. | chair |
| `--seed-cache` | A read-only cache directory to reuse first. Repeatable. Default: none. | chair |
| `--report-dir` | Where reports and logs go. Default: under the same user cache location. | both |
| `--git-dir` | The git repository to read the previous release from. Default: the dataset root. Needed only for a scratch copy that is not a git repository. | both |
| `--lock-file` | Optional. A batch driver's lock: a file holding its pid (or a directory with a file `pid`). Applying to the real dataset is refused while that process is alive. | both |
| `--converter` | A checkout of the converter with its virtual environment in `.venv`; its `scripts/validate_vcons.py` validates each touched meeting after `--apply`. Required with `--apply` unless `--no-validate`. | chair |

The logs and markers are produced by the batch driver that runs the conversions. If you run the converter yourself, make them like this (the log must not be colored or wrapped, so set `NO_COLOR`):

```bash
mkdir -p logs state
NO_COLOR=1 ietf2vcon convert-all --meeting 127 --output-dir ./ietf127 --rsync-mirror ./mirror -v \
    > logs/convert-127.log 2>&1
touch state/127.done          # only if the conversion finished cleanly
```

Interim meetings use their names: `logs/convert-interim-2026-vcon-01.log`, `state/interim-2026-vcon-01.done`, and `--meetings interim-2026-vcon-01`. Without `--meetings` the chair pass processes every meeting that has a `.done` marker.

The first run with an empty cache reads about thirty whole-table pages of the Datatracker API, one request at a time, one second apart, capped by `--max-requests` (default 200), with a ten minute stop after any HTTP 429. With a warm cache it makes no request. `--max-requests 0` forbids any.

## chair_determination.py

```bash
COMMON="--previous-rev <commit> --proceedings ./mirror/proceedings --log-dir ./logs --state-dir ./state"

# plan: reads only; reports go to --report-dir (plan-summary.txt, plan-mismatches.txt, plan.tsv, plan.json)
python scripts/chair_determination.py $COMMON [--meetings 127]

# apply: writes under --output-root, then validates every touched meeting
python scripts/chair_determination.py --apply --allow-production --output-root . \
    --converter /path/to/ietf2vcon $COMMON [--meetings 127]

# verify: reads only; exits 1 on any difference
python scripts/chair_determination.py --verify $COMMON [--meetings 127]
```

`--dry-run` (with `--apply`) runs every check and writes nothing. `--apply --update-records` also replaces the body of an existing record that differs from the plan; only that body and `updated_at` change.

### What it writes

For every file of a completed meeting, one entry appended at the end of `analysis` (existing entries keep their index): `type` `report`, `sessions` 0, `vendor` `ietf2vcon`, `product` `chair-determination`, `schema` `chair-determination-v1`, `mediatype` `application/json`, `encoding` `json`, and a `body` that is a JSON string. `meeting_metadata` is never touched. The body members (`outcome`, `basis`, `as_of`, `as_of_basis`, `datatracker`, `chairs`, `candidates`, `reason`, `note`) are described in the dataset README. For confirmed sessions it also writes `parties` (chair parties first, then the attendees party), and sets `sessions[0].parties` and every dialog's `parties` to the full index range.

Only these may differ from the file as read: the appended entry, `updated_at`, and for confirmed sessions `parties` with those index lists. The new file is checked against the file as read before saving and skipped with an error otherwise. Writes are atomic (temporary file, fsync, `os.replace`). A file that already has a record is verified against the plan and never given a second one, so a second apply changes nothing.

For ordinary sessions the tool recomputes the converter's rule from the cached Datatracker tables (group time, history snapshots, chair roles, live roles) and requires the result to equal the chairs in the file (same people, same order, same address) and to agree with the converter's own log line for that session. A file where they disagree is listed in `plan-mismatches.txt` and is not written.

### The evidence standard for minutes-confirmed chairs

The converter writes no chairs when several history snapshots share one timestamp but hold different chairs. Chairs are added to such a session only when all three sources name exactly the same people:

1. **Write order of the history.** Snapshot ids are handed out in write order across all groups, so each id can be dated from the many groups whose history shows when a snapshot was written. Every snapshot gets a window in which it was written. Snapshots certainly written after the session are "later"; those whose window contains the session are "uncertain". The chairs come from the earliest later snapshot (or the group's current chairs if none was written after the session), and count only if every snapshot that could be the right one names the same people.
2. **The group's proceedings page** (`<meeting>/<group>.html`) must name the same people. Names are compared by last name with accents folded, and by first initial where two chairs share a last name. Titles and suffixes (Dr, Prof, Ph.D., Jr) are ignored, the same words in another order match, and the other names the Datatracker records on a person count as that person. There is no fuzzy matching: a misspelling does not match.
3. **The session's own minutes or agenda** (`<meeting>/minutes/minutes-<meeting>-<group>*` and `<meeting>/agenda/agenda-<meeting>-<group>*`). A document confirms only if it has an explicit chairs line (`Chairs:`, `Chair(s):`, `Co-chairs:`, `WG Chairs:`; names may continue on the next few lines) whose names are exactly the same people. A document that names only some of them, or someone else, or has no such line, does not confirm. If one document names exactly the set and another names someone outside it, the session is not confirmed. Chairs are never inferred from who speaks. A remark on a labelled line that a chair was absent, remote or announced a departure leaves that person a role holder; someone "acting" or "standing in" is not a role holder (the person they stand in for is named). Prose such as "Chaired by X and Y" never confirms anything. Text, HTML, Markdown and docx files are read; PDF, Word 97, RTF and presentation files cannot be read and count as no document.

Research groups are treated like any other group. A chair's name is the person's current Datatracker name and the address is the one on the proceedings page (the role's address if the page has none); that page reflects the weeks after the meeting. Meetings from 99 onward have no proceedings pages, so ambiguous sessions there stay without chairs. Where the sources do not all agree the session stays without chairs and the candidates that were considered stay on record in the record's `candidates`; a confirmed session lists the evidence it relied on there too.

### Refusals when applying to the real dataset

`--apply` on the dataset itself, or on any git working tree, needs `--allow-production` (exit code 2 otherwise). It then refuses to start (exit code 2, with the reason) when a process whose command line contains `ietf2vcon convert` is running, when the `--lock-file` process is alive, when a requested meeting has no `.done`, or when any meeting has a `.failed` without a `.done` (with `--meetings`, only the named meetings count for the last two). The first two cannot be overridden. The checks run again immediately before the first file is written. A scratch copy outside a git working tree is not subject to them. The chair pass does not require a clean `git status`: it is meant to run on the converter's uncommitted output.

`--verify` reads the directory of every completed meeting and fails unless every `*.vcon.json` file has exactly one well-formed record, that agrees with the file's `parties` and equals the plan.

## reference_types.py

```bash
# plan: reads only
python scripts/reference_types.py --previous-rev <commit> --proceedings ./mirror/proceedings

# apply to the dataset (a clean git status is required, and no conversion or chair pass running)
python scripts/reference_types.py --apply --allow-production --output-root . \
    --previous-rev <commit> --proceedings ./mirror/proceedings

# check, against a copy of the tree from before the run
git archive <commit before the run> | tar -x -C /tmp/before
python scripts/check_reference_types.py /tmp/before .
```

### What it changes

An unhashed material reference is an attachment with no `url` and no `content_hash` whose inline JSON body is `{"url", "mimetype", "filename", "title"}`. The tool works on those whose `mimetype` is `application/pdf` (the converter's default guess), and on any whose file name maps to a different type. For each, in this order:

1. A `filename` with a known extension sets the type from a fixed table (`.html` and `.htm` to `text/html`, `.txt` to `text/plain`, `.md` to `text/markdown`, `.pdf`, `.json`, `.xml`, `.ppt`, `.pptx`, `.doc`, `.docx`, `.odp`, `.odt`, `.key`, `.zip`, `.gz`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.mp3`, `.mp4` and a few more; see `EXT_MIME`).
2. Otherwise the previous release's value for the same `url` in the same file. With the default `--previous-pdf ignore`, a previous `application/pdf` is not used, because it was the same default guess (it was wrong in every case where a file name could contradict it). `--previous-pdf keep` uses it.
3. Otherwise, with `--proceedings`: a single unambiguous file under `<proceedings>/<meeting>/` whose name continues the document name from the url (after a `.` or `-`) gives its extension.
4. Otherwise the `mimetype` member is removed: an unknown type is stated as unknown.

It never changes `url`, `title` or `filename`, and never touches attachments with a `url` (hashed external references) or attachments whose purpose is `ingress_info` or `lawful_basis`. It adds `recording_note` to the `meeting_metadata` body, and nothing else there.

For release 3.0.0 this set the type from the file name in 779 references (472 `application/json`, 217 pptx, 81 ppt, 7 `text/plain`, 1 zip, 1 gzip), from the previous release in 3,363 (3,354 `text/html`), and removed the member in 4,313; 592 references with a `.pdf` file name kept `application/pdf`. The mirror resolved none.

### The recording note

The dataset has one file per working group per meeting, named for one session, but the recording attached may come from another session of the same group. The tool adds a fixed sentence to the `meeting_metadata` body member `recording_note`:

- **Group A**: the session did not meet (a status other than `sched` in `meeting_metadata`) but the file has a recording dialog.
- **Group B**: status `sched`, the session has no `start`, and the file has a recording dialog.
- **Group C**: the recording's audio file name carries a timestamp that matches a different session of the same group. This is a fixed list of three files for release 3.0.0 (`ietf90_mpls_23162`, `ietf90_tsvwg_23197`, `ietf90_pcp_23229`); give another list with `--group-c-file`.

A file in groups A and C gets the A sentence followed by the C sentence. 3.0.0 has 7 files in A, 3 in B and 3 in C (one in both A and C).

### Guarantees and refusals

Only the `mimetype` member inside reference bodies, the `recording_note` member of the `meeting_metadata` body, and `updated_at` may differ from the file as read; a file where anything else would differ is skipped and reported as an error. A file that needs no change is not rewritten and its `updated_at` does not move. A second apply changes nothing. Writes are atomic, and are refused if the file changed on disk after it was read.

`--apply` on a git working tree needs `--allow-production`, and then refuses unless `git status --porcelain` is empty, no `chair_determination.py` or `ietf2vcon convert` process is running, and the `--lock-file` process (if given) is not alive. A scratch copy that is not in a git repository needs none of this; pass `--git-dir <repository>` so the previous release can still be read.

## Checking a run

```bash
# after the chair pass: the tree against the converter's output it was made from
python scripts/check_chair_determination.py --rev <commit of the converter output>

# after the reference pass: the tree against a copy from before the run
python scripts/check_reference_types.py <before> <after>
```

Both read only and exit 1 on any violation. They share no code with the tools they check.

## Tests

```bash
cd scripts
python -m pytest tests -q -p no:cacheprovider                 # both suites
python -m pytest tests/chair_determination -q -p no:cacheprovider
python -m pytest tests/reference_types -q -p no:cacheprovider
```

The tests use invented names and `example.org` addresses and make no network request. Some create a throwaway git repository inside the test's temporary directory.

## Reproducing release 3.0.0

The 3.0.0 files are the converter's output (commit `8d68ba8a`) with the chair pass applied, then the reference pass. Starting from that commit and the 3.0.0 logs and markers, with the Datatracker cache warm and `--previous-rev 3b17026b`, the two commands above give the published files, apart from the `updated_at` value of each file, which is the time of the run.
