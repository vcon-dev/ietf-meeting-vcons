import copy
import json
import shutil
from datetime import UTC, datetime

import pytest

import chair_determination as rc
from helpers import session_file, write_file
from test_apply import AS_OF, CHAIRS, NOW, body_named, body_none, recover_row, record_row
from test_safety import inputs, make_run


def make_tree(root):
    rows = []
    specs = [("grp", "recover"), ("other", "record"), ("plain", "record")]
    for index, (name, action) in enumerate(specs):
        f = f"ietf84_{name}_{index}.vcon.json"
        write_file(root / "ietf84", session_file(), f)
        base = recover_row() if action == "recover" and index == 0 else record_row(body_none())
        rows.append({
            **base, "meeting": 84, "acronym": name, "file": f, "path": f"ietf84/{f}",
            "group_type": "wg", "as_of": "2012-03-01T10:00:00Z", "as_of_basis": "session-start", "kind": "ambiguous",
            "basis": "minutes-confirmed" if index == 0 else "ambiguous-history", "outcome": "named" if index == 0 else "none",
            "decision": "recover" if index == 0 else "omit", "reason": "", "snapshot_used": 200, "minutes_verdict": "",
            "page_status": "ok", "previous_chairs": None, "comparison": "", "problems": [], "session_id": str(index),
            "ambiguous": True, "two_source": index == 0,
        })
    return rows


@pytest.fixture
def applied(tmp_path):
    scratch, pristine = tmp_path / "scratch", tmp_path / "pristine"
    rows = make_tree(scratch)
    shutil.copytree(scratch, pristine)
    result = rc.apply_plan(rows, scratch, NOW)
    assert len(result["applied"]) == 3
    return rows, scratch, pristine


def verify(rows, root, not_checked=()):
    result = rc.verify_tree(rows, root, [84])
    return result, rc.verify_clean(result, list(not_checked))


def edit(path, change):
    vcon = json.loads(path.read_text(encoding="utf-8"))
    change(vcon)
    path.write_text(rc.file_text(vcon), encoding="utf-8")


def test_clean_after_apply(applied):
    rows, scratch, _ = applied
    result, clean = verify(rows, scratch)
    assert clean and result[84]["files"] == 3 and result[84]["with_entry"] == 3


def test_a_restored_file_has_no_entry(applied):
    rows, scratch, pristine = applied
    name = rows[0]["file"]
    shutil.copy(pristine / "ietf84" / name, scratch / "ietf84" / name)
    result, clean = verify(rows, scratch)
    assert not clean and result[84]["without_entry"] == [name] and result[84]["with_entry"] == 2
    assert name in rc.verify_text(result, [])


def test_a_second_entry_is_reported(applied):
    rows, scratch, _ = applied
    edit(scratch / rows[1]["path"], lambda v: v["analysis"].append(copy.deepcopy(v["analysis"][-1])))
    result, clean = verify(rows, scratch)
    assert not clean and len(result[84]["several_entries"]) == 1


def test_entry_that_disagrees_with_the_parties_is_reported(applied):
    rows, scratch, _ = applied
    edit(scratch / rows[0]["path"], lambda v: (v["parties"].pop(0), v["sessions"][0].__setitem__("parties", [0, 1]),
                                               v["dialog"][0].__setitem__("parties", [0, 1])))  # chair removed, entry kept
    result, clean = verify(rows, scratch)
    assert not clean and result[84]["disagrees_with_parties"] == [rows[0]["file"]]
    # chairs where the record says none
    edit(scratch / rows[1]["path"], lambda v: v["parties"].insert(0, rc.chair_party(CHAIRS[0])))
    assert verify(rows, scratch)[0][84]["disagrees_with_parties"] == [rows[0]["file"], rows[1]["file"]]


def test_malformed_entry_is_reported(applied):
    rows, scratch, _ = applied
    edit(scratch / rows[1]["path"], lambda v: v["analysis"][-1].__setitem__("product", "other"))
    result, clean = verify(rows, scratch)
    assert not clean  # no longer recognised as the determination: the file has none
    assert result[84]["without_entry"] == [rows[1]["file"]]
    edit(scratch / rows[2]["path"], lambda v: v["analysis"][-1].__setitem__("body", json.dumps({"outcome": "named"})))
    assert "bad_shape" and len(verify(rows, scratch)[0][84]["bad_shape"]) == 1


def test_entry_that_differs_from_the_plan_is_reported(applied):
    rows, scratch, _ = applied
    changed = copy.deepcopy(rows)
    changed[1]["entry"] = rc.make_entry({**json.loads(rows[1]["entry"]["body"]), "reason": "Another reason."})
    result, clean = verify(changed, scratch)
    assert not clean and "entry differs from the plan" in result[84]["differs_from_plan"][0]
    changed = copy.deepcopy(rows)
    changed[0]["chairs"] = [CHAIRS[0]]
    assert "chair parties differ" in verify(changed, scratch)[0][84]["differs_from_plan"][0]


def test_a_file_the_plan_could_not_determine_is_reported_even_if_it_carries_an_entry(applied):
    rows, scratch, _ = applied
    changed = copy.deepcopy(rows)
    changed[2].update(action="mismatch", entry=None)
    result, clean = verify(changed, scratch)
    assert not clean and "could not determine" in result[84]["differs_from_plan"][0]


def test_legacy_chair_source_keys_are_reported(applied):
    rows, scratch, _ = applied

    def add(v):
        body = json.loads(v["attachments"][0]["body"])
        body["chair_source"] = "x"
        v["attachments"][0]["body"] = json.dumps(body, ensure_ascii=False)

    edit(scratch / rows[2]["path"], add)
    result, clean = verify(rows, scratch)
    assert not clean and result[84]["legacy_chair_source"] == [rows[2]["file"]]


def test_unreadable_and_absent_files_are_differences(applied):
    rows, scratch, _ = applied
    (scratch / rows[0]["path"]).write_text("{nope")
    (scratch / rows[1]["path"]).unlink()
    result, clean = verify(rows, scratch)
    assert not clean and len(result[84]["unreadable"]) == 2


def test_a_meeting_that_could_not_be_checked_is_not_clean(applied):
    rows, scratch, _ = applied
    assert verify(rows, scratch, not_checked=[95])[1] is False
    assert "NOT checked" in rc.verify_text(rc.verify_tree(rows, scratch, [84]), [95])


def test_verify_never_modifies_the_tree(applied):
    rows, scratch, _ = applied
    before = {p.name: p.read_bytes() for p in (scratch / "ietf84").iterdir()}
    verify(rows, scratch)
    assert before == {p.name: p.read_bytes() for p in (scratch / "ietf84").iterdir()}


def run_verify_main(tmp_path, rows, scratch, monkeypatch, *extra):
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    if not (run / "state").exists():
        make_run(run, done=[84])
    monkeypatch.setattr(rc, "compute_plan", lambda args, meetings, root, git_dir: (rows, {"requests_made": 0}))
    return rc.main(["--verify", "--output-root", str(scratch), *inputs(run, lock=False),
                    "--report-dir", str(tmp_path / "reports"), *extra])


def test_main_verify_exit_codes_and_reports(applied, tmp_path, monkeypatch, capsys):
    rows, scratch, pristine = applied
    assert run_verify_main(tmp_path, rows, scratch, monkeypatch) == 0
    assert "verify: clean" in capsys.readouterr().out
    name = rows[0]["file"]
    shutil.copy(pristine / "ietf84" / name, scratch / "ietf84" / name)
    assert run_verify_main(tmp_path, rows, scratch, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "DIFFERENCES FOUND" in out and name in out
    report = json.loads((tmp_path / "reports" / "verify.json").read_text())
    assert report["result"]["84"]["without_entry"] == [name]


def test_main_verify_with_an_incomplete_named_meeting_fails(applied, tmp_path, monkeypatch):
    rows, scratch, _ = applied
    assert run_verify_main(tmp_path, rows, scratch, monkeypatch, "--meetings", "84", "95") == 1


def test_a_file_in_the_directory_that_the_plan_does_not_know_is_checked_too(applied):
    rows, scratch, _ = applied
    write_file(scratch / "ietf84", session_file(), "ietf84_extra_9.vcon.json")  # no entry, not in the plan
    result, clean = verify(rows, scratch)
    assert not clean and result[84]["files"] == 4 and result[84]["without_entry"] == ["ietf84_extra_9.vcon.json"]


def test_a_file_with_an_entry_that_the_plan_does_not_know_is_reported(applied):
    rows, scratch, _ = applied
    entry = json.loads((scratch / rows[1]["path"]).read_text(encoding="utf-8"))
    vcon = session_file()
    vcon["analysis"].append(entry["analysis"][-1])
    write_file(scratch / "ietf84", vcon, "ietf84_extra_9.vcon.json")
    result, clean = verify(rows, scratch)
    assert not clean and "not in the plan" in result[84]["differs_from_plan"][0]


def test_every_file_of_a_completed_meeting_needs_exactly_one_entry(applied):
    rows, scratch, _ = applied
    for path in (scratch / "ietf84").glob("*.vcon.json"):
        edit(path, lambda v: v.__setitem__("analysis", [a for a in v["analysis"] if not rc.is_determination(a)]))
    result, clean = verify(rows, scratch)
    assert not clean and len(result[84]["without_entry"]) == 3


def test_interim_meetings_are_verified_in_their_own_directory(tmp_path):
    name = "interim-2026-vcon-01"
    row_file = f"{name}_vcon_35147.vcon.json"
    write_file(tmp_path / name, session_file(), row_file)
    base = record_row(body_none())
    row = {**base, "meeting": name, "file": row_file, "path": f"{name}/{row_file}", "acronym": "vcon"}
    assert rc.apply_plan([row], tmp_path, NOW)["applied"] == [f"{name}/{row_file}"]
    result = rc.verify_tree([row], tmp_path, [name])
    assert rc.verify_clean(result, []) and result[name]["files"] == 1
    write_file(tmp_path / name, session_file(), f"{name}_vcon_99999.vcon.json")
    assert not rc.verify_clean(rc.verify_tree([row], tmp_path, [name]), [])


def test_main_passes_completed_interim_meetings_to_the_plan_and_verifies_them(tmp_path, monkeypatch, capsys):
    name = "interim-2026-vcon-01"
    scratch = tmp_path / "scratch"
    f = f"{name}_vcon_1.vcon.json"
    write_file(scratch / name, session_file(), f)
    run = tmp_path / "run"
    run.mkdir()
    make_run(run, done=[84, name])
    seen = {}

    def fake_plan(args, meetings, root, git_dir):
        seen["meetings"] = meetings
        return [], {"requests_made": 0}

    monkeypatch.setattr(rc, "compute_plan", fake_plan)
    code = rc.main(["--verify", "--output-root", str(scratch), *inputs(run, lock=False), "--report-dir", str(tmp_path / "r")])
    assert seen["meetings"] == [84, name]
    assert code == 1 and f in capsys.readouterr().out  # the interim file has no entry
    rc.main(["--verify", "--output-root", str(scratch), *inputs(run, lock=False), "--report-dir", str(tmp_path / "r"),
             "--meetings", name])
    assert seen["meetings"] == [name]
