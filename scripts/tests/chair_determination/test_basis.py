import json

import pytest

import chair_determination as rc
from helpers import Names, T, make_data, session_file, write_file

CAL = rc.Calibration([(50, T("2012-01-01T00:00:00")), (130, T("2012-02-01T00:00:00")),
                      (160, T("2012-04-01T00:00:00")), (350, T("2012-12-01T00:00:00"))])
CUT = "2012-03-01T10:00:00+00:00"
TIE = "2011-12-09T20:00:00"

GROUPS = {
    1: ("live", "wg", "2010-01-01T00:00:00"),
    2: ("snap", "wg", "2013-06-01T00:00:00"),
    3: ("nohist", "wg", "2013-01-01T00:00:00"),
    4: ("empty", "wg", "2013-01-01T00:00:00"),
    5: ("amb", "wg", "2013-06-01T00:00:00"),
    6: ("ambrg", "rg", "2013-06-01T00:00:00"),
    7: ("ambunc", "wg", "2013-06-01T00:00:00"),
}
SNAPS = {
    2: [(10, "2011-01-01T00:00:00"), (11, "2012-02-01T00:00:00")],
    3: [(20, "2012-09-01T00:00:00")],
    4: [(30, "2011-05-01T00:00:00")],
    5: [(100, TIE), (120, TIE), (200, TIE)],
    6: [(101, TIE), (121, TIE), (201, TIE)],
    7: [(140, TIE), (141, TIE), (142, TIE)],
}
ROLES = {10: [1], 11: [2, 3], 20: [1], 30: [],
         100: [1, 3], 120: [1, 4], 200: [1, 2],
         101: [1, 3], 121: [1, 4], 201: [1, 2],
         140: [1, 2], 141: [1, 3], 142: [1, 2]}
LIVE = {1: [1, 2]}

LOGS = {
    "live": f"Chairs for live at {CUT}: live roles, group last edited 2010-01-01T00:00:00+00:00",
    "snap": f"Chairs for snap at {CUT}: snapshot 11 from 2012-02-01T00:00:00+00:00",
    "nohist": f"No nohist chairs at {CUT}: no history snapshot at or before it (group last edited 2013-01-01T00:00:00+00:00)",
    "empty": f"Chairs for empty at {CUT}: snapshot 30 from 2011-05-01T00:00:00+00:00",
    "amb": (f"Chairs for amb at {CUT}: snapshot 200 is ambiguous; snapshots 100, 120, 200 share time "
            f"{TIE}+00:00 with different chairs; omitting chairs"),
    "ambrg": (f"Chairs for ambrg at {CUT}: snapshot 201 is ambiguous; snapshots 101, 121, 201 share time "
              f"{TIE}+00:00 with different chairs; omitting chairs"),
    "ambunc": (f"Chairs for ambunc at {CUT}: snapshot 142 is ambiguous; snapshots 140, 141, 142 share time "
               f"{TIE}+00:00 with different chairs; omitting chairs"),
}
ANN = ("Ann One", "chair1@example.org")
BO = ("Bo Two", "chair2@example.org")
CY = ("Cy Three", "chair3@example.org")
PAGE = '<h3>Chair(s):</h3><ul>{}</ul>'
LI = '<li><a href="mailto:{}">{}</a></li>'


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.data = make_data(SNAPS, ROLES, LIVE, GROUPS)
        self.previous = {}
        self.dates = {}
        self.sid = 1000

    def page(self, acronym, *people, meeting=84):
        entries = "".join(LI.format(m, n) for n, m in people)
        path = self.tmp / "proc" / str(meeting) / f"{acronym}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PAGE.format(entries))

    def minutes(self, acronym, text, kind="minutes", meeting=84):
        path = self.tmp / "proc" / str(meeting) / kind / f"{kind}-{meeting}-{acronym}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        rc._listing_cache.clear()

    def plan(self, acronym, file_chairs, start=CUT, log_line=None, log=True, meeting=84):
        self.sid += 1
        sid = str(self.sid)
        directory = self.tmp / "out" / rc.meeting_dir(meeting)
        for old in directory.glob("*.vcon.json") if directory.exists() else []:
            old.unlink()
        vcon = session_file(file_chairs, start=start, acronym=acronym, sid=sid)
        write_file(directory, vcon, f"{rc.meeting_dir(meeting)}_{acronym}_{sid}.vcon.json")
        lines = [f"[x] INFO - Converting IETF {meeting} {acronym} session {sid}"]
        if start is None:
            lines.append(f"[x] INFO - Session {sid} of {acronym} has no start time; looking up chairs as of the end "
                         f"of the meeting's first day (2012-03-01T23:59:59+00:00)")
        if log:
            line = log_line or LOGS[acronym]
            if start is None and log_line is None:
                line = line.replace(CUT, "2012-03-01T23:59:59+00:00")
            lines.append("[x] WARNING - " + line)
        logfile = self.tmp / "convert.log"
        logfile.write_text("\n".join(lines) + "\n")
        previous = self.previous.get(acronym)
        ctx = rc.Context(self.data, CAL, Names(), self.tmp / "proc", self.tmp / "out", lambda rel: previous,
                         self.dates)
        rows = rc.plan_meeting(ctx, meeting, rc.parse_log_blocks(logfile))
        assert len(rows) == 1
        return rows[0]


@pytest.fixture
def world(tmp_path):
    rc._listing_cache.clear()
    return World(tmp_path)


def body(row):
    return json.loads(row["entry"]["body"])


def test_live_roles(world):
    row = world.plan("live", [ANN, BO])
    assert (row["action"], row["basis"], row["outcome"]) == ("record", "live-roles", "named")
    b = body(row)
    assert b["chairs"] == [{"name": "Ann One", "mailto": "chair1@example.org"},
                           {"name": "Bo Two", "mailto": "chair2@example.org"}]
    assert b["as_of"] == "2012-03-01T10:00:00Z" and b["as_of_basis"] == "session-start"
    assert b["datatracker"] == {"group": "live", "group_last_edited": "2010-01-01T00:00:00Z"}
    assert "candidates" not in b and "reason" not in b
    assert rc.entry_problems(row["entry"]) == []


def test_history_snapshot(world):
    row = world.plan("snap", [("Bo Two", "chair2@example.org"), ("Cy Three", "chair3@example.org")])
    assert (row["basis"], row["outcome"]) == ("history-snapshot", "named")
    assert body(row)["datatracker"] == {"group": "snap", "group_last_edited": "2013-06-01T00:00:00Z",
                                        "snapshot": 11, "snapshot_time": "2012-02-01T00:00:00Z"}


def test_no_chairs_recorded(world):
    row = world.plan("empty", [])
    assert (row["basis"], row["outcome"]) == ("no-chairs-recorded", "none")
    assert body(row)["reason"] == "The Datatracker record for that time lists no chairs."
    assert "chairs" not in body(row)


def test_no_history_with_nothing_else_to_record(world):
    row = world.plan("nohist", [])
    assert (row["basis"], row["outcome"], row["action"]) == ("no-history", "none", "record")
    assert "candidates" not in body(row) and "predates" in body(row)["reason"]


def test_no_history_keeps_published_chairs_and_the_proceedings_page_as_candidates(world):
    world.previous["nohist"] = [{"name": "Ann One", "mailto": "old@example.org"}]
    world.page("nohist", ("Ann One", "page@example.org"), ("Bo Two", "page2@example.org"))
    world.minutes("nohist", "Chairs: Ann One and Bo Two\n")
    b = body(world.plan("nohist", []))
    assert [c["source"] for c in b["candidates"]] == ["previous-release", "proceedings-page", "session-minutes"]
    assert b["candidates"][0] == {"source": "previous-release", "version": "2.1.0",
                                  "chairs": [{"name": "Ann One", "mailto": "old@example.org"}]}
    assert b["candidates"][1]["url"] == "https://www.ietf.org/proceedings/84/nohist.html"
    assert b["candidates"][1]["chairs"][1] == {"name": "Bo Two", "mailto": "page2@example.org"}
    assert b["candidates"][2] == {"source": "session-minutes", "document": "minutes-84-nohist.txt",
                                  "chairs_named": ["Ann One", "Bo Two"]}


def test_published_chairs_of_the_same_people_add_no_candidates(world):
    world.previous["snap"] = [{"name": "Cy Three", "mailto": "x@example.org"}, {"name": "Bo Two", "mailto": "y@example.org"}]
    assert "candidates" not in body(world.plan("snap", [BO, CY]))


def test_chairs_that_changed_from_the_previous_release_are_recorded(world):
    world.previous["snap"] = [{"name": "Ann One", "mailto": "old@example.org"}]
    b = body(world.plan("snap", [BO, CY]))
    assert b["outcome"] == "named" and [c["source"] for c in b["candidates"]] == ["previous-release"]
    assert "reason" not in b


def test_no_published_chairs_means_no_previous_release_entry(world):
    world.previous["snap"] = []
    assert "candidates" not in body(world.plan("snap", [BO, CY]))


def test_a_session_with_no_start_is_dated_by_the_first_day_of_the_meeting(world):
    from datetime import date
    world.dates = {84: date(2012, 3, 1)}
    row = world.plan("live", [ANN, BO], start=None)
    assert row["action"] == "record" and body(row)["as_of_basis"] == "meeting-first-day"
    assert body(row)["as_of"] == "2012-03-01T23:59:59Z"
    world.dates = {84: date(2012, 3, 2)}
    row = world.plan("live", [ANN, BO], start=None)
    assert row["action"] == "mismatch" and "first day" in row["problems"][0]


def test_the_logged_first_day_is_used_when_the_meeting_table_is_missing(world):
    row = world.plan("live", [ANN, BO], start=None)
    assert row["action"] == "record" and body(row)["as_of"] == "2012-03-01T23:59:59Z"


# ---- ambiguous sessions: three sources

def agree(world, acronym="amb"):
    world.page(acronym, ("Ann One", "page1@example.org"), ("Bo Two", "page2@example.org"))
    world.minutes(acronym, "Chairs: Ann One, Bo Two\nMinutes: someone\n")


def test_ambiguous_session_confirmed_by_all_three(world):
    agree(world)
    row = world.plan("amb", [])
    assert (row["action"], row["decision"], row["basis"], row["outcome"]) == ("recover", "recover", "minutes-confirmed", "named")
    assert row["chairs"] == [{"name": "Ann One", "mailto": "page1@example.org"},
                             {"name": "Bo Two", "mailto": "page2@example.org"}]
    b = body(row)
    assert b["chairs"] == row["chairs"]
    sources = [c["source"] for c in b["candidates"]]
    assert sources == ["history-write-order", "tied-history-records", "proceedings-page", "session-minutes"]
    assert b["datatracker"]["snapshot"] == 200 and b["datatracker"]["snapshot_time"] == "2011-12-09T20:00:00Z"
    assert "share one timestamp" in b["reason"] and "some weeks after" in b["note"]
    assert row["two_source"] is True and row["minutes_verdict"] == "confirmed"


def test_confirmed_recovery_that_differs_from_the_previous_release_keeps_the_evidence(world):
    agree(world)
    world.previous["amb"] = [{"name": "Cy Three", "mailto": "old@example.org"}]
    b = body(world.plan("amb", []))
    assert [c["source"] for c in b["candidates"]] == [
        "previous-release", "history-write-order", "tied-history-records", "proceedings-page", "session-minutes"]


def test_research_group_confirmed_like_any_other(world):
    agree(world, "ambrg")
    row = world.plan("ambrg", [])
    assert row["group_type"] == "rg" and row["action"] == "recover" and row["basis"] == "minutes-confirmed"


@pytest.mark.parametrize("setup,reason", [
    (lambda w: w.page("amb", ("Ann One", None), ("Bo Two", None)), "no minutes document"),
    (lambda w: (w.page("amb", ("Ann One", None), ("Bo Two", None)), w.minutes("amb", "Agenda\nno chairs line\n")),
     "no explicit chairs line"),
    (lambda w: (w.page("amb", ("Ann One", None), ("Bo Two", None)), w.minutes("amb", "Chairs: Ann One\n")),
     "minutes name a subset"),
    (lambda w: (w.page("amb", ("Ann One", None), ("Bo Two", None)), w.minutes("amb", "Chairs: Ann One, Cy Three\n")),
     "minutes do not match the other sources"),
    (lambda w: w.minutes("amb", "Chairs: Ann One, Bo Two\n"), "no proceedings page"),
    (lambda w: (w.page("amb", ("Ann One", None), ("Cy Three", None)), w.minutes("amb", "Chairs: Ann One, Bo Two\n")),
     "sources disagree"),
])
def test_ambiguous_session_stays_without_chairs_with_its_candidates(world, setup, reason):
    setup(world)
    row = world.plan("amb", [])
    assert (row["action"], row["decision"], row["reason"]) == ("record", "omit", reason)
    assert (row["basis"], row["outcome"]) == ("ambiguous-history", "none")
    b = body(row)
    assert "chairs" not in b and b["reason"].startswith("History records share one timestamp with different chairs, and ")
    sources = [c["source"] for c in b["candidates"]]
    assert sources[:2] == ["history-write-order", "tied-history-records"]
    write_order = b["candidates"][0]
    assert write_order["confident"] is True and write_order["snapshot"] == 200
    assert write_order["written_between"] == ["2012-04-01T00:00:00Z", "2012-12-01T00:00:00Z"]
    assert [c["name"] for c in write_order["chairs"]] == ["Ann One", "Bo Two"]
    tied = b["candidates"][1]
    assert tied["timestamp"] == "2011-12-09T20:00:00Z"
    assert [[c["name"] for c in s] for s in tied["chair_sets"]] == [["Ann One", "Cy Three"], ["Ann One", "Di Four"], ["Ann One", "Bo Two"]]
    assert rc.entry_problems(row["entry"]) == []


def test_ambiguous_with_an_uncertain_write_order(world):
    world.page("ambunc", ("Ann One", None), ("Bo Two", None))
    row = world.plan("ambunc", [])
    assert row["reason"] == "write order uncertain" and row["write_order_status"] == "uncertain"
    b = body(row)
    assert b["candidates"][0]["confident"] is False
    assert [c["source"] for c in b["candidates"]] == ["history-write-order", "tied-history-records", "proceedings-page"]


def test_minutes_chairs_are_listed_for_an_omitted_session(world):
    world.page("amb", ("Ann One", None), ("Bo Two", None))
    world.minutes("amb", "Chairs: Ann One\n")
    world.minutes("amb", "Chairs: Ann One, Cy Three\n", kind="agenda")
    b = body(world.plan("amb", []))
    named = [c for c in b["candidates"] if c["source"] == "session-minutes"]
    assert [(c["document"], c["chairs_named"]) for c in named] == [
        ("minutes-84-amb.txt", ["Ann One"]), ("agenda-84-amb.txt", ["Ann One", "Cy Three"])]


def test_chairs_in_the_record_are_never_null_placeholders(world):
    agree(world)
    world.previous["amb"] = [{"name": "Cy Three", "mailto": None}]
    text = world.plan("amb", [])["entry"]["body"]
    assert "null" not in text


# ---- the recomputation must equal the file

def test_a_file_whose_chairs_differ_from_the_converters_rule_is_not_written(world):
    row = world.plan("live", [ANN])
    assert row["action"] == "mismatch" and row["entry"] is None
    assert "Ann One" in row["problems"][0] and "Bo Two" in row["problems"][0]
    row = world.plan("live", [BO, ANN])  # same people, other order
    assert row["action"] == "mismatch"
    row = world.plan("live", [("Ann One", "other@example.org"), BO])  # same people, other address
    assert row["action"] == "mismatch"


def test_chairs_in_a_file_where_the_rule_gives_none_are_a_mismatch(world):
    assert world.plan("nohist", [ANN])["action"] == "mismatch"
    assert world.plan("amb", [ANN])["action"] == "mismatch"


def test_a_log_that_disagrees_is_a_mismatch(world):
    line = f"Chairs for snap at {CUT}: snapshot 10 from 2011-01-01T00:00:00+00:00"
    row = world.plan("snap", [BO, CY], log_line=line)
    assert row["action"] == "mismatch" and "snapshot 10" in row["problems"][0]
    line = f"Chairs for live at {CUT}: live roles, group last edited 2026-11-14T18:30:00+00:00"
    row = world.plan("live", [ANN, BO], log_line=line)
    assert row["action"] == "mismatch" and "edited between the cache and the conversion" in row["problems"][0]
    row = world.plan("live", [ANN, BO], log_line=f"Chairs for live at 2012-03-01T11:00:00+00:00: live roles, group last edited 2010-01-01T00:00:00+00:00")
    assert row["action"] == "mismatch" and "cutoff" in row["problems"][0]
    row = world.plan("amb", [], log_line=LOGS["amb"].replace("100, 120, 200", "100, 200"))
    assert row["action"] == "mismatch"
    assert world.plan("live", [ANN, BO], log=False)["action"] == "mismatch"


def test_unknown_group_and_unreadable_file_are_mismatches(world, tmp_path):
    world.data.by_acronym.pop("live")
    row = world.plan("live", [ANN, BO])
    assert row["action"] == "mismatch" and "not in the cached group table" in row["problems"][0]


def test_planning_again_over_a_tree_that_was_already_applied_gives_the_same_plan(world):
    agree(world)
    first = world.plan("amb", [])
    path = world.tmp / "out" / "ietf84" / first["file"]
    assert rc.apply_file(path, first, T("2026-11-15T00:00:00")) == "applied"
    # the converter's rule still gives no chairs for the session, but the file now has them and its entry
    ctx = rc.Context(world.data, CAL, Names(), world.tmp / "proc", world.tmp / "out", lambda rel: None, world.dates)
    logfile = world.tmp / "convert.log"
    again = rc.plan_meeting(ctx, 84, rc.parse_log_blocks(logfile))[0]
    assert again["action"] == "recover" and again["entry"] == first["entry"] and again["problems"] == []
    assert rc.apply_file(path, again) == "already recorded"


def test_chairs_in_an_ambiguous_file_without_our_entry_are_still_a_mismatch(world):
    agree(world)
    row = world.plan("amb", [("Ann One", "page1@example.org"), ("Bo Two", "page2@example.org")])
    assert row["action"] == "mismatch" and "converter's rule gives none" in row["problems"][0]


def test_chairs_that_the_sources_no_longer_confirm_are_a_mismatch(world):
    agree(world)
    first = world.plan("amb", [])
    path = world.tmp / "out" / "ietf84" / first["file"]
    rc.apply_file(path, first, T("2026-11-15T00:00:00"))
    world.minutes("amb", "Chairs: Ann One\n")  # the evidence changed after the chairs were added
    ctx = rc.Context(world.data, CAL, Names(), world.tmp / "proc", world.tmp / "out", lambda rel: None, world.dates)
    again = rc.plan_meeting(ctx, 84, rc.parse_log_blocks(world.tmp / "convert.log"))[0]
    assert again["action"] == "mismatch" and "do not confirm" in again["problems"][0]


# ---- remarks, "chaired by", no-chairs-recorded candidates, interims

def test_a_confirmed_session_keeps_the_remark_and_gets_a_fixed_note(world):
    world.page("amb", ("Ann One", "page1@example.org"), ("Bo Two", "page2@example.org"))
    world.minutes("amb", "- Chaired by Ann One and Cy Three (since Bo could not attend)\n")
    world.minutes("amb", "Chairs: Ann One, Bo Two\n", kind="agenda")
    row = world.plan("amb", [])
    assert row["action"] == "recover" and row["remark_kinds"] == ["absent"]
    b = body(row)
    assert b["note"] == ("Addresses come from the proceedings page, which reflects the chairs some weeks after the "
                         "meeting. The session minutes indicate that a named chair was not present at the session.")
    minutes = [c for c in b["candidates"] if c["source"] == "session-minutes"]
    assert minutes == [
        {"source": "session-minutes", "document": "minutes-84-amb.txt",
         "remarks": ["- Chaired by Ann One and Cy Three (since Bo could not attend)"]},
        {"source": "session-minutes", "document": "agenda-84-amb.txt", "chairs_named": ["Ann One", "Bo Two"]},
    ]
    assert [c["source"] for c in b["candidates"]][:3] == ["history-write-order", "tied-history-records", "proceedings-page"]
    assert rc.entry_problems(row["entry"]) == []


@pytest.mark.parametrize("text,kinds,sentence", [
    ("Chairs: Ann One, Bo Two (in absentia)\n", ["absent"], "was not present at the session."),
    ("Chairs: Ann One, Bo Two (remote)\n", ["remote"], "took part remotely."),
    ("Chairs: Ann One, Bo Two\nAO will step down as a chair\n", ["departure"], "announcing a departure."),
    ("Chairs: Ann One, Bo Two\nCy Three is acting for Bo Two\nBo Two is absent\n", ["absent", "acting"], None),
])
def test_one_fixed_sentence_per_kind_of_remark_and_no_names_in_the_note(world, text, kinds, sentence):
    world.page("amb", ("Ann One", None), ("Bo Two", None))
    world.minutes("amb", text)
    row = world.plan("amb", [])
    assert row["action"] == "recover" and row["remark_kinds"] == kinds
    note = body(row)["note"]
    assert sentence is None or note.endswith(sentence)
    assert all(REMARK in note for REMARK in [rc.REMARK_NOTES[k] for k in kinds])
    assert "Ann" not in note and "Bo " not in note


def test_every_confirmed_session_lists_the_evidence_it_relied_on(world):
    agree(world)
    b = body(world.plan("amb", []))
    wo, tied, page, minutes = b["candidates"]
    assert (wo["source"], wo["confident"], wo["snapshot"]) == ("history-write-order", True, 200)
    assert wo["written_between"] == ["2012-04-01T00:00:00Z", "2012-12-01T00:00:00Z"]
    assert [c["name"] for c in wo["chairs"]] == ["Ann One", "Bo Two"]
    assert [[c["name"] for c in s_] for s_ in tied["chair_sets"]] == [["Ann One", "Cy Three"], ["Ann One", "Di Four"], ["Ann One", "Bo Two"]]
    assert page["chairs"] == [{"name": "Ann One", "mailto": "page1@example.org"}, {"name": "Bo Two", "mailto": "page2@example.org"}]
    assert minutes == {"source": "session-minutes", "document": "minutes-84-amb.txt", "chairs_named": ["Ann One", "Bo Two"]}
    assert b["note"].startswith("Addresses come") and "minutes indicate" not in b["note"]
    assert "reason" in b and rc.entry_problems(rc.make_entry(b)) == []


def test_a_chaired_by_line_is_recorded_for_every_ambiguous_session_and_changes_no_outcome(world):
    world.page("amb", ("Ann One", None), ("Bo Two", None))
    world.minutes("amb", "Minutes\nChaired by Ann One and Bo Two\n")
    row = world.plan("amb", [])
    assert (row["action"], row["decision"], row["reason"]) == ("record", "omit", "no explicit chairs line")
    assert row["chaired_by_remarks"] == 1 and row["remark_kinds"] == []
    named = [c for c in body(row)["candidates"] if c["source"] == "session-minutes"]
    assert named == [{"source": "session-minutes", "document": "minutes-84-amb.txt",
                      "remarks": ["Chaired by Ann One and Bo Two"]}]
    assert "note" not in body(row)
    # the same session with the line removed has the same outcome and reason
    world.minutes("amb", "Minutes\n")
    again = world.plan("amb", [])
    assert (again["action"], again["decision"], again["reason"]) == ("record", "omit", "no explicit chairs line")


def test_a_chaired_by_line_does_not_stop_a_confirmation(world):
    agree(world)
    world.minutes("amb", "Chairs: Ann One, Bo Two\nChaired by Cy Three\n")
    row = world.plan("amb", [])
    assert row["action"] == "recover"
    assert any("remarks" in c for c in body(row)["candidates"] if c["source"] == "session-minutes")


def test_no_chairs_recorded_keeps_the_proceedings_page_and_minutes_as_candidates(world):
    world.page("empty", ("Ann One", "page@example.org"))
    world.minutes("empty", "Chairs: Ann One\n")
    row = world.plan("empty", [])
    assert row["basis"] == "no-chairs-recorded"
    b = body(row)
    assert [c["source"] for c in b["candidates"]] == ["proceedings-page", "session-minutes"]
    assert b["candidates"][0]["chairs"] == [{"name": "Ann One", "mailto": "page@example.org"}]
    assert b["candidates"][1]["chairs_named"] == ["Ann One"]
    world.previous["empty"] = [{"name": "Bo Two", "mailto": "old@example.org"}]
    assert [c["source"] for c in body(world.plan("empty", []))["candidates"]][0] == "previous-release"


def test_an_interim_meeting_is_planned_like_any_other_without_pages_or_minutes(world):
    name = "interim-2026-vcon-01"
    line = "Chairs for live at 2012-03-01T10:00:00+00:00: live roles, group last edited 2010-01-01T00:00:00+00:00"
    row = world.plan("live", [ANN, BO], meeting=name, log_line=line)
    assert row["action"] == "record" and (row["basis"], row["outcome"]) == ("live-roles", "named")
    assert row["path"] == f"{name}/{name}_live_{row['session_id']}.vcon.json" and row["meeting"] == name
    assert body(row)["as_of_basis"] == "session-start"
    assert rc.entry_problems(row["entry"]) == []
    # an ambiguous interim session stays without chairs: no page, no minutes
    row = world.plan("amb", [], meeting=name, log_line=LOGS["amb"])
    assert row["action"] == "record" and row["reason"] == "no proceedings page" and row["basis"] == "ambiguous-history"
    assert not any(c["source"] in ("proceedings-page", "session-minutes") for c in body(row)["candidates"])
    # the file name must carry the meeting's own name
    wrong = world.tmp / "out" / name / "ietf84_live_1.vcon.json"
    write_file(wrong.parent, session_file([ANN, BO]), wrong.name)
    ctx = rc.Context(world.data, CAL, Names(), world.tmp / "proc", world.tmp / "out", lambda rel: None, {})
    odd = [r for r in rc.plan_meeting(ctx, name, {}) if r["file"] == wrong.name][0]
    assert odd["action"] == "mismatch" and "file name" in odd["problems"][0]
