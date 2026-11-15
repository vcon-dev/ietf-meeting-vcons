import copy
import json
import os
from datetime import UTC, datetime

import pytest

import chair_determination as rc
from helpers import T, session_file

NOW = datetime(2026, 11, 15, 9, 30, 0, 123456, tzinfo=UTC)
CHAIRS = [
    {"name": "Olaf Brøan", "mailto": "olaf@example.org"},
    {"name": "Pat Nobody", "mailto": None},
]
DT = {"group": "grp", "group_last_edited": "2013-06-01T00:00:00Z", "snapshot": 200, "snapshot_time": "2011-12-09T20:00:00Z"}
AS_OF = T("2012-03-01T10:00:00")


def body_named(chairs=CHAIRS, basis="minutes-confirmed", datatracker=DT):
    return rc.build_body("named", basis, AS_OF, "session-start", datatracker, chairs, [],
                         "A reason." if basis == "minutes-confirmed" else "", "")


def body_none():
    return rc.build_body("none", "ambiguous-history", AS_OF, "session-start", {"group": "grp", "group_last_edited": "2013-06-01T00:00:00Z"},
                         [], [], "History records share one timestamp with different chairs, and nothing settles it.")


def recover_row(chairs=CHAIRS):
    return {"action": "recover", "entry": rc.make_entry(body_named(chairs)), "chairs": chairs, "path": "x"}


def record_row(entry_body):
    return {"action": "record", "entry": rc.make_entry(entry_body), "chairs": [], "path": "x"}


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "ietf84" / "ietf84_grp_4242.vcon.json"
    path.parent.mkdir()
    path.write_text(rc.file_text(session_file()), encoding="utf-8")
    return path


def test_the_entry_has_exactly_the_agreed_shape():
    entry = rc.make_entry(body_none())
    assert list(entry) == ["type", "sessions", "vendor", "body", "encoding", "mediatype", "product", "schema"]
    assert (entry["type"], entry["sessions"], entry["vendor"]) == ("report", 0, "ietf2vcon")
    assert (entry["encoding"], entry["mediatype"], entry["product"], entry["schema"]) == (
        "json", "application/json", "chair-determination", "chair-determination-v1")
    assert isinstance(entry["body"], str) and json.loads(entry["body"])["outcome"] == "none"
    assert entry["body"] == json.dumps(json.loads(entry["body"]), ensure_ascii=False)
    assert rc.entry_problems(entry) == []
    assert list(json.loads(entry["body"])) == ["outcome", "basis", "as_of", "as_of_basis", "datatracker", "reason"]


def test_body_and_entry_shape_problems_are_found():
    entry = rc.make_entry(body_none())
    assert rc.entry_problems({**entry, "extra": 1})
    assert rc.entry_problems({k: v for k, v in entry.items() if k != "product"})
    bad = json.loads(entry["body"])
    for change, text in [
        ({"basis": "made-up"}, "basis"),
        ({"outcome": "named"}, "outcome and basis"),
        ({"as_of_basis": "whenever"}, "as_of_basis"),
        ({"as_of": "yesterday"}, "RFC 3339"),
        ({"reason": ""}, "needs a reason"),
    ]:
        assert any(text in p for p in rc.body_problems({**bad, **change})), change
    assert any("fixed order" in p for p in rc.body_problems(dict(reversed(list(bad.items())))))
    named = json.loads(rc.make_entry(body_named())["body"])
    assert rc.body_problems({k: v for k, v in named.items() if k != "chairs"})
    assert rc.body_problems({**named, "candidates": [{"source": "invented"}]})
    assert rc.body_problems({**named, "candidates": [{"source": "proceedings-page", "chairs": [], "url": "u"}]})


def test_recording_a_named_session_adds_the_chairs_and_the_entry_only(target):
    old = json.loads(target.read_text(encoding="utf-8"))
    row = recover_row()
    assert rc.apply_file(target, row, NOW) == "applied"
    text = target.read_text(encoding="utf-8")
    new = json.loads(text)
    assert text == json.dumps(new, indent=2, ensure_ascii=False) + "\n" and "Brøan" in text
    assert new["parties"] == [
        {"mailto": "olaf@example.org", "name": "Olaf Brøan", "role": "chair"},
        {"name": "Pat Nobody", "role": "chair"},
        {"name": "IETF Attendees", "role": "attendee"},
    ]
    assert [list(p) for p in new["parties"]] == [["mailto", "name", "role"], ["name", "role"], ["name", "role"]]
    assert new["sessions"][0]["parties"] == [0, 1, 2] and new["dialog"][0]["parties"] == [0, 1, 2]
    assert all(a["party"] == 0 for a in new["attachments"])
    assert new["attachments"] == old["attachments"]  # meeting_metadata and the rest are untouched
    assert new["extensions"] == old["extensions"]
    assert new["analysis"][:-1] == old["analysis"] and new["analysis"][-1] == row["entry"]
    assert len(new["analysis"]) == len(old["analysis"]) + 1
    assert new["updated_at"] == NOW.isoformat() and new["updated_at"] != old["updated_at"]
    for key in ("vcon", "uuid", "created_at", "subject"):
        assert new[key] == old[key]
    assert list(new) == list(old)
    assert rc.verify_only_expected(old, new, CHAIRS, row["entry"]) == []


def test_recording_an_omitted_session_changes_nothing_but_the_entry_and_updated_at(target):
    old = json.loads(target.read_text(encoding="utf-8"))
    row = record_row(body_none())
    assert rc.apply_file(target, row, NOW) == "applied"
    new = json.loads(target.read_text(encoding="utf-8"))
    for key in old:
        if key not in ("analysis", "updated_at"):
            assert new[key] == old[key], key
    assert new["analysis"] == old["analysis"] + [row["entry"]]
    assert new["parties"] == [{"name": "IETF Attendees", "role": "attendee"}]


def test_the_entry_is_appended_last_even_without_other_analysis(tmp_path):
    path = tmp_path / "f.vcon.json"
    path.write_text(rc.file_text(session_file(dialog=False)), encoding="utf-8")
    rc.apply_file(path, record_row(body_none()), NOW)
    new = json.loads(path.read_text(encoding="utf-8"))
    assert len(new["analysis"]) == 1 and rc.is_determination(new["analysis"][0])


def test_a_named_record_for_a_file_that_already_has_those_chairs(tmp_path):
    chairs = [{"name": "Ann One", "mailto": "ann@example.org"}]
    vcon = session_file([("Ann One", "ann@example.org")])
    path = tmp_path / "f.vcon.json"
    path.write_text(rc.file_text(vcon), encoding="utf-8")
    row = record_row(body_named(chairs, basis="history-snapshot"))
    assert rc.apply_file(path, row, NOW) == "applied"
    new = json.loads(path.read_text(encoding="utf-8"))
    assert new["parties"] == vcon["parties"] and new["sessions"] == vcon["sessions"]
    # and refuses when the file's chairs are not the ones the record names
    other = tmp_path / "g.vcon.json"
    other.write_text(rc.file_text(session_file([("Cy Three", "cy@example.org")])), encoding="utf-8")
    before = other.read_bytes()
    with pytest.raises(rc.ApplyError, match="not what the record would name"):
        rc.apply_file(other, row, NOW)
    assert other.read_bytes() == before


def test_idempotent(target):
    row = recover_row()
    rc.apply_file(target, row, NOW)
    before, stamp = target.read_bytes(), target.stat().st_mtime_ns
    assert rc.apply_file(target, row, datetime(2027, 1, 1, tzinfo=UTC)) == "already recorded"
    assert target.read_bytes() == before and target.stat().st_mtime_ns == stamp
    assert len(json.loads(before)["analysis"]) == 2


def test_an_existing_entry_is_verified_not_duplicated(target):
    rc.apply_file(target, record_row(body_none()), NOW)
    before = target.read_bytes()
    with pytest.raises(rc.ApplyError, match="differs from the plan"):
        rc.apply_file(target, recover_row(), NOW)  # a different plan for a file that already has an entry
    assert target.read_bytes() == before
    vcon = json.loads(before)
    vcon["analysis"].append(copy.deepcopy(vcon["analysis"][-1]))
    target.write_text(rc.file_text(vcon), encoding="utf-8")
    with pytest.raises(rc.ApplyError, match="2 chair-determination entries"):
        rc.apply_file(target, record_row(body_none()), NOW)


def test_an_existing_entry_whose_chairs_disagree_with_the_parties_is_an_error_for_a_record_only_file(tmp_path):
    row = record_row(body_named([{"name": "Ann One", "mailto": "ann@example.org"}], basis="history-snapshot"))
    vcon = session_file()
    vcon["analysis"].append(row["entry"])  # entry names a chair, the parties have none, and no recovery is planned
    path = tmp_path / "f.vcon.json"
    path.write_text(rc.file_text(vcon), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(rc.ApplyError, match="disagree"):
        rc.apply_file(path, row, NOW)
    assert path.read_bytes() == before


def test_chairs_removed_by_a_later_conversion_are_restored_without_a_second_entry(target):
    row = recover_row()
    rc.apply_file(target, row, NOW)
    applied = json.loads(target.read_text(encoding="utf-8"))
    # what a converter re-run leaves: no chairs (the history is ambiguous again), the entry carried across
    rerun = session_file()
    rerun["analysis"] = applied["analysis"]
    rerun["updated_at"] = "2026-11-16T00:00:00+00:00"
    target.write_text(rc.file_text(rerun), encoding="utf-8")
    later = datetime(2026, 11, 17, tzinfo=UTC)
    assert rc.apply_file(target, row, later) == "restored"
    restored = json.loads(target.read_text(encoding="utf-8"))
    assert restored["parties"] == applied["parties"] and restored["sessions"] == applied["sessions"]
    assert restored["dialog"] == applied["dialog"] and restored["analysis"] == applied["analysis"]
    assert len([a for a in restored["analysis"] if rc.is_determination(a)]) == 1
    assert restored["updated_at"] == later.isoformat()
    assert rc.apply_file(target, row, later) == "already recorded"


def test_a_restore_is_refused_for_anything_but_a_chairless_file(target):
    row = recover_row()
    rc.apply_file(target, row, NOW)
    vcon = json.loads(target.read_text(encoding="utf-8"))
    vcon["parties"][0]["name"] = "Someone Else"  # chairs present, but not the planned ones
    target.write_text(rc.file_text(vcon), encoding="utf-8")
    with pytest.raises(rc.ApplyError, match="disagree"):
        rc.apply_file(target, row, NOW)


def test_confirmed_chairs_are_refused_for_a_file_that_already_has_a_chair_party(tmp_path):
    vcon = session_file([("Someone Else", "x@example.org")])
    path = tmp_path / "f.vcon.json"
    path.write_text(rc.file_text(vcon), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(rc.ApplyError, match="already has a chair party"):
        rc.apply_file(path, recover_row(), NOW)
    assert path.read_bytes() == before


def test_guard_trips_when_something_else_changes(target, monkeypatch):
    before = target.read_bytes()
    real = rc.build_updated

    def tampering(*args, **kwargs):
        new = real(*args, **kwargs)
        new["dialog"][0]["duration"] = 1
        return new

    monkeypatch.setattr(rc, "build_updated", tampering)
    with pytest.raises(rc.ApplyError, match="Dialog changed beyond parties"):
        rc.apply_file(target, recover_row(), NOW)
    assert target.read_bytes() == before


@pytest.mark.parametrize("tamper,expect", [
    (lambda n: n.__setitem__("uuid", "other"), "uuid changed"),
    (lambda n: n.__setitem__("created_at", "2000-01-01T00:00:00+00:00"), "created_at changed"),
    (lambda n: n["analysis"][0].__setitem__("body", "{x}"), "analysis is not"),
    (lambda n: n["analysis"].append({"type": "report"}), "analysis is not"),
    (lambda n: n["analysis"].pop(), "analysis is not"),
    (lambda n: n["attachments"][0].__setitem__("body", "{}"), "attachments changed"),
    (lambda n: n["attachments"][2].__setitem__("url", "https://example.org/other"), "attachments changed"),
    (lambda n: n["attachments"].pop(), "attachments changed"),
    (lambda n: n["extensions"].pop(), "extensions changed"),
    (lambda n: n["sessions"][0].__setitem__("start", "2000-01-01T00:00:00+00:00"), "session changed"),
    (lambda n: n["parties"][-1].__setitem__("name", "Renamed"), "original parties"),
    (lambda n: n["dialog"][0].__setitem__("parties", [0]), "Dialog's parties"),
    (lambda n: n.__setitem__("updated_at", "2026-11-14T22:28:48.273453+00:00"), "updated_at"),
])
def test_verify_only_expected_catches_each_kind_of_change(tamper, expect):
    old = session_file()
    row = recover_row()
    new = rc.build_updated(old, row["entry"], CHAIRS, NOW)
    assert rc.verify_only_expected(old, copy.deepcopy(new), CHAIRS, row["entry"]) == []
    tamper(new)
    problems = rc.verify_only_expected(old, new, CHAIRS, row["entry"])
    assert any(expect in p for p in problems), problems


def test_guard_for_an_omitted_session_allows_no_party_change():
    old = session_file()
    row = record_row(body_none())
    new = rc.build_updated(old, row["entry"], [], NOW)
    assert rc.verify_only_expected(old, new, [], row["entry"]) == []
    new["parties"].insert(0, rc.chair_party(CHAIRS[0]))
    assert any("parties changed" in p for p in rc.verify_only_expected(old, new, [], row["entry"]))
    new = rc.build_updated(old, row["entry"], [], NOW)
    new["sessions"][0]["parties"] = [0, 1]
    assert any("sessions parties" in p for p in rc.verify_only_expected(old, new, [], row["entry"]))


def test_unexpected_shapes_are_refused_not_guessed():
    entry = recover_row()["entry"]
    for mutate, message in [
        (lambda v: v["attachments"][1].__setitem__("party", 1), "attachment 1 party"),
        (lambda v: v["dialog"][0].__setitem__("parties", [0, 1]), "dialog 0 parties"),
        (lambda v: v["parties"].append({"name": "Other", "role": "attendee"}), "exactly one party"),
        (lambda v: v["sessions"][0].__setitem__("parties", [0, 1]), "sessions\\[0\\].parties"),
        (lambda v: v.__setitem__("analysis", None), "analysis is not a list"),
    ]:
        vcon = session_file()
        mutate(vcon)
        with pytest.raises(rc.ApplyError, match=message):
            rc.build_updated(vcon, entry, CHAIRS, NOW)


def test_file_not_in_converter_layout_is_refused(target):
    target.write_text(json.dumps(session_file(), indent=4), encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(rc.ApplyError, match="layout"):
        rc.apply_file(target, recover_row(), NOW)
    assert target.read_bytes() == before


def test_unreadable_file_and_missing_plan_entry_are_errors(tmp_path):
    bad = tmp_path / "bad.vcon.json"
    bad.write_text("{not json")
    with pytest.raises(rc.ApplyError, match="cannot read"):
        rc.apply_file(bad, recover_row(), NOW)
    with pytest.raises(rc.ApplyError, match="cannot read"):
        rc.apply_file(tmp_path / "absent.vcon.json", recover_row(), NOW)
    with pytest.raises(rc.ApplyError, match="no entry"):
        rc.apply_file(bad, {"action": "mismatch", "entry": None}, NOW)


def test_write_is_atomic_when_the_replace_fails(target, monkeypatch):
    before = target.read_bytes()

    def broken(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        rc.apply_file(target, recover_row(), NOW)
    assert target.read_bytes() == before
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_atomic_write_uses_a_hidden_temporary_in_the_same_directory(tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda s, d: (seen.append((os.path.dirname(s), os.path.basename(s))), real(s, d)))
    target = tmp_path / "x.vcon.json"
    rc.atomic_write_text(target, "hello\n")
    assert seen[0][0] == str(tmp_path) and seen[0][1].startswith(".") and not seen[0][1].endswith(".vcon.json")


def test_chairs_sharing_an_address_are_merged_as_the_builder_does(tmp_path):
    path = tmp_path / "f.vcon.json"
    path.write_text(rc.file_text(session_file()), encoding="utf-8")
    chairs = [{"name": "Ann One", "mailto": "shared@example.org"}, {"name": "Bo Two", "mailto": "shared@example.org"},
              {"name": "Cy Three", "mailto": "cy@example.org"}]
    deduped = rc.converter_dedupe(chairs)
    row = {"action": "recover", "chairs": chairs, "entry": rc.make_entry(body_named(deduped))}
    rc.apply_file(path, row, NOW)
    new = json.loads(path.read_text(encoding="utf-8"))
    assert [p["name"] for p in new["parties"]] == ["Ann One", "Cy Three", "IETF Attendees"]


def test_apply_plan_reports_applied_recorded_errors_and_unwritten(tmp_path):
    root = tmp_path
    (root / "ietf84").mkdir()
    for name in ("a", "b"):
        (root / "ietf84" / f"ietf84_{name}_1.vcon.json").write_text(rc.file_text(session_file()), encoding="utf-8")
    rows = [
        {**recover_row(), "path": "ietf84/ietf84_a_1.vcon.json"},
        {**record_row(body_none()), "path": "ietf84/ietf84_b_1.vcon.json"},
        {**record_row(body_none()), "path": "ietf84/ietf84_zzz_1.vcon.json"},
        {"action": "mismatch", "entry": None, "chairs": [], "path": "ietf84/ietf84_m_1.vcon.json"},
    ]
    first = rc.apply_plan(rows, root, NOW)
    assert first["applied"] == ["ietf84/ietf84_a_1.vcon.json", "ietf84/ietf84_b_1.vcon.json"]
    assert len(first["errors"]) == 1 and first["not written"] == ["ietf84/ietf84_m_1.vcon.json"]
    second = rc.apply_plan(rows, root, NOW)
    assert second["applied"] == [] and len(second["already recorded"]) == 2


# ---- the deliberate update path

def stale_file(tmp_path, with_neighbours=True, chairs=True):
    """A file as an earlier run left it: the entry holds an older body, sitting between two other entries."""
    row = recover_row() if chairs else record_row(body_none())
    old_body = json.loads(row["entry"]["body"])
    old_body.pop("candidates", None)
    stale = rc.make_entry(old_body)
    vcon = session_file(([("Olaf Brøan", "olaf@example.org"), ("Pat Nobody", None)] if chairs else []))
    if with_neighbours:
        vcon["analysis"] = [vcon["analysis"][0], stale, {"type": "summary", "vendor": "x", "body": "s", "encoding": "none"}]
    else:
        vcon["analysis"] = [stale]
    path = tmp_path / "f.vcon.json"
    path.write_text(rc.file_text(vcon), encoding="utf-8")
    return path, row, stale


def with_candidates(row):
    body = json.loads(row["entry"]["body"])
    body["candidates"] = [{"source": "proceedings-page", "url": "https://www.ietf.org/proceedings/84/grp.html",
                           "chairs": [{"name": "Olaf Brøan", "mailto": "olaf@example.org"}]}]
    body = {k: body[k] for k in rc._BODY_ORDER if k in body}
    return {**row, "entry": rc.make_entry(body)}


def test_an_update_replaces_only_the_body_of_the_one_entry_in_place(tmp_path):
    path, row, stale = stale_file(tmp_path)
    new_row = with_candidates(row)
    old = json.loads(path.read_text(encoding="utf-8"))
    info = {}
    assert rc.apply_file(path, new_row, NOW, update=True, report=info) == "updated"
    assert info["changes"] == {"added": ["candidates"], "removed": [], "changed": []}
    text = path.read_text(encoding="utf-8")
    new = json.loads(text)
    assert text == json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    assert new["analysis"][1] == new_row["entry"] and rc.is_determination(new["analysis"][1])  # same position
    assert new["analysis"][0] == old["analysis"][0] and new["analysis"][2] == old["analysis"][2]
    assert list(new["analysis"][1]) == list(stale)
    assert new["updated_at"] == NOW.isoformat() and new["updated_at"] != old["updated_at"]
    for key in old:
        if key not in ("analysis", "updated_at"):
            assert new[key] == old[key], key
    assert rc.verify_only_body_replaced(old, new, 1, new_row["entry"]) == []


def test_an_update_is_a_no_op_when_the_record_is_current(tmp_path):
    path, row, stale = stale_file(tmp_path)
    new_row = with_candidates(row)
    rc.apply_file(path, new_row, NOW, update=True)
    before, stamp = path.read_bytes(), path.stat().st_mtime_ns
    assert rc.apply_file(path, new_row, datetime(2027, 1, 1, tzinfo=UTC), update=True) == "already recorded"
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stamp


def test_without_the_flag_a_stale_record_is_an_error_as_before(tmp_path):
    path, row, _ = stale_file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(rc.ApplyError, match="differs from the plan"):
        rc.apply_file(path, with_candidates(row), NOW)
    assert path.read_bytes() == before


def test_an_update_works_for_a_record_only_file_too(tmp_path):
    path, row, stale = stale_file(tmp_path, with_neighbours=False, chairs=False)
    body = json.loads(row["entry"]["body"])
    body["reason"] = "Another reason for the same outcome."
    new_row = {**row, "entry": rc.make_entry(body)}
    info = {}
    assert rc.apply_file(path, new_row, NOW, update=True, report=info) == "updated"
    assert info["changes"]["changed"] == ["reason"]
    assert json.loads(path.read_text(encoding="utf-8"))["analysis"][0] == new_row["entry"]


def test_an_update_is_refused_when_anything_else_would_change(tmp_path, monkeypatch):
    path, row, stale = stale_file(tmp_path)
    new_row = with_candidates(row)
    before = path.read_bytes()
    # a planned entry that differs in a key other than the body
    other = {**new_row, "entry": {**new_row["entry"], "product": "something-else"}}
    with pytest.raises(rc.ApplyError):
        rc.apply_file(path, other, NOW, update=True)
    # a planned record whose chairs are not the chairs in the file
    body = json.loads(new_row["entry"]["body"])
    body["chairs"] = [{"name": "Someone Else", "mailto": "x@example.org"}]
    body = {k: body[k] for k in rc._BODY_ORDER if k in body}
    with pytest.raises(rc.ApplyError, match="disagree"):
        rc.apply_file(path, {**new_row, "entry": rc.make_entry(body)}, NOW, update=True)
    assert path.read_bytes() == before
    # the guard trips if the replacement touches anything else
    monkeypatch.setattr(rc, "verify_only_body_replaced", lambda old, new, index, entry: ["tripped"])
    with pytest.raises(rc.ApplyError, match="tripped"):
        rc.apply_file(path, new_row, NOW, update=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("tamper,expect", [
    (lambda n: n["parties"].pop(), "parties changed"),
    (lambda n: n["sessions"][0].__setitem__("parties", [0]), "sessions changed"),
    (lambda n: n["dialog"][0].__setitem__("duration", 1), "dialog changed"),
    (lambda n: n["analysis"][0].__setitem__("body", "{x}"), "analysis entry 0 changed"),
    (lambda n: n["analysis"][2].__setitem__("body", "x"), "analysis entry 2 changed"),
    (lambda n: n["analysis"].pop(), "analysis count"),
    (lambda n: n["analysis"][1].__setitem__("vendor", "other"), "beyond its body"),
    (lambda n: n["analysis"][1].__setitem__("body", "{}"), "not the planned body"),
    (lambda n: n["attachments"][0].__setitem__("body", "{}"), "attachments changed"),
    (lambda n: n.__setitem__("uuid", "x"), "uuid changed"),
    (lambda n: n.__setitem__("created_at", "x"), "created_at changed"),
    (lambda n: n.__setitem__("updated_at", "2026-11-14T22:28:48.273453+00:00"), "updated_at"),
])
def test_the_update_guard_catches_each_kind_of_extra_change(tmp_path, tamper, expect):
    path, row, _ = stale_file(tmp_path)
    new_row = with_candidates(row)
    old = json.loads(path.read_text(encoding="utf-8"))
    new = copy.deepcopy(old)
    new["analysis"][1]["body"] = new_row["entry"]["body"]
    new["updated_at"] = NOW.isoformat()
    assert rc.verify_only_body_replaced(old, new, 1, new_row["entry"]) == []
    tamper(new)
    assert any(expect in p for p in rc.verify_only_body_replaced(old, new, 1, new_row["entry"])), expect


def test_an_update_needs_the_file_layout_and_one_entry(tmp_path):
    path, row, _ = stale_file(tmp_path)
    new_row = with_candidates(row)
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=4), encoding="utf-8")
    with pytest.raises(rc.ApplyError, match="layout"):
        rc.apply_file(path, new_row, NOW, update=True)
    vcon = json.loads(path.read_text(encoding="utf-8"))
    vcon["analysis"].append(copy.deepcopy(vcon["analysis"][1]))
    path.write_text(rc.file_text(vcon), encoding="utf-8")
    with pytest.raises(rc.ApplyError, match="2 chair-determination"):
        rc.apply_file(path, new_row, NOW, update=True)


def test_dry_run_checks_everything_and_writes_nothing(tmp_path, target):
    before = target.read_bytes()
    assert rc.apply_file(target, recover_row(), NOW, dry_run=True) == "applied"
    assert target.read_bytes() == before
    path, row, _ = stale_file(tmp_path)
    before = path.read_bytes()
    assert rc.apply_file(path, with_candidates(row), NOW, update=True, dry_run=True) == "updated"
    assert path.read_bytes() == before
    with pytest.raises(rc.ApplyError):  # a dry run still refuses what a real run would refuse
        rc.apply_file(path, with_candidates(row), NOW, dry_run=True)


def test_apply_plan_counts_updates_and_keeps_the_changes_per_file(tmp_path):
    path, row, _ = stale_file(tmp_path)
    rows = [{**with_candidates(row), "path": "f.vcon.json"}]
    dry = rc.apply_plan(rows, tmp_path, NOW, update=True, dry_run=True)
    assert dry["updated"] == ["f.vcon.json"] and dry["changes"]["f.vcon.json"]["added"] == ["candidates"]
    assert rc.apply_plan(rows, tmp_path, NOW)["errors"]  # without the flag
    assert rc.apply_plan(rows, tmp_path, NOW, update=True)["updated"] == ["f.vcon.json"]
    assert rc.apply_plan(rows, tmp_path, NOW, update=True)["already recorded"] == ["f.vcon.json"]
