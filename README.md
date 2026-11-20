# IETF Meeting vCons

This repository contains [vCon](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/) (Virtual Conversation Container) files for IETF group sessions: the numbered meetings 66-126 (July 2006 - July 2026), 61 consecutive meetings and 8,179 files, plus the VCON working group's two 2026 interim sessions. The files are in vCon syntax `0.5.0`, as defined by [draft-ietf-vcon-vcon-core-05](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/).

There is one file per group per meeting, not one per session. A group that met twice at a meeting has one file, named for one of its sessions. See [Known limitations and deviations](#known-limitations-and-deviations).

This is release 3.0.0. It replaces 2.1.0 (syntax `0.4.0`) and is not a drop-in replacement: see [Changes in 3.0.0](#changes-in-300) before upgrading code that reads the files.

## What is vCon?

vCon is an IETF standard format for capturing conversation data. Each vCon file contains:

- **Session** - The scheduled start and duration of the session, taken from the meeting's official schedule, and the parties and recording it covers
- **Meeting metadata** - Date, location, group, room, and the session's status (held, cancelled, did not meet, and so on)
- **Video or audio recording** - A link to the session recording (no media is stored here)
- **Transcript** - Full transcript in [WTF (World Transcription Format)](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/) with segment timestamps, where one exists
- **Materials** - Links to slides, agenda, minutes, bluesheets, chat logs and other session documents
- **Participants** - The chairs who held the role at the time of the session, where that can be established, plus an attendees party
- **Chair determination record** - How the chairs of the session were determined, and the evidence considered where they could not be
- **Lawful basis** - Documentation of the IETF Note Well (the IETF's published policy for participation in its meetings) per [draft-howe-vcon-lawful-basis](https://datatracker.ietf.org/doc/draft-howe-vcon-lawful-basis/)

## Repository Structure

One directory per meeting, holding one vCon per group. A numbered meeting is `ietf<N>/`; an interim is named as the Datatracker names it, since an interim has no number:

```
ietf-meeting-vcons/
├── ietf66/                    # IETF 66 (July 2006, Montreal)
│   ├── ietf66_dnsop_1234.vcon.json
│   └── ...
├── ietf67/
├── ...
├── ietf126/                   # IETF 126 (July 2026, Vienna)
├── interim-2026-vcon-01/      # VCON interim, 2026-01-27
└── interim-2026-vcon-02/      # VCON interim, 2026-09-09
```

The groups are working groups and also other groups that hold sessions at a meeting, such as research groups, the Hackathon and the Secretariat.

### Interim sessions

Interim coverage is not a backfill. Working groups have held hundreds of interims and only the VCON working group's are here, generated the same way and from the same Datatracker documents as everything else. Any loader that walks `vcon_dir` recursively picks them up without changes; one that assumes an `ietf<N>` directory name will miss them.

The dataset spans four eras, which differ in what the IETF published at the time rather than in how the vCons are built:

| Meetings | Period | Files | What exists | Dialog |
|---|---|---|---|---|
| 66-89 | 2006-2014 | 2,887 | Agendas, minutes, slides. No recordings, with one exception (see below). | none |
| 90-94 | 2014-2015 | 683 | Audio MP3 on ietf.org (424 files), some video (98 files) | `audio/mpeg` or `video/mp4` |
| 95-125 | 2016-2026 | 4,439 | Video (3,412 files), most with auto-captions | `video/mp4` |
| 126 | July 2026 | 170 | Video (142 files) with auto-captions | `video/mp4` |

The two interim files are not in the table; both have a video recording.

A vCon is not required to have a dialog. A session that was not held, or whose recording was never published, has none. The 66-89 meetings have no recordings, but every one of those files carries its agenda, minutes and slides, which is still a record of the session worth having. The one exception is `ietf86/ietf86_sdnrg_22511.vcon.json`, which links to an audio recording.

Transcript bodies for meetings 90-125 are **not** in this repository. They are published as GitHub Release assets and referenced from the vCons by URL and hash. See [Transcripts](#transcripts) below.

## Getting the data

**Git** - the whole dataset with history:

```bash
git clone https://github.com/vcon-dev/ietf-meeting-vcons.git
```

**S3** - anonymous read, no AWS account or credentials required. Useful for pulling a single meeting, or for streaming the set without a clone of about 145 MB of vCon files plus history:

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

The bucket is read-only to the public: `s3:GetObject` and `s3:ListBucket` are granted anonymously, writes are denied. It carries all 8,181 vCons and nothing else; transcript bodies stay on GitHub Releases, which is where the vCons reference them. The bucket follows the `main` branch: a push to `main` that changes a vCon replaces the bucket's contents to match, including deleting objects that are no longer in the repository.

## Deploying this dataset

`dataset.json` at the repo root declares the whole repository as a single dataset, so a loader can ingest it without being told how the files are laid out:

```json
{
  "name": "ietf-meeting-vcons",
  "version": "3.0.0",
  "vcon_dir": ".",
  "count": 8181,
  "spec": "0.5.0"
}
```

(The real file also has a `description`.) `vcon_dir` is the root, and vCons live in per-meeting subdirectories, so a loader should walk it recursively. `count` is a checksum on completeness: a partial or truncated checkout can be rejected instead of silently ingesting fewer files than the dataset claims. `spec` is the vCon syntax version of every file.

## File Naming Convention

Files follow the pattern: `{meeting}_{group}_{session_id}.vcon.json`

- `meeting` - `ietf` plus the meeting number for a numbered meeting (`ietf124`), or the Datatracker's interim name for an interim (`interim-2026-vcon-02`)
- `group` - Group acronym (e.g., `httpbis`, `quic`, `tls`)
- `session_id` - Identifier of one Datatracker session of that group at that meeting. The same value is in `meeting_metadata` as `session_id`.

## vCon Structure

Each vCon file follows the [draft-ietf-vcon-vcon-core-05](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/) specification. This example is abridged from `ietf126/ietf126_vcon_35521.vcon.json`: names and addresses are replaced with placeholders, long values are cut, most attachments and the transcript body are left out, and `updated_at` is omitted.

```json
{
  "vcon": "0.5.0",
  "uuid": "019ff22b-00ab-88bc-9dd8-dd37220d739c",
  "parties": [
    {"mailto": "chair.one@example.org", "name": "Chair One", "role": "chair"},
    {"mailto": "chair.two@example.org", "name": "Chair Two", "role": "chair"},
    {"name": "IETF Attendees", "role": "attendee"}
  ],
  "dialog": [
    {"type": "recording", "start": "2026-07-22T12:00:00+00:00", "parties": [0, 1, 2],
     "mediatype": "video/mp4", "url": "https://youtu.be/EZqkTS4_vTc", "duration": 5400}
  ],
  "attachments": [
    {"purpose": "meeting_metadata", "encoding": "json", "mediatype": "application/json",
     "body": "{\"ietf_meeting_number\": 126, \"location\": \"Vienna, AT\", \"working_group\": \"vcon\", ...}",
     "start": "2026-11-15T03:21:51.658758+00:00", "party": 0, "session": 0},
    {"purpose": "ingress_info", "encoding": "json", "mediatype": "application/json",
     "body": "{\"source\": \"ietf2vcon\", \"converter_version\": \"0.3.0\", ...}",
     "start": "2026-11-15T03:21:51.658758+00:00", "party": 0, "session": 0},
    {"purpose": "lawful_basis", "encoding": "json", "mediatype": "application/json",
     "body": "{\"lawful_basis\": \"legitimate_interests\", ...}",
     "start": "2026-11-15T03:21:51.658758+00:00", "party": 0, "session": 0},
    {"purpose": "agenda", "mediatype": "text/plain", "filename": "agenda-126-vcon-01.txt",
     "url": "https://www.ietf.org/proceedings/126/agenda/agenda-126-vcon-01.txt",
     "content_hash": "sha512-3C3asfKtVD6w93Od...",
     "start": "2026-11-15T03:21:51.658758+00:00", "party": 0, "session": 0},
    {"purpose": "agenda", "encoding": "json", "mediatype": "application/json",
     "body": "{\"url\": \"https://datatracker.ietf.org/meeting/126/session/vcon/\", \"mimetype\": \"text/html\", \"filename\": null, \"title\": \"VCON Agenda\"}",
     "start": "2026-11-15T03:21:51.658758+00:00", "party": 0, "session": 0}
  ],
  "analysis": [
    {"type": "wtf_transcription", "dialog": 0, "vendor": "youtube", "product": "auto-captions",
     "encoding": "json", "mediatype": "application/json", "body": "{\"transcript\": {...}, \"segments\": [...], ...}"},
    {"type": "report", "sessions": 0, "vendor": "ietf2vcon", "product": "chair-determination",
     "schema": "chair-determination-v1", "encoding": "json", "mediatype": "application/json",
     "body": "{\"outcome\": \"named\", \"basis\": \"live-roles\", ...}"}
  ],
  "created_at": "2026-08-11T18:52:12.331574+00:00",
  "sessions": [
    {"start": "2026-07-22T12:00:00+00:00", "duration": 5400, "parties": [0, 1, 2], "dialog": [0]}
  ],
  "subject": "IETF 126 - VCON Working Group Session",
  "extensions": ["lawful_basis", "CC", "wtf_transcription"]
}
```

What each part is, using the example:

- **`sessions`** has exactly one Session in every file. `start` and `duration` come from the meeting's official schedule (see [Session times](#session-times)). `parties` lists the receivers of the dialog, here every party. `dialog` lists the indexes of the recording Dialogs the session contains. A Session with no `start` means the session has no time in the official schedule; the `status` in `meeting_metadata` says whether it was cancelled or did not meet. A Session with no recording has no `dialog` member.
- **`parties`** lists the chairs first, then one attendees party. The chairs are the people who held the chair role at the time of the session, and there are none when that cannot be established, in which case the attendees party is first. The first party is the implied originator of the session in core-05, so `originator` is never written. `role` is a parameter of the contact-center extension, whose token is `CC`, which is why `extensions` lists `CC`.
- **`dialog`** holds the recording as a Dialog of type `recording` with a `url` and `mediatype` and no `content_hash` (see [Media references](#media-references)). Its `start` is the session's official start, and `parties` lists every party. A video's `duration` is the recording's own length, which differs from the session's `duration` in 514 files; an audio recording's `duration` is the session's in 423 of 425 files (the other two belong to cancelled sessions with no official duration, and keep their published duration).
- **`attachments`** are scoped to the Session: `session` is `0` and `party` is `0` (the first party, as core-05 requires a party). A `start` on an attachment is the time the file was converted, not the time of the session or of the document. The attachments in order:
  - `meeting_metadata`: a JSON string with the meeting number, location, group, session id, `status`, room, start time, duration, and `materials`. `ietf_meeting_number` is an integer for numbered meetings and the interim's name (a string) for the two interims. `status` is the Datatracker's scheduling status for the session (`sched`, `notmeet`, `canceled`, and so on). `start_time`, `duration_seconds` and `room` are `null` for a session with no official time. `materials` lists, for each hashed material, its `filename`, `url`, `title` (where known) and `datatracker_url`, because core-05 has no `meta` parameter on an attachment to carry them. A `recording_note` member is present in 12 files, where the recording may belong to another session of the group or its start time is unverified (see [Known limitations and deviations](#known-limitations-and-deviations)).
  - `ingress_info`: which tool and version produced the file (`ietf2vcon` 0.3.0), when, and for which meeting, group and session index.
  - `lawful_basis`: see [Lawful basis](#lawful-basis).
  - Materials: slides, agendas, minutes, bluesheets, chat logs and other documents. A material is either a **hashed external reference** (`url`, `content_hash`, `mediatype`, `filename`) or an **unhashed reference** (an inline JSON `body` with `url`, `filename` and `title`, usually a `mimetype`, and no hash). See [Why materials point at www.ietf.org](#why-materials-point-at-wwwietforg-rather-than-the-datatracker).
- **`analysis`** holds the transcript (type `wtf_transcription`, pointing at the recording with `dialog`) and, in every file, one entry of type `report` that records how the chairs were determined (`sessions` points at the Session). Select transcripts by `type`, not by position. The bodies of both are strings of serialized JSON.
- **`extensions`** lists the extension tokens defined by their drafts (`lawful_basis`, `CC` and, in files that have a transcript, `wtf_transcription`). Each draft requests registration of its token; core-05's extension registry has no values yet.

Every inline `body` is a string holding serialized JSON, with `encoding` `json`. Parse the string once more. Strictly, core-05's `json` encoding means the body is the JSON value itself, not a string of it (the core-05 schema accepts a string, which is a JSON value, so the validator does not flag it); see [Known limitations and deviations](#known-limitations-and-deviations). Every inline entry carries a `mediatype`.

### Why materials point at www.ietf.org rather than the Datatracker

A material attachment carries `url` + `content_hash`, so the URL has to return the hashed bytes. The Datatracker's `/meeting/N/materials/<doc>` is a display endpoint, not a file: it renders Markdown agendas as HTML pages and converts PowerPoint decks to PDF, so a hash of the published file cannot describe what it serves. `https://www.ietf.org/proceedings/{meeting}/{type}/{filename}` serves each file verbatim, is the tree `rsync.ietf.org::proceedings` mirrors, and is reachable by scripted clients across all eras. The Datatracker page is still the right thing for a human to open, so it is kept as `datatracker_url` in the `materials` list of the `meeting_metadata` body.

Landing pages (the session page, the collaborative notes, a draft's own page) are mutable HTML and carry no hash. They stay unhashed references, since a `content_hash` on them would assert an integrity guarantee that does not hold. The same is true of a material whose verbatim file could not be read. See [Known limitations and deviations](#known-limitations-and-deviations) for the counts.

## Session times

A Session's `start` and `duration`, a recording Dialog's `start`, and the room in `meeting_metadata` come from the meeting's official schedule in the Datatracker and nowhere else. A session that is not in the official schedule, or that has a status other than `sched`, has no `start`, no `duration` and no room, and its `meeting_metadata` has `null` for `start_time`, `duration_seconds` and `room`. The `status` says why: `notmeet`, `canceled`, `deleted`, `schedw`, `disappr` or `resched`. Eighteen sessions with status `sched` have no time because they are not in the official schedule. Two files have no status recorded at all.

Release 2.1.0 took the first schedule assignment the converter found for a session, which was often an assignment in a draft schedule and not the official one. In 3.0.0, compared with 2.1.0:

| Session start | Files |
|---|---|
| Same time (4,771), or no time in both releases (994) | 5,765 |
| Moved | 2,293 |
| of which moved by more than 24 hours | 1,508 |
| Removed (the session is not in the official schedule or did not meet) | 123 |
| Added where 2.1.0 had none | 0 |

The recording Dialog takes the session's official start: 2,172 recording starts changed and 107 recording durations changed. No recording link, media type or transcript changed.

The corrections were checked against the Datatracker's official agenda for 14 meetings (70, 75, 86, 90, 92, 97, 100, 108, 109, 115, 122, 123, 126 and the second VCON interim): the starts of 1,521 sessions agreed in every case.

A session with no time is dated for the lawful basis and the chair determination by its meeting's first day, as recorded in the Datatracker. In 36 meetings the earliest session start in the files is before that date; the earliest session is the `tools` group's in 27 of them and the `hackathon` group's in 8.

## Chairs

Chair parties name the people who held the chair role **at the time of the session**, not today's chairs. When that cannot be established, the file names no chairs and has only the attendees party. Absence of a claim is preferable to a false one.

The date used is the session's start. For a session with no time, it is the end of its meeting's first day, UTC. A person counts as a chair if they held the Datatracker's chair role for the group on that date.

### How the chairs are determined

The Datatracker keeps a live record of each group's roles and a dated history of changes to the group. History records are timestamped snapshots of a group; a snapshot is in force from its timestamp until the next one.

1. If the group was **not edited after the session** (its last edit is before the session), the live chair roles are the chairs. Basis `live-roles`.
2. Otherwise, the history snapshot in force at the session gives the chairs. Basis `history-snapshot`. If the roles or snapshot used list no chairs, the file names none and the basis is `no-chairs-recorded`.
3. The Datatracker's role history begins on **9 December 2011**. A session before the earliest history record of its group, for a group that was edited later, cannot be resolved, and **no chairs are named**. This is every session before IETF 83 (March 2012) and two sessions at IETF 91. Basis `no-history`.
4. Where several history records share one timestamp and hold different chair people, the timestamp says nothing about when each served (older tools wrote a record per change without moving the group's timestamp). No chairs are named. Basis `ambiguous-history`.
5. For an ambiguous session, chairs are named only if **three independent sources** name exactly the same people: the write order of the history records (record ids are handed out in write order across all groups, so each record can be dated from other groups' records), the group's page in the meeting proceedings, and a chairs line in the session's own minutes or agenda document. This confirmed 89 sessions (IETF 83 to 94). Basis `minutes-confirmed`. Sources that do not all agree leave the session with no chairs. Their evidence is kept in the record (see below).

Chairs are never inferred from who speaks, and a chair's absence, remote participation or announced departure noted in minutes leaves the person a role holder.

Compared with 2.1.0, the chairs of 6,488 files are identical (2,002 of them have none in both releases), 223 are the same people in another order, 479 are the same people with a different email address, 467 are different people (5 of them only in the capitalisation of a name), 520 files no longer name chairs, and 4 newly name chairs.

### The chair determination record

Every file has one `analysis` entry of type `report`, `product` `chair-determination` and `schema` `chair-determination-v1`. Its `body` is a JSON string with these members, in this order, omitted when they do not apply:

| Member | Meaning |
|---|---|
| `outcome` | `named` (the chairs are in `parties`) or `none` |
| `basis` | One of the six values below |
| `as_of`, `as_of_basis` | The date the determination is for. `as_of_basis` is `session-start`, or `meeting-first-day` (23:59:59 UTC on the first day) for a session with no time |
| `datatracker` | `group` (acronym), `group_last_edited`, and where a history snapshot was used, `snapshot` and `snapshot_time` |
| `chairs` | The chairs named in `parties` (`name`, `mailto`), when `outcome` is `named` |
| `candidates` | Evidence considered (below) |
| `reason` | A sentence explaining a `none` outcome or a `minutes-confirmed` one |
| `note` | Only for `minutes-confirmed`: where the addresses come from, and any absence or departure noted in the minutes |

The `basis` values and the number of files with each:

| `basis` | Files | Outcome | Meaning |
|---|---|---|---|
| `history-snapshot` | 5,055 | named | A dated history record in force at the session gave the chairs |
| `no-history` | 1,965 | none | The session is older than the group's history, and the group was edited later |
| `live-roles` | 515 | named | The group was not edited after the session, so the live roles apply |
| `ambiguous-history` | 437 | none | History records share one timestamp with different chairs, and the sources did not all agree |
| `no-chairs-recorded` | 120 | none | The Datatracker record for that time lists no chairs |
| `minutes-confirmed` | 89 | named | Ambiguous history, resolved because three sources name the same people |

`candidates` is present in 2,741 files: 2,228 of the 2,522 files whose outcome is `none` (the other 294 have nothing to list), all 89 `minutes-confirmed` files, and 424 of the 433 files whose named chairs differ from 2.1.0 (the 4 newly named and the 5 whose names differ only in capitalisation have none). It is a list of entries, each with a `source`:

| `source` | Meaning | Other members |
|---|---|---|
| `previous-release` | The chairs listed in the 2.1.0 file | `version`, `chairs` |
| `history-write-order` | The chairs by the write order of the history records | `confident` (all candidate sets agree), `snapshot`, `written_between` (the window in which that record was written), `chairs` |
| `tied-history-records` | The distinct chair sets of the history records that share one timestamp | `timestamp`, `chair_sets` |
| `proceedings-page` | The chairs on the group's page in the meeting proceedings | `url`, `chairs` |
| `session-minutes` | What a minutes or agenda document says | `document`, `chairs_named` (the document's chairs line, names as written), `remarks` (lines saying a chair was absent, remote, acting or leaving) |

**Candidates are recorded evidence, not established facts.** They are the people a source named, kept so that a reader can see why a session has no chairs, or why they changed since 2.1.0. They may name people who held the role at some other time, and **the record names past role holders who are not parties** in the file.

A worked example, from `ietf86/ietf86_json_22585.vcon.json` (abridged, name and address replaced). The file has no chair party, only the attendees party. Its record:

```json
{
  "outcome": "none",
  "basis": "ambiguous-history",
  "as_of": "2013-03-11T17:00:00Z",
  "as_of_basis": "session-start",
  "datatracker": {"group": "json", "group_last_edited": "2015-03-23T20:29:07Z"},
  "candidates": [
    {"source": "previous-release", "version": "2.1.0",
     "chairs": [{"name": "Chair One", "mailto": "chair.one@example.org"}]},
    {"source": "history-write-order", "confident": true, "snapshot": 669,
     "written_between": ["2013-05-01T18:43:06Z", "2013-05-01T18:43:06Z"],
     "chairs": [{"name": "Chair One", "mailto": "chair.one@example.org"}]},
    {"source": "tied-history-records", "timestamp": "2013-01-31T19:24:58Z",
     "chair_sets": [[], [{"name": "Chair One", "mailto": "chair.one@example.org"}]]},
    {"source": "proceedings-page", "url": "https://www.ietf.org/proceedings/86/json.html",
     "chairs": [{"name": "Chair One", "mailto": "chair.one@example.org"}]}
  ],
  "reason": "History records share one timestamp with different chairs, and the session minutes or agenda have no explicit chairs line."
}
```

Two history records share the timestamp `2013-01-31T19:24:58Z`, one with no chairs and one with Chair One, so the timestamp does not say which applied at the session (`tied-history-records`). The write order and the proceedings page both point to Chair One, and so did the 2.1.0 file. But the third source, the session's own minutes or agenda, has no chairs line to confirm it, so the rule for `minutes-confirmed` is not met and the file names no chairs. The evidence stays in the record.

Limits to keep in mind:

- The email address recorded for a role can change without any trace in the history, so an address can be a later one for the same person. 479 files differ from 2.1.0 only in addresses.
- A change of chair made at or around the meeting itself can be recorded in the Datatracker late, so the person named for a session close to a change may be the one before or after it.
- For `minutes-confirmed` files the addresses come from the proceedings page, which reflects the weeks after the meeting.
- Meetings from IETF 99 onward have no proceedings pages, so an ambiguous session there stays without chairs.

The record applies to the file as it was written. If the files are converted again, the chair determination has to be run again; see [How the dataset is produced](#how-the-dataset-is-produced).

## Lawful basis

Every file has a `lawful_basis` attachment, per [draft-howe-vcon-lawful-basis](https://datatracker.ietf.org/doc/draft-howe-vcon-lawful-basis/). Its body states:

- `lawful_basis`: `legitimate_interests`, and `expiration`: `2099-12-31T23:59:59+00:00` (a far-future value).
- `purpose_grants`: five purposes, `recording`, `transcription`, `publication`, `archival` and `analysis`, each with `granted: true` and a `granted_at` date.
- `terms_of_service`: `https://www.ietf.org/about/note-well/`, and `metadata` naming it the IETF Note Well, the jurisdiction `IETF` and the controller (the IETF), with a note that participation in IETF meetings constitutes agreement to the Note Well, which permits recording, transcription and public archival of proceedings.
- `proof_mechanisms`: entries of type `external_system`. The first points to the Note Well. The second points to the Datatracker session record, including the bluesheets attendance document the vCon references.

The attachment is scoped to the Session (`session` `0`, `party` `0`). `granted_at` is the Session's `start`, or, for a session with no time, the meeting's first day at 00:00:00 UTC. The second proof entry is written only when the session was held (status `sched`, or no status recorded) and the file references a `bluesheets` document: 3,573 files have two entries and 4,608 have one. A session that did not meet does not claim to have been held.

This describes what the attachment says. It records a published policy; it holds no record of any individual's agreement, and this README makes no statement about whether any particular use of the data is permitted.

## Transcripts

There are 4,061 transcripts in [WTF](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/) format, covering every session with a recording except the gaps noted under [Coverage gaps](#coverage-gaps). They are stored in one of two ways.

**IETF 126 and the two interims are inline** (142 files). The transcript body sits in the vCon, so each file is self-contained and needs no network access to read.

**IETF 90-125 are externally referenced** (3,919 files). A WTF body runs 200-400 KB, so inlining all of them would make this repository well over a gigabyte. The core spec anticipates this: the Analysis Object section states that it *"SHOULD contain the body and encoding parameters **or** the url and content_hash parameters."* Those meetings therefore reference their transcripts instead of carrying them. This is a real entry, from `ietf92/ietf92_6lo_23625.vcon.json`:

```json
{
  "type": "wtf_transcription",
  "dialog": 0,
  "vendor": "apple",
  "product": "speechanalyzer",
  "url": "https://github.com/vcon-dev/ietf-meeting-vcons/releases/download/transcripts-ietf92/ietf92_6lo_23625.wtf.json",
  "content_hash": "sha512-ooILscbLPWFJbKIpK7TpK_kS4OvZQ8g4ZLPoa6bKBaQV9okQBvOYCEJ5Bc-KCO8rvuSFZvD-kDNrD9YhXWmncw",
  "mediatype": "application/json"
}
```

The `content_hash` is a SHA-512 digest of the exact bytes served, formatted as `sha512-` plus unpadded base64url. The vCons in this dataset are not signed (see [Known limitations and deviations](#known-limitations-and-deviations)), so the hash protects against a corrupted or substituted body only as far as you trust the vCon file you read it from. This keeps the repository at about 145 MB of vCon files.

The transcripts were not changed in 3.0.0: all 4,061 bodies and hashes are the same as in 2.1.0. The 142 inline entries gained a `mediatype`.

### Reading a transcript either way

Transcripts are the analysis entries of type `wtf_transcription`. Select by `type`: every file also has an entry of type `report`.

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

Always check the hash before trusting a fetched body. That check is the only thing separating an external reference from an unverified download.

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

`transcripts/` is gitignored. Publishing a meeting means uploading that directory's `.wtf.json` files as assets on a release tagged `transcripts-ietf<N>`, matching the URL prefix baked into the vCons. The script only touches entries of type `wtf_transcription`.

## Data Sources

All data is sourced from public IETF resources:

- **Session metadata, schedules, statuses, roles**: [IETF Datatracker API](https://datatracker.ietf.org/api/)
- **Recordings**: the IETF's published recording links (`youtube.com` and `youtu.be` video, and `ietf.org` audio)
- **Transcripts**: three provenances, distinguished by `vendor` and able to coexist in `analysis`:
  - `youtube`: auto-generated captions published with the video, the default where they exist (3,271 files; `product` is `auto-captions`, or `auto-generated` in two).
  - `whisper`: local speech recognition (IETF 125, 112 files), with word-level timing.
  - `apple` (`product: speechanalyzer`): on-device speech recognition on macOS, used where no captions exist: the audio era (IETF 90-94) and the caption-less 2016-2017 videos (IETF 95-98), 678 files. Segment-level timing, no per-word timestamps or confidence.
- **Materials**: the IETF proceedings tree (`rsync.ietf.org::proceedings`), which serves each document verbatim
- **Chairs**: the Datatracker's group, role and history records, the proceedings pages, and session minutes, as described under [Chairs](#chairs)

### Media references

`content_hash` on an external `attachment` or `analysis` reference lets a consumer verify the bytes it fetches match what was hashed at dataset-build time. Recording `dialog` entries carry no `content_hash`, because no stable bytes can be obtained for the URL. This follows the working group's direction on [core draft issue 40](https://github.com/ietf-wg-vcon/draft-ietf-vcon-vcon-core/issues/40): a `url` without a `content_hash` is allowed on a Dialog, Attachment or Analysis when the producer cannot obtain stable bytes for it, with no marker field. The [`vcon`](https://pypi.org/project/vcon/) Python library accepts it from 1.0.6. The core-05 text (Section 2.4, and the `url` → `content_hash` dependency in Appendix C) still says MUST until the working group revises it, so a validator that follows the draft text literally reports these links. The recording links are of two kinds, and no hash is written for either:

- **Video recordings** (3,654 files, IETF 90-126 and the interims). The link is a `youtube.com` or `youtu.be` address, which serves an HTML page, not the media file, so fetching it and hashing the response would hash a web page, not a recording.
- **Audio recordings** (425 files: 424 in IETF 90-94 and one in IETF 86). The `ietf.org/audio/...` MP3 URLs redirect to a login page (checked on one of them in November 2026), and a plain, polite fetch gets no file. These URLs are left as they are, unhashed, until the IETF publishes a public replacement. One of them, in `ietf86/ietf86_sdnrg_22511.vcon.json`, is an `http` link, not `https`.

Every other external reference (slides, agendas, minutes, bluesheets, chat logs, transcript bodies) carries a hash. Where a material could not be hashed, the file holds an unhashed reference instead (see [Known limitations and deviations](#known-limitations-and-deviations)).

## Statistics

| Metric | Value |
|--------|-------|
| Meetings | 61 numbered (IETF 66-126, consecutive) plus 2 VCON interims |
| Total vCons | 8,181 |
| Sessions with a recording | 4,079 (3,654 video, 425 audio) |
| Transcripts | 4,061 (142 inline, 3,919 externally referenced) |
| Transcript coverage | 4,061 of 4,079 recordings (99.6%) |
| Hashed material references | 64,032 |
| Unhashed references (session, notes and draft pages, and 9,048 material pages) | 32,876 |
| Files that name chairs | 5,659 |
| Date Range | July 2006 - September 2026 |
| vCon file size | about 145 MB (plus the transcript bodies published separately) |

### Coverage gaps

18 of 4,079 recorded sessions have no transcript. Nearly all are videos that have since been **removed or made private**, so there is no longer any source to transcribe; a few are recordings that are silent or non-speech.

**IETF 66-89 has no recordings by design.** The IETF did not publish session recordings in that era, so those 2,887 vCons are materials-only (apart from the one audio link above). They are not empty: they carry 20,855 slide decks, 2,834 agendas and 2,747 minutes (a median of 7 documents per session), plus a session-page link and a notes link in every file.

`ietf107` (March 2020, the first virtual meeting) is a special case within the recorded era: it has **129 vCons and no recordings at all**, because the Datatracker holds no recording documents for it. Its sessions are materials-only.

**2,522 files name no chairs** (see [Chairs](#chairs)). **1,117 files have no session start**: 1,098 because the session's status is not `sched`, 18 because a `sched` session is not in the official schedule, and one with no status recorded. See [Session times](#session-times).

## Known limitations and deviations

These are known properties of the data, listed so that nobody has to find them by accident.

**Coverage**

- **One file per group per meeting.** A file is named for one session (`session_id`), but its materials come from all the group's sessions at the meeting, and its recording may come from another session of the same group. Twelve files carry a `recording_note` in the `meeting_metadata` body: 7 where the named session did not meet and the recording belongs to another session of the group, 3 where the session has no time in the official schedule (the recording start is unverified), and 3 where the recording's file name indicates a different session of the same group (one file is in both the first and the last group). In the two scheduled files of the last kind (`ietf90_mpls_23162` and `ietf90_tsvwg_23197`), the recording `start` is the named session's, not the recording's.
- **Second and later sessions of a group have no file.** Their recordings and transcripts are not in the dataset.
- **About 1,100 files describe sessions that did not meet**: 1,098 files have a status other than `sched` (768 `notmeet`, 233 `canceled`, 76 `deleted`, 18 `schedw`, 2 `disappr`, 1 `resched`), and two have no status recorded. Seven of them still have a recording, which belongs to another session of the group.
- **2,522 files name no chairs.** The reason is in each file: 1,965 `no-history`, 437 `ambiguous-history`, 120 `no-chairs-recorded`.
- **10 recordings have an unverified start time**: the 7 above whose session did not meet, and 3 sessions with no official time. Their Dialog `start` is as published before, not taken from the official schedule.
- No Event objects are written: the Datatracker gives no join or drop times.

**Links and hashes**

- **Unhashed material references: 32,876.** They are 8,181 session pages, 8,181 collaborative-notes pages and 7,466 draft pages, which are mutable landing pages with no file to hash, and 9,048 references to a Datatracker material page whose verbatim file was not hashed (7,123 of them in IETF 66-89). The files do not record the reason for each one; the 3.0.0 conversion log counted 898 materials that the servers reported as not available. The `mimetype` inside such a reference is a label taken from the file name or from 2.1.0, not checked by fetching the page, and it is absent from 4,313 of the 32,876 references because nothing known says what the page is.
- **Recording links have no content hash** (4,079), as described above. This follows the working group's direction on core issue 40.
- **One recording link is `http`**, not `https` (`ietf86/ietf86_sdnrg_22511.vcon.json`), where core-05 requires HTTPS.
- **Links to collaborative notes** for older meetings no longer lead to a note: the two older meetings checked (IETF 70 and 92) redirect to the notes service's start page. The link for IETF 126 resolves.

**Deviations from the specifications**

- **Inline bodies are strings of serialized JSON.** Every inline `body` (57,419 attachments, 8,181 lawful basis bodies among them, and every inline analysis entry) is a string holding JSON, with `encoding` `json`. Core-05 says `json` means the body is the JSON value itself, so strictly the body should be the object, not a string of it. The core-05 schema accepts a string, so the validator does not flag it.
- **One recording link is `http`** (see above). Core-05 requires HTTPS.
- **Recording links have no `content_hash`** (4,079). The core-05 text still requires a hash with a `url`, but this follows the working group's direction on [core issue 40](https://github.com/ietf-wg-vcon/draft-ietf-vcon-vcon-core/issues/40), which allows a `url` without a hash when stable bytes cannot be obtained, and `vcon` 1.0.6 accepts it (see [Media references](#media-references)). It is listed here only until the draft text is revised.
- **Parties carry no `validation`**, which core-05 says should be present when a party has a `name`.
- **Recording media types are `video/mp4` and `audio/mpeg`**, the registered names. The list core-05 suggests for Dialog `mediatype` has `video/x-mp4` and `audio/x-mp3`, and does not include them.
- **Attachment `start` values are the conversion time** of the file (14 or 15 November 2026 for every file), not the time of the session or the document. Core-05 defines `start` on an attachment as when it was sent or exchanged.

**Lawful basis attachments against draft-howe-vcon-lawful-basis-02.** Each of these holds in all 8,181 files:

- The attachment has no `dialog` member. Section 5.1 lists one (and `party`). The files scope the attachment by `session`, which that revision of the draft predates, as it refers to core-02.
- The `body` is a string of serialized JSON, where section 5.2 asks for the JSON object.
- Each proof entry (11,754 in all) has `mechanism_type` and `description`, where section 5.2.4 requires `proof_type`, `timestamp` and `proof_data`.
- The attachments are not signed (section 10.3 asks for integrity protection with vCon signing, and none of the vCons is signed), and nothing binds an attachment to its vCon against replay (section 10.2 asks for cryptographic binding).

**Data conventions**

These are choices of this dataset that are not departures from a specification:

- **Every `subject` of a numbered meeting reads "IETF N - GROUP Working Group Session"**, including for research groups, the Hackathon and the Secretariat. The two interim subjects read "IETF VCON Working Group Interim Session - <interim name>".
- **`ietf_meeting_number`** in `meeting_metadata` is an integer for numbered meetings and a string (the interim's name) for the two interims.
- **Transcripts use the analysis type `wtf_transcription`.** The transcription extension requires this type, and it is not in core-05's suggested list (`report`, `sentiment`, `summary`, `transcript`, `translation`, `tts`). The chair record uses `report`.

## Usage Examples

The examples run from the root of a checkout and use real files.

### Python

```python
import json

# Load a vCon
with open("ietf121/ietf121_quic_33502.vcon.json") as f:
    vcon = json.load(f)

# Get session info
session = vcon["sessions"][0]
print(f"Subject: {vcon['subject']}")
print(f"Session start: {session.get('start', 'no time in the official schedule')}")
for index in session.get("dialog", []):
    print(f"Recording: {vcon['dialog'][index]['url']}")

# Chairs
for party in vcon["parties"]:
    if party.get("role") == "chair":
        print(f"Chair: {party['name']}")
```

For transcripts see [Reading a transcript either way](#reading-a-transcript-either-way); meetings 90-125 reference them by URL rather than carrying them inline.

**Reading the chair determination record**

```python
import json

def chair_record(vcon):
    """The body of the chair determination entry, or None."""
    for analysis in vcon.get("analysis", []):
        if analysis.get("type") == "report" and analysis.get("product") == "chair-determination":
            return json.loads(analysis["body"])

with open("ietf86/ietf86_json_22585.vcon.json") as f:
    vcon = json.load(f)

record = chair_record(vcon)
print(record["outcome"], record["basis"], "as of", record["as_of"])
for chair in record.get("chairs", []):
    print("chair:", chair["name"])
for candidate in record.get("candidates", []):
    print("evidence from:", candidate["source"])
print(record.get("reason", ""))
```

**Counting files by chair determination basis**

```python
import collections, glob, json

counts = collections.Counter()
for path in glob.glob("*/*.vcon.json"):
    with open(path) as f:
        vcon = json.load(f)
    for analysis in vcon["analysis"]:
        if analysis.get("product") == "chair-determination":
            counts[json.loads(analysis["body"])["basis"]] += 1

for basis, n in counts.most_common():
    print(f"{n:5d}  {basis}")
```

**Finding sessions that did not meet, and checking a material's hash**

```python
import base64, glob, hashlib, json, urllib.request

def attachment_body(vcon, purpose):
    for attachment in vcon["attachments"]:
        if attachment["purpose"] == purpose:
            return json.loads(attachment["body"])

# Sessions of IETF 126 with a status other than "sched"
for path in sorted(glob.glob("ietf126/*.vcon.json")):
    with open(path) as f:
        vcon = json.load(f)
    status = attachment_body(vcon, "meeting_metadata").get("status")
    if status != "sched":
        print(path, status)

# Check one hashed material against its content_hash
with open("ietf126/ietf126_vcon_35521.vcon.json") as f:
    vcon = json.load(f)
for attachment in vcon["attachments"]:
    if attachment["purpose"] == "agenda" and "content_hash" in attachment:
        raw = urllib.request.urlopen(attachment["url"]).read()
        algorithm, _, expected = attachment["content_hash"].partition("-")
        digest = hashlib.new(algorithm, raw).digest()
        actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        print(attachment["filename"], "hash ok" if actual == expected else "hash MISMATCH")
```

### jq (Command Line)

```bash
# Get all recording URLs from a meeting
jq -r '.dialog[0].url // empty' ietf121/*.vcon.json

# Extract transcript text (IETF 126, inline bodies; note body is a JSON string)
jq -r '.analysis[] | select(.type=="wtf_transcription") | .body | fromjson | .segments[].text' \
    ietf126/ietf126_vcon_35521.vcon.json

# List transcript URLs for an externally referenced meeting
jq -r '.analysis[]? | select(.type=="wtf_transcription") | .url // empty' ietf121/*.vcon.json

# Count the chair determination bases in one meeting
jq -r '.analysis[] | select(.product=="chair-determination") | .body | fromjson | .basis' \
    ietf86/*.vcon.json | sort | uniq -c

# Files of IETF 126 whose session has no start
jq -r 'select(.sessions[0].start == null) | input_filename' ietf126/*.vcon.json

# List all groups in a meeting
ls ietf121/*.vcon.json | sed 's/.*ietf121_\(.*\)_.*/\1/' | sort -u
```

## How the dataset is produced

The files are generated with [ietf2vcon](https://github.com/vcon-dev/ietf2vcon) (version 0.3.0 for 3.0.0, recorded in each file's `ingress_info`), then changed by two post-processing tools, then validated. The tools are `scripts/chair_determination.py` and `scripts/reference_types.py`. Two more scripts, `scripts/check_chair_determination.py` and `scripts/check_reference_types.py`, check their work without using their code. All four need only Python 3.11 or later and git. [scripts/README-tools.md](scripts/README-tools.md) documents every option, and `--help` on each script lists them. The order of the steps matters.

**Installing the converter.** ietf2vcon is not on the Python package index. Install it from its source repository at its 0.3.0 release tag:

```bash
pip install "git+https://github.com/vcon-dev/ietf2vcon.git@v0.3.0"
```

It depends on the `vcon` library at version 1.0.0 or later (`vcon>=1.0.0,<2`).

1. **Build the proceedings mirror.** The converter reads materials from a local copy of the IETF proceedings tree instead of making tens of thousands of requests, and hashes the files it reads. Copy each numbered meeting into `<mirror>/proceedings/<N>/` with plain `rsync`:

   ```bash
   for n in $(seq 66 126); do
       mkdir -p mirror/proceedings/$n
       rsync -a --delete rsync.ietf.org::proceedings/$n/ mirror/proceedings/$n/
   done
   ```

   For IETF 66 to 126 this is about 57 GB in about 637,000 files (measured when the mirror for 3.0.0 was built). `ietf2vcon sync --meeting <N> --mirror-dir ./mirror` does the same for one meeting, but takes numbered meetings only, so the materials of the two interims are fetched over HTTP. The converter is given `--rsync-mirror ./mirror`, and the chair tool is given `--proceedings ./mirror/proceedings`.

2. **Convert every meeting, one at a time, keeping a log and a completion marker.** The chair determination tool needs two things for each meeting: a log, `logs/convert-<N>.log`, and a marker, `state/<N>.done`, for each meeting that converted cleanly. The converter does not write either; a shell redirection and `touch` do. The 3.0.0 files were produced by a batch driver (a shell script, not published) that ran the converter on meetings 66 to 126 and the two interims in turn, read each log for errors, retries and warnings, ran the validator, and wrote the marker only for a clean meeting. Without a driver, for each meeting:

   ```bash
   mkdir -p logs state
   NO_COLOR=1 ietf2vcon convert-all --meeting 126 --existing-only --no-transcript --no-video \
       --rsync-mirror ./mirror --output-dir ./ietf126 --parallel 1 -v > logs/convert-126.log 2>&1
   touch state/126.done          # only after a clean conversion
   ```

   - **Use `--parallel 1`.** The chair tool reads each session's chair lookup from the converter's log and attaches every line to the most recent "Converting IETF" line, so parallel conversion interleaves the lines and attaches them to the wrong session. `-v` puts those lines in the log, and `NO_COLOR=1` keeps the log free of colour codes. The tool was built against the log wording of converter 0.3.0, and a converter that words these lines differently will not work with it.
   - **A clean conversion** is one that exits with status 0, converts every session, and has no traceback in the log. Write `state/<N>.failed` instead for a meeting that failed: the chair tool refuses to apply while a `.failed` marker has no `.done`.
   - **Interims** use their names: `logs/convert-interim-2026-vcon-01.log`, `state/interim-2026-vcon-01.done`, and `--meeting interim-2026-vcon-01`.
   - `--existing-only` regenerates only sessions that already have a file, keeping the file name and the `uuid` and `created_at`, and creates none. `--no-transcript` and `--no-video` leave the existing recording and transcript in place: the converter carries them over from the previous file, takes the session's official start for the recording, and refuses to replace a file whose recording or analysis entries would be lost. A file is written only when the result is complete: any Datatracker read that still fails after retries fails the session and leaves its file untouched. The converter keeps to 8 requests per second by default and honours `Retry-After`.

3. **Commit the converter's output.** `git add -A && git commit`. The chair tool does not need this, but `scripts/check_chair_determination.py` compares the finished tree with the commit holding the files as the converter wrote them, and 3.0.0 was built this way.

4. **Chair determination: plan, apply, verify.** The converter names the chairs it can establish from the Datatracker's history and writes none where the history is ambiguous. `chair_determination.py` writes the [chair determination record](#the-chair-determination-record) into every file of a completed meeting, adds the chairs of the sessions confirmed by three sources, and checks, for every ordinary session, that its own recomputation of the converter's rule gives exactly the chairs in the file and agrees with the converter's log line for that session; a file where they disagree is listed in a report and not written. One apply writes everything, including the evidence lists in the 89 minutes-confirmed records; no second run is needed.

   ```bash
   COMMON="--previous-rev 3b17026b --proceedings ./mirror/proceedings --log-dir ./logs --state-dir ./state"

   # plan: reads the files, writes only reports
   python scripts/chair_determination.py $COMMON
   # apply, then validate every touched meeting with the converter's validator
   python scripts/chair_determination.py --apply --allow-production --output-root . \
       --converter /path/to/ietf2vcon $COMMON
   # verify: reads only, exits 1 on any difference
   python scripts/chair_determination.py --verify $COMMON
   # independent check against the commit made in step 3
   python scripts/check_chair_determination.py --rev <commit of the converter output>
   ```

   The four options in `COMMON` are required in every mode:
   - `--previous-rev` names the commit of the previous release (`3b17026b` for 3.0.0), and `dataset.json` at that commit must say the version given by `--previous-version` (default `2.1.0`). Give the same `--previous-rev` and `--previous-version` every time the tool is run for a release: a record states the differences from the previous release, and a later run with another previous release plans a different record and is refused ("differs from the plan") unless `--update-records` is given.
   - `--proceedings` is the proceedings mirror from step 1, `--log-dir` holds the logs from step 2 and `--state-dir` the markers.
   - `--converter` is a checkout of ietf2vcon with its virtual environment in `.venv`. It is required with `--apply` unless `--no-validate` is given.

   Without `--meetings`, the tool processes every meeting that has a `.done` marker. The tool takes the Datatracker's group, history and role tables from an on-disk cache (`--cache-dir`, by default under `$XDG_CACHE_HOME` or `~/.cache`, never inside the repository), and `--seed-cache` reuses another cache read-only. With an empty cache, the first run reads about thirty whole-table pages of the Datatracker API, one request at a time, one second apart, capped by `--max-requests` (default 200), and stops for ten minutes after any HTTP 429; with a warm cache it makes no request.

   `--apply` on a git working tree needs `--allow-production`. It then refuses to start when a process whose command line contains `ietf2vcon convert` is running, when the process named by `--lock-file` (if given) is alive, when a requested meeting has no `.done`, or when a meeting has a `.failed` without a `.done`. It does not require a clean `git status`: it is meant to run on the converter's uncommitted output.

5. **Commit the chair pass.** `git add -A && git commit`. The reference-types tool refuses to apply to a working tree with uncommitted changes.

6. **Reference types and recording notes: plan, apply, check.** `reference_types.py` corrects the `mimetype` member inside unhashed material references and adds the `recording_note` to the 12 files described above. Of the 9,048 references to a Datatracker material page, 9,047 carried `application/pdf`, the converter's default. The tool sets the type from the file name where it has a known extension, otherwise takes the 2.1.0 value for the same `url` (a 2.1.0 value of `application/pdf` is not used, because it was the same default guess), and otherwise removes the `mimetype` member. It changes nothing else, and skips and reports any file where anything else would differ.

   ```bash
   # plan: reads only
   python scripts/reference_types.py --previous-rev 3b17026b --proceedings ./mirror/proceedings
   # copy of the tree from before the run, for the check
   mkdir -p /tmp/before && git archive HEAD | tar -x -C /tmp/before
   # apply (needs a clean git status, and no conversion or chair pass running)
   python scripts/reference_types.py --apply --allow-production --output-root . \
       --previous-rev 3b17026b --proceedings ./mirror/proceedings
   # independent check of every changed file
   python scripts/check_reference_types.py /tmp/before .
   ```

   `reference_types.py` has no `--meetings` option: it scans the whole tree and writes only the files that need a change, and a second apply changes nothing. `--proceedings` is optional here (the mirror resolved no reference in 3.0.0).

7. **Validate, then commit.** Every file is checked against the core-05 JSON Schema (Appendix C) plus rules for session wiring, attachment scope and lawful basis scoping. The recording links without a hash are counted and reported separately and do not fail a file, since they follow the working group's direction on core issue 40 (the validator, which checks the core-05 schema as written, labels them known deviations); every other schema error does:

   ```bash
   # from an ietf2vcon checkout; the pattern reaches the per-meeting directories
   .venv/bin/python scripts/validate_vcons.py /path/to/ietf-meeting-vcons --all --pattern '*/*.vcon.json'
   ```

   The validator reports 8,181 of 8,181 files passing for this release, with the 4,079 recording links without a hash listed separately (following core issue 40).

### Running a step again

- **After converting a meeting again, run the chair pass for it again (apply, then verify), then the reference pass.** The converter rebuilds `parties` without the chairs confirmed from minutes. It keeps an existing analysis (the transcript and the chair record) only when the rebuild produces no analysis of its own, so a conversion that fetches a transcript drops the record. It also writes its default reference types again. Do not rely on either being carried forward. Use the same `--previous-rev` and `--previous-version` as for the rest of the release.
- **Reproducibility.** The chair pass reads the Datatracker's group history, and a run made later reads later data (groups edited, roles changed). It will not necessarily reproduce 3.0.0 byte for byte: where the tables and the conversion log disagree, the tool reports a mismatch and writes nothing, and `updated_at` is the time of the run. The cache of Datatracker tables used for 3.0.0 is not in this repository.

### Adding a New Meeting

A new meeting has no file in the previous release, so `--existing-only` does not apply. The steps are the same, for that meeting only:

```bash
ietf2vcon sync --meeting 127 --mirror-dir ./mirror
mkdir -p logs state
NO_COLOR=1 ietf2vcon convert-all --meeting 127 --output-dir ./ietf127 --rsync-mirror ./mirror \
    --transcript-source youtube --parallel 1 -v > logs/convert-127.log 2>&1
touch state/127.done          # only after a clean conversion
git add -A && git commit -m "IETF 127, converter output"
python scripts/chair_determination.py --apply --allow-production --output-root . \
    --converter /path/to/ietf2vcon --meetings 127 \
    --previous-rev <commit of the latest release> --previous-version <its version> \
    --proceedings ./mirror/proceedings --log-dir ./logs --state-dir ./state
git add -A && git commit -m "IETF 127, chair determination"
python scripts/reference_types.py --apply --allow-production --output-root . \
    --previous-rev <commit of the latest release> --proceedings ./mirror/proceedings
.venv/bin/python scripts/validate_vcons.py /path/to/ietf-meeting-vcons --all --pattern 'ietf127/*.vcon.json'   # run from an ietf2vcon checkout
```

What works and what to expect:

- The chair tool accepts a meeting that has no file in the previous release. Such a file is compared as "no published file" and gets no `previous-release` candidate. The tool still needs `--previous-rev`, and `dataset.json` at that commit must say `--previous-version`, so for a meeting added after 3.0.0 use the commit of the latest release with its version, for example `--previous-version 3.0.0`. The meetings already in the dataset keep the `--previous-rev` they were recorded with.
- Use an empty `--cache-dir` (or one filled after the meeting) for the new meeting. A cache from an earlier run lacks the meeting's group changes, and the tool then reports mismatches instead of writing; an empty cache costs about thirty throttled requests. This has not been tried on a real new meeting, because none exists yet.
- Converting a new meeting without `--no-transcript` fetches a transcript where one exists. The converter writes transcripts as WTF analysis entries itself, so no separate normalisation step is needed.
- Do not run any of the one-off migration scripts in `scripts/` on these files: they were written for older syntax versions and would damage 0.5.0 files. [scripts/README.md](scripts/README.md) says which scripts apply.

To generate a single session, after installing the converter as above:

```bash
ietf2vcon convert --meeting 126 --group quic
```

### Backfilling Transcripts

If sessions end up with a recording but no transcript, fetch the video's auto-captions directly rather than regenerating the vCons, which would churn their UUIDs:

```bash
python scripts/backfill_youtube_captions.py --meetings 110-124 --workers 2 --delay 2
```

It skips sessions that already have a `youtube` transcript, so it is idempotent and resumes cleanly after an interrupt. Two operational notes, both learned the hard way:

- **Keep `yt-dlp` current.** A stale build fails with "Sign in to confirm you're not a bot" on every request, which looks exactly like an IP ban but is not. `brew upgrade yt-dlp` (or `pip install -U yt-dlp`) fixes it.
- **Do not raise `--workers` much.** The video host throttles bulk caption fetches. The `--give-up-after` guard aborts on sustained throttling rather than burning through the queue against a block.

Sessions whose recording genuinely has no published captions are reported as `no-captions` rather than as errors. The entry the script appends goes after the chair determination entry in `analysis`, and it carries no `mediatype`; the files in this release all have one, so add `"mediatype": "application/json"` when you add a transcript, or run `externalize_transcripts.py` on it, which sets it.

### Transcribing without captions

Where no captions exist (the audio era, IETF 90-94, and the caption-less 2016-2017 videos, IETF 95-98), the audio is transcribed on-device with the `mi` command line tool (macOS 26 or later). It runs on the machine's own accelerator hardware at roughly 90x real time, needs no model download or API key, and reads compressed audio directly. Output carries `vendor: apple`, `product: speechanalyzer`; it has segment-level timing but no per-word timestamps or confidence, so those fields are omitted rather than fabricated.

For sessions with a video recording (extract audio, then transcribe):

```bash
python scripts/transcribe_mi.py --meetings 95-98
```

For the audio era, whose `ietf.org/audio/*.mp3` recordings are behind the IETF's single sign-on login, fetching needs a browser-grade TLS fingerprint *and* an authenticated session. `scripts/transcribe_audio_authed.py` reuses the operator's logged-in browser session; it never handles a password:

```bash
# 1. Sign in at datatracker.ietf.org in the browser, then open one audio URL
#    (e.g. https://www.ietf.org/audio/ietf92/<file>.mp3) to complete the
#    sign-in flow until the file plays.
# 2. Then:
pip install curl_cffi browser_cookie3   # into the ietf2vcon venv
python scripts/transcribe_audio_authed.py --meetings 90-93
```

It re-reads the browser's cookies on every download to ride through clearance rotation, and stops cleanly (resumably) if the browser session expires. Both scripts are idempotent: a session that already has a transcript is skipped. Some audio-era files are Ogg Vorbis mislabelled as `.mp3`, which the decoder rejects; the authed script transcodes those with ffmpeg and retries. Like the backfill script, both append an entry with no `mediatype`.

Two older scripts, `scripts/transcribe.py` (a cloud transcription service with speaker diarization) and `scripts/whisper_transcribe.py` (local speech recognition), are **not usable on the current files**: they look for a dialog of type `video`, which no file has (recordings are type `recording`), so they skip every file. No transcript in the dataset came from the first of them. See [scripts/README.md](scripts/README.md).

## Changes in 3.0.0

| What | 2.1.0 | 3.0.0 |
|---|---|---|
| vCon syntax (`vcon`) | `0.4.0` | `0.5.0`, all 8,181 files |
| `sessions` | absent | one Session in every file |
| Session start | first schedule assignment found, often from a draft schedule | official schedule only: 2,293 moved (1,508 by more than 24 hours), 123 removed, 5,765 unchanged or none in both |
| Recording Dialogs (4,079) | `start` from that schedule, `meta` on each | `start` changed in 2,172 and `duration` in 107; `parties` corrected in 903; no `meta`; links and media types unchanged |
| Attachment scope | `party` and `dialog` (an index past the end of `dialog` in 4,102 files with no recording) | `party` and `session`, in all 121,451 attachments |
| Attachment `meta` | `title` and `datatracker_url` in `meta` (63,295) | gone; the `materials` list in `meeting_metadata` carries them |
| `extensions` | `lawful_basis`, `wtf_transcription`, `role`, `meta` | `lawful_basis`, `CC`, `wtf_transcription` |
| Session status | not recorded | `status` in `meeting_metadata`: 7,081 `sched`, 768 `notmeet`, 233 `canceled`, 76 `deleted`, 18 `schedw`, 2 `disappr`, 1 `resched`, 2 none |
| Chairs | the converter's lookup | recorded in every file; compared with 2.1.0: 6,488 identical, 223 reordered, 479 email only, 467 different people, 520 no longer named, 4 newly named |
| `analysis` | one transcript entry where there is a transcript | a new entry of type `report` (the chair determination record) in all 8,181 files |
| Lawful basis | grants dated at conversion time; no `proof_mechanisms` (two in the 2 interim files) | grants dated at the session start (or the meeting's first day); `proof_mechanisms` in every file, with a second entry in 3,573 |
| Hashed references | 63,295 | 64,032 (778 added, 60 removed) |
| Unhashed references | 33,238 | 32,876 |
| Material attachments | 51,993 slides, 15,164 minutes, 3,716 bluesheets | 51,995 slides, 15,249 minutes, 4,004 bluesheets |
| Transcripts | 4,061 | 4,061, bodies and hashes unchanged; inline entries gained `mediatype` |
| `recording_note` | absent | in 12 files |
| `mimetype` in the 9,048 unhashed references to a Datatracker material page | in 2.1.0 (9,410 such references): 5,954 `application/pdf`, 3,355 `text/html`, 101 other types | converter 0.3.0 wrote `application/pdf` for 9,047 of them. The type was then set from the file name or restored from 2.1.0 (4,142), or removed where nothing says what the page is (4,313). Final: absent 4,313, `text/html` 3,354, `application/pdf` 592, `application/json` 472, pptx 221, ppt 85, `text/plain` 9, gzip 1, zip 1 |

`uuid` and `created_at` are unchanged in all 8,181 files; `updated_at` changed in every file.

**What will break for a consumer.** The `vcon` value is `0.5.0`, so a reader that checks it for `0.4.0` rejects every file. Session data is in `sessions`, which did not exist. Attachments are scoped by `session`, no longer by `dialog`, and have no `meta`: read a material's title and Datatracker page from `meeting_metadata.materials`. `extensions` no longer lists `role` or `meta`, and lists `CC`. Every file now has an `analysis` entry of type `report`, so code that takes `analysis[0]` or the last entry as the transcript is wrong: select transcripts by `type == "wtf_transcription"`. A session's start is `sessions[0]["start"]`, and it can be absent. Recordings are linked from `sessions[0]["dialog"]`.

## Related Specifications

- [draft-ietf-vcon-vcon-core](https://datatracker.ietf.org/doc/draft-ietf-vcon-vcon-core/) - vCon container format; these files follow revision -05
- [draft-howe-vcon-wtf-extension](https://datatracker.ietf.org/doc/draft-howe-vcon-wtf-extension/) - World Transcription Format
- [draft-howe-vcon-lawful-basis](https://datatracker.ietf.org/doc/draft-howe-vcon-lawful-basis/) - Lawful basis extension
- [draft-ietf-vcon-cc-extension](https://datatracker.ietf.org/doc/draft-ietf-vcon-cc-extension/) - Contact-center extension (`CC`), which defines the party `role` parameter

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
- The organisations that host the IETF recordings, which publish auto-generated captions
