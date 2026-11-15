"""Synthetic fixtures. All names and addresses are invented."""
import json
from datetime import UTC, datetime

import chair_determination as rc


def T(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def make_data(snapshots, rolehistory, live=None, groups=None):
    """snapshots: {gid: [(snapshot id, time)]}; rolehistory: {snapshot id: [person ids]};
    live: {gid: [person ids]}; groups: {gid: (acronym, type, time)}."""
    groups = groups or {}
    group_objs = {
        gid: rc.Group(gid, acr, typ, T(when) if isinstance(when, str) else when)
        for gid, (acr, typ, when) in groups.items()
    }
    snaps = {
        gid: [rc.Snapshot(sid, gid, T(when)) for sid, when in items]
        for gid, items in snapshots.items()
    }
    counter = [1000]

    def rows(persons):
        out = []
        for person in persons:
            counter[0] += 1
            out.append(rc.RoleRow(counter[0], person, f"/api/v1/person/email/chair{person}@example.org/"))
        return out

    return rc.ChairData(
        group_objs, snaps,
        {sid: rows(p) for sid, p in rolehistory.items()},
        {gid: rows(p) for gid, p in (live or {}).items()},
    )


def session_file(chairs=(), start="2012-03-01T10:00:00+00:00", dialog=True, acronym="grp", sid="4242", meeting="84"):
    """A regenerated session in the converter's key order; chairs: [(name, mailto)]."""
    body = {
        "ietf_meeting_number": meeting, "location": "Nowhere, XX", "working_group": acronym,
        "session_id": int(sid), "status": "sched", "room": "Hall 1",
        "start_time": start, "duration_seconds": 3600,
        "materials": [{"filename": "agenda.txt", "url": "https://www.ietf.org/proceedings/84/agenda/agenda-84-grp.txt"}],
    }
    attachment = lambda purpose, **extra: {"purpose": purpose, **extra, "start": "2026-08-12T15:37:53.230411+00:00",
                                           "party": 0, "session": 0}
    parties = [rc.chair_party({"name": n, "mailto": m}) for n, m in chairs] + [{"name": "IETF Attendees", "role": "attendee"}]
    indices = list(range(len(parties)))
    session = {"start": start, "duration": 3600, "parties": indices} if start else {"parties": indices}
    return {
        "vcon": "0.5.0",
        "uuid": "00000000-0000-4000-8000-000000000001",
        "parties": parties,
        "dialog": ([{"type": "recording", "start": "2012-03-01T10:00:00+00:00", "duration": 3600,
                     "parties": list(indices), "mediatype": "audio/mpeg", "url": "https://example.org/a.mp3"}]
                   if dialog else []),
        "attachments": [
            attachment("meeting_metadata", body=json.dumps(body, ensure_ascii=False), encoding="json",
                       mediatype="application/json"),
            attachment("ingress_info", body='{"source": "ietf2vcon"}', encoding="json", mediatype="application/json"),
            attachment("agenda", mediatype="text/plain", filename="agenda.txt",
                       url="https://www.ietf.org/proceedings/84/agenda/agenda-84-grp.txt", content_hash="sha256-abc"),
        ],
        "analysis": ([{"type": "wtf_transcription", "dialog": 0, "vendor": "x", "body": "{}", "encoding": "json"}]
                     if dialog else []),
        "created_at": "2026-08-12T15:37:53.230411+00:00",
        "sessions": [session],
        "subject": "IETF 84 - GRP Working Group Session",
        "extensions": ["lawful_basis", "CC"],
        "updated_at": "2026-11-14T22:28:48.273453+00:00",
    }


def write_file(directory, vcon, name):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rc.file_text(vcon), encoding="utf-8")
    return path


class Names:
    def __init__(self, names=None):
        self.names = names or {1: "Ann One", 2: "Bo Two", 3: "Cy Three", 4: "Di Four", 5: "Ed Five"}

    def resolve(self, ids):
        return {i: self.names[i] for i in ids if i in self.names}
