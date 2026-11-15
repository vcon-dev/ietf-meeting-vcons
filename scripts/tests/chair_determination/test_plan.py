import json

import chair_determination as rc
from helpers import T, make_data, session_file, write_file

LOG = """\
[14/Nov/2026 17:23:50] INFO - Converting IETF 84 grp session 4242
[14/Nov/2026 17:23:51] WARNING - Chairs for grp at 2012-08-01T10:00:00+00:00: snapshot 200 is ambiguous; snapshots 100, 200 share time 2011-12-09T20:00:00+00:00 with different chairs; omitting chairs
[14/Nov/2026 17:23:51] INFO - No verifiable chairs for grp at 2012-08-01 10:00:00+00:00; omitting the chair party
[14/Nov/2026 17:23:55] INFO - Converting IETF 84 rgg session 4300
[14/Nov/2026 17:23:56] WARNING - Chairs for rgg at 2012-08-02T10:00:00+00:00: live roles is ambiguous; snapshots 300, 301 share time 2011-12-09T20:00:00+00:00 with different chairs; omitting chairs
[14/Nov/2026 17:23:57] INFO - Converting IETF 84 snp session 4400
[14/Nov/2026 17:23:58] INFO - Chairs for snp at 2012-08-02T11:00:00+00:00: snapshot 5 from 2012-01-01T00:00:00+00:00
[14/Nov/2026 17:23:59] INFO - Converting IETF 84 liv session 4500
[14/Nov/2026 17:24:00] INFO - Chairs for liv at 2012-08-02T11:00:00+00:00: live roles, group last edited 2010-01-01T00:00:00+00:00
[14/Nov/2026 17:24:01] INFO - Converting IETF 84 nhi session 4600
[14/Nov/2026 17:24:01] INFO - Session 4600 of nhi has no start time; looking up chairs as of the end of the meeting's first day (2012-07-29T23:59:59+00:00)
[14/Nov/2026 17:24:02] INFO - No nhi chairs at 2012-07-29T23:59:59+00:00: no history snapshot at or before it (group last edited 2013-01-01T00:00:00+00:00)
[14/Nov/2026 17:24:03] INFO - Converting IETF 84 snp session 4400
[14/Nov/2026 17:24:04] INFO - Chairs for snp at 2012-08-02T11:00:00+00:00: snapshot 6 from 2012-01-02T00:00:00+00:00
"""


def test_parse_log_blocks_reads_every_kind_and_the_last_block_wins(tmp_path):
    log = tmp_path / "convert-84.log"
    log.write_text(LOG)
    blocks = rc.parse_log_blocks(log)
    assert sorted(blocks) == ["4242", "4300", "4400", "4500", "4600"]
    amb = blocks["4242"]
    assert (amb.kind, amb.source, amb.tied, amb.cutoff) == ("ambiguous", "snapshot 200", [100, 200], T("2012-08-01T10:00:00"))
    assert amb.tied_time == T("2011-12-09T20:00:00")
    assert blocks["4300"].source == "live roles" and blocks["4300"].tied == [300, 301]
    assert (blocks["4400"].kind, blocks["4400"].snapshot_id) == ("snapshot", 6)  # the later attempt
    assert (blocks["4500"].kind, blocks["4500"].group_time) == ("live", T("2010-01-01T00:00:00"))
    assert (blocks["4600"].kind, blocks["4600"].no_start) == ("no-history", T("2012-07-29T23:59:59"))
    assert blocks["4600"].group_time == T("2013-01-01T00:00:00")


def test_completed_meetings_need_a_done_marker(tmp_path):
    (tmp_path / "state").mkdir()
    for name in ("83.done", "84.done", "85.failed", "interim-2026-vcon-01.done"):
        (tmp_path / "state" / name).write_text("")
    assert rc.completed_meetings(tmp_path / "state") == [83, 84, "interim-2026-vcon-01"]


def test_converter_answer_follows_the_converters_rule():
    data = make_data(
        {1: [(1, "2011-01-01T00:00:00"), (2, "2011-06-01T00:00:00"), (3, "2011-06-01T00:00:00")]},
        {1: [10], 2: [10, 11], 3: [10, 12]}, live={1: [10]},
        groups={1: ("grp", "wg", "2013-01-01T00:00:00")})
    answer = lambda t: rc.converter_answer(data, 1, T(t))
    assert answer("2010-01-01T00:00:00").kind == "no-history"
    assert (answer("2011-02-01T00:00:00").kind, answer("2011-02-01T00:00:00").snapshot.id) == ("snapshot", 1)
    tied = answer("2011-07-01T00:00:00")
    assert (tied.kind, tied.source, tied.tied) == ("ambiguous", "snapshot 3", [2, 3])
    assert answer("2014-01-01T00:00:00").kind == "live"  # group edited before then; snapshots 2, 3 do not tie its time
    data.live[1] = []
    assert rc.converter_answer(data, 1, T("2014-01-01T00:00:00")).rows == []


def test_live_roles_are_ambiguous_when_snapshots_tied_at_the_group_time_differ_from_them():
    data = make_data({1: [(1, "2013-01-01T00:00:00"), (2, "2013-01-01T00:00:00")]}, {1: [10], 2: [11]}, live={1: [10]},
                     groups={1: ("grp", "wg", "2013-01-01T00:00:00")})
    answer = rc.converter_answer(data, 1, T("2014-01-01T00:00:00"))
    assert (answer.kind, answer.source, answer.tied) == ("ambiguous", "live roles", [1, 2])


def test_the_published_reader_reads_chairs_from_a_git_object_database(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    write_file(repo / "ietf84", session_file([("Ann One", "ann@example.org")]), "ietf84_grp_1.vcon.json")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path)}
    for command in (["init", "-q"], ["add", "."]):
        subprocess.run(["git", "-C", str(repo), *command], check=True, env=env, capture_output=True)
    tree = subprocess.run(["git", "-C", str(repo), "write-tree"], check=True, env=env, capture_output=True,
                          text=True).stdout.strip()  # a tree object is enough to read from; nothing is committed
    reader = rc.PublishedReader(repo, rev=tree)
    try:
        assert reader.chairs("ietf84/ietf84_grp_1.vcon.json") == [{"name": "Ann One", "mailto": "ann@example.org"}]
        assert reader.chairs("ietf84/ietf84_nothing_9.vcon.json") is None
        assert reader.chairs("ietf84/ietf84_grp_1.vcon.json") == [{"name": "Ann One", "mailto": "ann@example.org"}]
    finally:
        reader.close()


def test_compare_people_with_published_chairs():
    ours = [{"name": "Olaf Brøan"}, {"name": "Pat Nobody"}]
    pub = lambda *n: [{"name": x} for x in n]
    assert rc.compare_people(ours, None) == "no published file"
    assert rc.compare_people(ours, []) == "published has no chairs"
    assert rc.compare_people(ours, pub("Pat Nobody", "Olaf Broan")) == "same people"
    assert rc.compare_people(ours, pub("Olaf Broan")) == "overlap"
    assert rc.compare_people(ours, pub("Kim Other")) == "different people"
    assert rc.compare_people([], pub("Kim Other")) == "different people"


def test_summary_counts_by_meeting_and_reports_are_written(tmp_path):
    rows = []

    def row(meeting, action, basis, ambiguous=False, decision="", reason="", rg=False, two=False):
        rows.append({"meeting": meeting, "action": action, "basis": basis, "ambiguous": ambiguous, "decision": decision,
                     "reason": reason, "group_type": "rg" if rg else "wg", "two_source": two, "file": "f", "acronym": "a",
                     "as_of": "", "as_of_basis": "", "kind": "", "outcome": "", "chairs": [], "snapshot_used": "",
                     "minutes_verdict": "", "page_status": "", "previous_chairs": None, "comparison": "",
                     "problems": ["why"] if action == "mismatch" else [], "session_id": "1", "path": "ietf1/f"})

    row(84, "recover", "minutes-confirmed", True, "recover", two=True)
    row(84, "record", "ambiguous-history", True, "omit", "no explicit chairs line", two=True)
    row(84, "record", "ambiguous-history", True, "omit", "no minutes document", rg=True, two=True)
    row(84, "record", "history-snapshot")
    row(84, "mismatch", "")
    summary = rc.summarize(rows)["84"]
    assert summary["files"] == 5 and summary["mismatches"] == 1 and summary["ambiguous"] == 3 and summary["recovered"] == 1
    assert summary["omitted"] == {"no explicit chairs line": 1, "no minutes document": 1}
    assert summary["earlier_two_source"] == 2 and summary["earlier_now_omitted"] == {"no explicit chairs line": 1}
    assert summary["bases"] == {"minutes-confirmed": 1, "ambiguous-history": 2, "history-snapshot": 1}
    rc.write_reports(rows, rc.summarize(rows), {"requests_made": 0}, tmp_path / "reports")
    tsv = (tmp_path / "reports" / "plan.tsv").read_text().splitlines()
    assert len(tsv) == 6 and tsv[0].split("\t")[:3] == ["meeting", "file", "acronym"]
    assert (tmp_path / "reports" / "plan-mismatches.txt").read_text() == "ietf1/f\twhy\n"


def test_rate_limit_during_person_lookup_stops_the_run(tmp_path):
    class Limited:
        def resolve(self, ids):
            raise rc.RateLimited("stop")

    data = make_data({1: [(100, "2011-01-01T00:00:00")]}, {100: [1]}, live={1: [1]},
                     groups={1: ("grp", "wg", "2010-01-01T00:00:00")})
    write_file(tmp_path / "out" / "ietf84", session_file([("Ann One", "chair1@example.org")], acronym="grp", sid="1"),
               "ietf84_grp_1.vcon.json")
    ctx = rc.Context(data, rc.Calibration([]), Limited(), tmp_path, tmp_path / "out")
    try:
        rc.plan_meeting(ctx, 84, {})
    except rc.RateLimited:
        return
    raise AssertionError("expected the run to stop")


def test_meetings_are_numbers_or_interim_names_and_sort_numbers_first():
    assert rc.parse_meeting("84") == 84 and rc.parse_meeting("interim-2026-vcon-01") == "interim-2026-vcon-01"
    assert rc.meeting_dir(84) == "ietf84" and rc.meeting_dir("interim-2026-vcon-01") == "interim-2026-vcon-01"
    assert sorted([ "interim-2026-vcon-02", 100, 9, "interim-2026-vcon-01"], key=rc.meeting_key) == [
        9, 100, "interim-2026-vcon-01", "interim-2026-vcon-02"]
    assert rc._FILE_RE.match("interim-2026-vcon-01_vcon_35147.vcon.json").groups() == ("interim-2026-vcon-01", "vcon", "35147")
    assert rc._FILE_RE.match("ietf84_6man_22149.vcon.json").groups() == ("ietf84", "6man", "22149")


def test_the_log_of_an_interim_meeting_is_read(tmp_path):
    log = tmp_path / "convert-interim-2026-vcon-02.log"
    log.write_text(
        "[14/Nov/2026 15:50:37] INFO - Converting IETF interim-2026-vcon-02 vcon session 35825\n"
        "[14/Nov/2026 15:50:38] INFO - Chairs for vcon at 2026-09-09T14:00:00+00:00: live roles, group last edited "
        "2026-03-18T09:56:22+00:00\n")
    block = rc.parse_log_blocks(log)["35825"]
    assert (block.kind, block.cutoff, block.group_time) == ("live", T("2026-09-09T14:00:00"), T("2026-03-18T09:56:22"))


def test_summary_handles_numbered_and_interim_meetings_together():
    def row(meeting):
        return {"meeting": meeting, "action": "record", "basis": "live-roles", "ambiguous": False, "decision": "",
                "reason": "", "group_type": "wg", "two_source": False}

    summary = rc.summarize([row("interim-2026-vcon-01"), row(100), row(9)])
    assert list(summary) == ["9", "100", "interim-2026-vcon-01"]
    assert "interim-2026-vcon-01" in rc.summary_text(summary, {})


def test_the_previous_release_must_be_the_commit_that_says_so(tmp_path):
    import os
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}

    def tree(version):
        (repo / "dataset.json").write_text(json.dumps({"name": "x", "version": version}))
        for command in (["init", "-q"], ["add", "."]):
            subprocess.run(["git", "-C", str(repo), *command], check=True, env=env, capture_output=True)
        return subprocess.run(["git", "-C", str(repo), "write-tree"], check=True, env=env, capture_output=True,
                              text=True).stdout.strip()

    assert rc.release_problem(repo, tree("2.1.0")) is None
    assert "version 2.2.0" in rc.release_problem(repo, tree("2.2.0"))
    assert "cannot read" in rc.release_problem(repo, "0" * 40)
