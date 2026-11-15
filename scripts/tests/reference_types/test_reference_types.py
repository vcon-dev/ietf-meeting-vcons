import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import reference_types as pp  # noqa: E402

NOW = "2030-01-01T00:00:00.000000+00:00"
OLD_UPDATED = "2026-11-15T03:29:45.555522+00:00"


class StubPrevious:
    def __init__(self, table=None):
        self.table = table or {}

    def body_ref_mimetypes(self, relpath):
        return self.table.get(relpath)


def att_body(purpose, body):
    return {"purpose": purpose, "body": json.dumps(body, ensure_ascii=False), "encoding": "json",
            "party": 0, "dialog": 0, "start": "2026-01-01T00:00:00+00:00", "mediatype": "application/json"}


def ref(url, mimetype="application/pdf", filename=None, title="T", purpose="slides"):
    return att_body(purpose, {"url": url, "mimetype": mimetype, "filename": filename, "title": title})


def hashed(url="https://www.ietf.org/proceedings/1/slides/a.pdf"):
    return {"purpose": "slides", "party": 0, "dialog": 0, "start": "2026-01-01T00:00:00+00:00", "url": url,
            "content_hash": "sha512-abc", "mediatype": "application/pdf", "filename": "a.pdf",
            "meta": {"title": "x"}}


def vcon(extra_atts=(), status="sched", with_recording=False, session_start=True, meeting=104,
         group="core", sid="1"):
    md = {"ietf_meeting_number": meeting, "working_group": group, "session_id": sid}
    if status is not None:
        md["status"] = status
    sess = {"parties": [0], "dialog": [0] if with_recording else []}
    if session_start:
        sess["start"] = "2026-01-01T10:00:00+00:00"
    dialog = [{"type": "recording", "start": "2026-01-01T10:00:00+00:00", "parties": [0],
               "mediatype": "audio/mpeg", "url": "https://x/a.mp3"}] if with_recording else []
    return {
        "vcon": "0.5.0", "uuid": "0195e1f0-0000-7000-8000-000000000001",
        "parties": [{"name": "Zoë"}], "dialog": dialog,
        "attachments": [att_body("meeting_metadata", md),
                        att_body("ingress_info", {"source": "x"}),
                        att_body("lawful_basis", {"lawful_basis": "legitimate_interests"})] + list(extra_atts),
        "analysis": [], "created_at": "2026-01-01T00:00:00+00:00", "sessions": [sess],
        "subject": "core at IETF 104", "extensions": ["ext"], "updated_at": OLD_UPDATED,
    }


def write_tree(tmp_path, files):
    root = tmp_path / "tree"
    for rel, obj in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(pp.dumps_file(obj), encoding="utf-8")
    return str(root)


def make_ctx(previous=None, mirror=None, group_c=()):
    return pp.Context(previous=previous, mirror=mirror, group_c=group_c, now=lambda: NOW)


def run_one(tmp_path, obj, ctx=None, rel="ietf104/ietf104_core_1.vcon.json", base_table=None, apply=True):
    root = write_tree(tmp_path, {rel: obj})
    ctx = ctx or make_ctx()
    loader = StubPrevious(base_table).body_ref_mimetypes
    res = pp.process_file(root, rel, ctx, apply=apply, base_map_loader=loader)
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        after = json.load(f)
    return res, after, root, ctx


def bodies(obj):
    return [json.loads(a["body"]) for a in obj["attachments"] if "body" in a]


def ref_bodies(obj):
    return [json.loads(a["body"]) for a in obj["attachments"] if pp.is_body_ref(a)]


# ---------- correction 1 ----------

@pytest.mark.parametrize("filename,expected", [
    ("a.html", "text/html"), ("a.HTM", "text/html"), ("a.txt", "text/plain"), ("a.md", "text/markdown"),
    ("a.pdf", "application/pdf"), ("a.json", "application/json"), ("a.xml", "application/xml"),
    ("a.ppt", "application/vnd.ms-powerpoint"),
    ("a.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("a.doc", "application/msword"),
    ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("a.odp", "application/vnd.oasis.opendocument.presentation"),
    ("a.odt", "application/vnd.oasis.opendocument.text"),
    ("a.key", "application/vnd.apple.keynote"), ("a.zip", "application/zip"),
    ("a.png", "image/png"), ("a.jpg", "image/jpeg"), ("a.jpeg", "image/jpeg"), ("a.gif", "image/gif"),
    ("a.mp3", "audio/mpeg"), ("a.mp4", "video/mp4"), ("a.tar.gz", "application/gzip"),
])
def test_rule1_extension_table(tmp_path, filename, expected):
    obj = vcon([ref("https://dt/meeting/104/materials/x", filename=filename)])
    res, after, _, _ = run_one(tmp_path, obj)
    assert not res["errors"]
    assert ref_bodies(after)[0]["mimetype"] == expected
    assert [c["rule"] for c in res["changes"]] == ["1"]


def test_rule1_wins_over_previous(tmp_path):
    url = "https://dt/meeting/104/materials/x"
    obj = vcon([ref(url, filename="x.pptx")])
    res, after, _, _ = run_one(tmp_path, obj, base_table={"ietf104/ietf104_core_1.vcon.json": {url: {"text/html"}}})
    assert ref_bodies(after)[0]["mimetype"].endswith("presentationml.presentation")
    assert res["changes"][0]["rule"] == "1"


def test_rule1_unmapped_extension_falls_through_and_is_listed(tmp_path):
    obj = vcon([ref("https://dt/meeting/104/materials/x", filename="x.weird")])
    res, after, _, ctx = run_one(tmp_path, obj)
    assert "mimetype" not in ref_bodies(after)[0]
    assert res["changes"][0]["rule"] == "4"
    assert ctx.unmapped_ext[".weird"] == 1


def test_rule2_previous_value(tmp_path):
    url = "https://dt/meeting/104/materials/agenda-104-core"
    obj = vcon([ref(url, purpose="agenda")])
    res, after, _, _ = run_one(tmp_path, obj, base_table={"ietf104/ietf104_core_1.vcon.json": {url: {"text/html"}}})
    assert ref_bodies(after)[0]["mimetype"] == "text/html"
    assert res["changes"][0]["rule"] == "2"


def test_rule2_previous_pdf_is_kept_unchanged_when_asked(tmp_path):
    url = "https://dt/meeting/104/materials/x"
    obj = vcon([ref(url)])
    ctx = pp.Context(group_c=(), now=lambda: NOW, previous_pdf="keep")
    res, after, _, _ = run_one(tmp_path, obj, ctx=ctx, base_table={
        "ietf104/ietf104_core_1.vcon.json": {url: {"application/pdf"}}})
    assert res["changes"][0]["rule"] == "2" and not res["changes"][0]["changed"]
    assert after["updated_at"] == OLD_UPDATED


def test_rule2_previous_pdf_is_ignored_by_default(tmp_path):
    url = "https://dt/meeting/104/materials/x"
    obj = vcon([ref(url)])
    assert pp.Context().previous_pdf == "ignore"
    res, after, _, _ = run_one(tmp_path, obj, base_table={
        "ietf104/ietf104_core_1.vcon.json": {url: {"application/pdf"}}})
    assert res["changes"][0]["rule"] == "4" and "mimetype" not in ref_bodies(after)[0]


def test_rule2_other_previous_values_are_used_by_default(tmp_path):
    url = "https://dt/meeting/104/materials/x"
    res, after, _, _ = run_one(tmp_path, vcon([ref(url)]), base_table={
        "ietf104/ietf104_core_1.vcon.json": {url: {"text/html"}}})
    assert res["changes"][0]["rule"] == "2" and ref_bodies(after)[0]["mimetype"] == "text/html"


def test_rule2_ambiguous_previous_goes_to_next_rule(tmp_path):
    url = "https://dt/meeting/104/materials/x"
    obj = vcon([ref(url)])
    ctx = pp.Context(group_c=(), now=lambda: NOW, previous_pdf="keep")
    res, after, _, _ = run_one(tmp_path, obj, ctx=ctx, base_table={
        "ietf104/ietf104_core_1.vcon.json": {url: {"text/html", "application/pdf"}}})
    assert res["changes"][0]["rule"] == "4"


def test_rule2_needs_same_url(tmp_path):
    obj = vcon([ref("https://dt/meeting/104/materials/x")])
    res, _, _, _ = run_one(tmp_path, obj, base_table={
        "ietf104/ietf104_core_1.vcon.json": {"https://dt/other": {"text/html"}}})
    assert res["changes"][0]["rule"] == "4"


def make_mirror(tmp_path, files):
    root = tmp_path / "mirror"
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return pp.MirrorIndex(str(root))


def test_rule3_mirror_unique(tmp_path):
    mirror = make_mirror(tmp_path, ["104/slides/slides-104-core-1-00.pptx", "104/slides/slides-104-core-10-00.pdf"])
    obj = vcon([ref("https://dt/meeting/104/materials/slides-104-core-1")])
    res, after, _, ctx = run_one(tmp_path, obj, ctx=make_ctx(mirror=mirror))
    assert res["changes"][0]["rule"] == "3"
    assert ref_bodies(after)[0]["mimetype"].endswith("presentationml.presentation")
    assert res["changes"][0]["detail"] == "slides/slides-104-core-1-00.pptx"


def test_rule3_ambiguous_removes(tmp_path):
    mirror = make_mirror(tmp_path, ["104/agenda/agenda-104-core-00.txt", "104/agenda/agenda-104-core-01.html"])
    obj = vcon([ref("https://dt/meeting/104/materials/agenda-104-core", purpose="agenda")])
    res, after, _, ctx = run_one(tmp_path, obj, ctx=make_ctx(mirror=mirror))
    assert res["changes"][0]["rule"] == "4"
    assert "mimetype" not in ref_bodies(after)[0]
    assert len(ctx.rule3_ambiguous) == 1


def test_rule3_wrong_meeting_not_used(tmp_path):
    mirror = make_mirror(tmp_path, ["105/slides/slides-104-core-1.pdf"])
    obj = vcon([ref("https://dt/meeting/104/materials/slides-104-core-1")])
    res, _, _, _ = run_one(tmp_path, obj, ctx=make_ctx(mirror=mirror))
    assert res["changes"][0]["rule"] == "4"


def test_rule3_unmapped_mirror_extension_removes(tmp_path):
    mirror = make_mirror(tmp_path, ["104/slides/slides-104-core-1.xyz"])
    obj = vcon([ref("https://dt/meeting/104/materials/slides-104-core-1")])
    res, _, _, ctx = run_one(tmp_path, obj, ctx=make_ctx(mirror=mirror))
    assert res["changes"][0]["rule"] == "4"
    assert ctx.rule3_unmapped


def test_rule4_removes_member_and_keeps_other_members_in_order(tmp_path):
    obj = vcon([ref("https://dt/meeting/104/materials/x", title="Zoë")])
    res, after, _, _ = run_one(tmp_path, obj)
    b = ref_bodies(after)[0]
    assert list(b) == ["url", "filename", "title"] and b["title"] == "Zoë"
    assert after["updated_at"] == NOW


def test_non_default_mimetypes_without_filename_are_untouched(tmp_path):
    obj = vcon([ref("https://dt/meeting/104/session/core/", mimetype="text/html", purpose="agenda"),
                ref("https://notes.ietf.org/notes-ietf-104-core", mimetype="text/markdown", purpose="minutes")])
    res, after, root, _ = run_one(tmp_path, obj)
    assert res["changes"] == [] and not res["wrote"]
    assert after == obj


def test_non_default_mimetype_with_matching_filename_is_unchanged(tmp_path):
    obj = vcon([ref("https://dt/x", mimetype="application/vnd.ms-powerpoint", filename="a.ppt")])
    res, after, _, _ = run_one(tmp_path, obj)
    assert res["changes"] == [] and after == obj


def test_untouched_attachment_kinds(tmp_path):
    protected = [
        att_body("meeting_metadata", {"ietf_meeting_number": 104, "url": "u", "mimetype": "application/pdf"}),
        att_body("ingress_info", {"url": "u", "mimetype": "application/pdf"}),
        att_body("lawful_basis", {"url": "u", "mimetype": "application/pdf"}),
    ]
    h = hashed()
    h["mediatype"] = "application/pdf"
    obj = vcon([h, ref("https://dt/meeting/104/materials/x", filename="x.pptx")])
    obj["attachments"][:3] = protected
    res, after, _, _ = run_one(tmp_path, obj)
    assert after["attachments"][:3] == protected
    assert after["attachments"][3] == h
    assert len(res["changes"]) == 1 and res["changes"][0]["att_index"] == 4


def test_attachment_with_url_and_body_is_not_a_body_ref(tmp_path):
    a = ref("https://dt/x")
    a["url"] = "https://dt/hashed"
    obj = vcon([a])
    res, after, _, _ = run_one(tmp_path, obj)
    assert res["changes"] == [] and after == obj


# ---------- correction 2 ----------

def note_of(obj):
    return json.loads(obj["attachments"][0]["body"]).get("recording_note")


def test_group_a(tmp_path):
    for status in ("canceled", "resched", "schedw", "notmeet"):
        d = tmp_path / status
        d.mkdir()
        res, after, _, _ = run_one(d, vcon(status=status, with_recording=True))
        assert note_of(after) == pp.NOTE_A
        assert [c["group"] for c in res["changes"]] == ["A"]
        assert after["updated_at"] == NOW


def test_group_a_needs_recording_and_status(tmp_path):
    (tmp_path / "a").mkdir()
    res, after, _, _ = run_one(tmp_path / "a", vcon(status="canceled", with_recording=False))
    assert note_of(after) is None and res["changes"] == []
    (tmp_path / "b").mkdir()
    res, after, _, _ = run_one(tmp_path / "b", vcon(status=None, with_recording=True))
    assert note_of(after) is None and res["changes"] == []


def test_group_b(tmp_path):
    res, after, _, _ = run_one(tmp_path, vcon(status="sched", with_recording=True, session_start=False))
    assert note_of(after) == pp.NOTE_B
    assert [c["group"] for c in res["changes"]] == ["B"]


def test_sched_with_start_or_without_recording_gets_no_note(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    res, after, _, _ = run_one(tmp_path / "a", vcon(status="sched", with_recording=True, session_start=True))
    assert note_of(after) is None and res["changes"] == []
    res, after, _, _ = run_one(tmp_path / "b", vcon(status="sched", with_recording=False, session_start=False))
    assert note_of(after) is None and res["changes"] == []


def test_group_c(tmp_path):
    rel = "ietf90/ietf90_mpls_23162.vcon.json"
    ctx = make_ctx(group_c={rel})
    res, after, _, _ = run_one(tmp_path, vcon(status="sched", with_recording=True), ctx=ctx, rel=rel)
    assert note_of(after) == pp.NOTE_C
    assert [c["group"] for c in res["changes"]] == ["C"]


def test_group_a_and_c_combined(tmp_path):
    rel = "ietf90/ietf90_pcp_23229.vcon.json"
    ctx = make_ctx(group_c={rel})
    res, after, _, _ = run_one(tmp_path, vcon(status="canceled", with_recording=True, session_start=False),
                               ctx=ctx, rel=rel)
    assert note_of(after) == pp.NOTE_A + " " + pp.NOTE_C
    assert res["changes"][0]["group"] == "A+C"


def test_note_is_appended_after_existing_members_and_changes_nothing_else(tmp_path):
    obj = vcon(status="canceled", with_recording=True)
    _, after, _, _ = run_one(tmp_path, obj)
    md_before = json.loads(obj["attachments"][0]["body"])
    md_after = json.loads(after["attachments"][0]["body"])
    assert list(md_after) == list(md_before) + ["recording_note"]
    expected = dict(obj, updated_at=NOW)
    expected["attachments"] = [dict(a) for a in obj["attachments"]]
    expected["attachments"][0]["body"] = json.dumps(md_after, ensure_ascii=False)
    assert after == expected


def test_notes_are_fixed_sentences_without_names():
    assert "ietf" not in (pp.NOTE_A + pp.NOTE_B + pp.NOTE_C).lower()
    assert pp.NOTE_A.startswith("This session did not meet. The recording attached")
    assert pp.NOTE_B.startswith("This session has no time in the official schedule.")
    assert pp.NOTE_C.startswith("The recording's file name indicates")


# ---------- guard, idempotence, rewriting, formatting ----------

def bad_transform_factory(mutate):
    def transform(relpath, obj, ctx, loader):
        new, changes = pp.compute_changes(relpath, obj, ctx, loader)
        mutate(new)
        return new, changes
    return transform


@pytest.mark.parametrize("mutate", [
    lambda n: n.__setitem__("uuid", "other"),
    lambda n: n.__setitem__("created_at", "2000-01-01T00:00:00+00:00"),
    lambda n: n["parties"].append({"name": "x"}),
    lambda n: n["sessions"][0].__setitem__("start", "2000-01-01T00:00:00+00:00"),
    lambda n: n["dialog"][0].__setitem__("url", "https://evil"),
    lambda n: n["analysis"].append({}),
    lambda n: n.__setitem__("extra", 1),
    lambda n: n["attachments"][3].__setitem__("mediatype", "text/plain"),
    lambda n: n["attachments"][3].__setitem__("purpose", "agenda"),
    lambda n: n["attachments"][1].__setitem__("body", json.dumps({"source": "changed"})),
    lambda n: n["attachments"][4].__setitem__(
        "body", json.dumps({"url": "https://changed", "filename": None, "title": "T"})),
    lambda n: n["attachments"][4].__setitem__(
        "body", json.dumps({"url": "https://dt/y", "mimetype": "text/html", "filename": None, "title": "other"})),
    lambda n: n["attachments"][0].__setitem__(
        "body", json.dumps({"ietf_meeting_number": 105, "recording_note": "x"})),
    lambda n: n["attachments"].pop(),
])
def test_guard_trips_and_file_is_skipped(tmp_path, mutate):
    obj = vcon([hashed(), ref("https://dt/y")], status="canceled", with_recording=True)
    root = write_tree(tmp_path, {"ietf104/ietf104_core_1.vcon.json": obj})
    path = os.path.join(root, "ietf104/ietf104_core_1.vcon.json")
    before = open(path, "rb").read()
    res = pp.process_file(root, "ietf104/ietf104_core_1.vcon.json", make_ctx(), apply=True,
                          transform=bad_transform_factory(mutate))
    assert res["errors"] and not res["wrote"]
    assert open(path, "rb").read() == before
    assert [f for f in os.listdir(os.path.dirname(path)) if f.startswith(".")] == []


def test_guard_allows_exactly_the_intended_members():
    orig = vcon([ref("https://dt/y")], status="canceled", with_recording=True)
    new, _ = pp.compute_changes("ietf104/ietf104_core_1.vcon.json", orig, make_ctx())
    assert pp.guard(orig, json.loads(json.dumps(new))) == []


def test_idempotent(tmp_path):
    obj = vcon([ref("https://dt/y", filename="y.pptx"), ref("https://dt/z")], status="canceled",
               with_recording=True)
    res, after, root, ctx = run_one(tmp_path, obj)
    assert res["wrote"]
    path = os.path.join(root, "ietf104/ietf104_core_1.vcon.json")
    first = open(path, "rb").read()
    ctx2 = pp.Context(group_c=(), now=lambda: "2031-01-01T00:00:00+00:00")
    res2 = pp.process_file(root, "ietf104/ietf104_core_1.vcon.json", ctx2, apply=True,
                           base_map_loader=StubPrevious().body_ref_mimetypes)
    assert not res2["wrote"] and not any(c["changed"] for c in res2["changes"])
    assert open(path, "rb").read() == first


def test_no_rewrite_when_nothing_changes(tmp_path):
    obj = vcon([ref("https://dt/y", mimetype="text/html"), hashed()])
    root = write_tree(tmp_path, {"ietf104/ietf104_core_1.vcon.json": obj})
    path = os.path.join(root, "ietf104/ietf104_core_1.vcon.json")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    st = os.stat(path)
    res = pp.process_file(root, "ietf104/ietf104_core_1.vcon.json", make_ctx(), apply=True)
    assert not res["wrote"]
    st2 = os.stat(path)
    assert (st2.st_mtime_ns, st2.st_ino) == (st.st_mtime_ns, st.st_ino)
    assert json.load(open(path))["updated_at"] == OLD_UPDATED


def test_plan_mode_never_writes(tmp_path):
    obj = vcon([ref("https://dt/y")])
    res, after, root, _ = run_one(tmp_path, obj, apply=False)
    assert res["changes"][0]["changed"] and not res["wrote"]
    assert after == obj


def test_formatting_preserved(tmp_path):
    obj = vcon([ref("https://dt/y", title="Zoë - naïve"), ref("https://dt/z", filename="z.pdf")],
               status="canceled", with_recording=True)
    res, _, root, _ = run_one(tmp_path, obj)
    text = open(os.path.join(root, "ietf104/ietf104_core_1.vcon.json"), encoding="utf-8").read()
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert "Zoë - naïve" in text and "\\u" not in text
    for a in json.loads(text)["attachments"]:
        if "body" in a:
            assert a["body"] == json.dumps(json.loads(a["body"]), ensure_ascii=False)


def test_unchanged_file_roundtrips_byte_identical(tmp_path):
    obj = vcon([ref("https://dt/y", title="Zoë")])
    text = pp.dumps_file(obj)
    assert pp.dumps_file(json.loads(text)) == text


def test_atomic_write_failure_leaves_original(tmp_path, monkeypatch):
    obj = vcon([ref("https://dt/y")])
    root = write_tree(tmp_path, {"ietf104/ietf104_core_1.vcon.json": obj})
    path = os.path.join(root, "ietf104/ietf104_core_1.vcon.json")
    before = open(path, "rb").read()

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(pp.os, "replace", boom)
    res = pp.process_file(root, "ietf104/ietf104_core_1.vcon.json", make_ctx(), apply=True)
    assert res["errors"] and not res["wrote"]
    assert open(path, "rb").read() == before
    assert os.listdir(os.path.dirname(path)) == ["ietf104_core_1.vcon.json"]


def test_write_refused_if_file_changed_since_read(tmp_path):
    obj = vcon([ref("https://dt/y")])
    root = write_tree(tmp_path, {"ietf104/ietf104_core_1.vcon.json": obj})
    path = os.path.join(root, "ietf104/ietf104_core_1.vcon.json")
    raw = open(path, "rb").read()
    open(path, "ab").write(b" ")
    with pytest.raises(RuntimeError):
        pp.atomic_write(path, "{}\n", raw)
    assert os.listdir(os.path.dirname(path)) == ["ietf104_core_1.vcon.json"]


# ---------- production safety ----------

def make_work_tree(tmp_path):
    root = tmp_path / "dataset"
    (root / ".git").mkdir(parents=True)
    (root / "ietf100").mkdir()
    return str(root)


def test_apply_in_a_git_working_tree_refused_without_flag(tmp_path):
    root = make_work_tree(tmp_path)
    with pytest.raises(SystemExit) as e:
        pp.check_production(root, allow=False, dataset_root=root)
    assert "--allow-production" in str(e.value)
    with pytest.raises(SystemExit):
        pp.check_production(os.path.join(root, "ietf100"), allow=False, dataset_root=root)
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit):
        pp.check_production(str(other), allow=False, dataset_root=root)


def test_the_dataset_root_itself_is_production_even_without_a_git_entry(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(SystemExit):
        pp.check_production(str(tmp_path / "plain"), allow=False, dataset_root=str(tmp_path / "plain"))


def test_scratch_dir_is_allowed(tmp_path):
    (tmp_path / "scratch").mkdir()
    pp.check_production(str(tmp_path / "scratch"), allow=False, dataset_root=make_work_tree(tmp_path))


def test_production_refused_when_converter_running_or_dirty(monkeypatch, tmp_path):
    root = make_work_tree(tmp_path)
    monkeypatch.setattr(pp, "git_status_porcelain", lambda root: "")
    monkeypatch.setattr(pp, "running_blockers", lambda: ["123 python chair_determination.py"])
    with pytest.raises(SystemExit) as e:
        pp.check_production(root, allow=True, dataset_root=root)
    assert "chair_determination.py" in str(e.value)
    monkeypatch.setattr(pp, "running_blockers", lambda: [])
    monkeypatch.setattr(pp, "git_status_porcelain", lambda root: " M ietf100/x.vcon.json\n")
    with pytest.raises(SystemExit) as e:
        pp.check_production(root, allow=True, dataset_root=root)
    assert "uncommitted" in str(e.value)
    monkeypatch.setattr(pp, "git_status_porcelain", lambda root: "")
    pp.check_production(root, allow=True, dataset_root=root)


def test_a_live_lock_file_process_blocks_but_a_dead_or_absent_one_does_not(monkeypatch, tmp_path):
    root = make_work_tree(tmp_path)
    monkeypatch.setattr(pp, "git_status_porcelain", lambda root: "")
    monkeypatch.setattr(pp, "running_blockers", lambda: [])
    lock = tmp_path / "lock"
    lock.write_text(str(os.getpid()) + "\n")
    with pytest.raises(SystemExit) as e:
        pp.check_production(root, allow=True, dataset_root=root, lock_file=str(lock))
    assert "named by the lock is running" in str(e.value)
    lock.write_text("not a pid\n")
    with pytest.raises(SystemExit) as e:
        pp.check_production(root, allow=True, dataset_root=root, lock_file=str(lock))
    assert "without a readable pid" in str(e.value)
    lock.write_text("4194303\n")  # beyond the usual pid range
    pp.check_production(root, allow=True, dataset_root=root, lock_file=str(lock))
    pp.check_production(root, allow=True, dataset_root=root, lock_file=str(tmp_path / "absent"))
    pp.check_production(root, allow=True, dataset_root=root)


def test_a_lock_directory_with_a_pid_file_is_read(tmp_path):
    (tmp_path / "lockdir").mkdir()
    (tmp_path / "lockdir" / "pid").write_text(str(os.getpid()))
    assert "is running" in pp.lock_problem(str(tmp_path / "lockdir"))
    assert pp.lock_problem(None) is None


def test_running_blockers_detects_processes(monkeypatch):
    class R:
        stdout = "  11 /usr/bin/python chair_determination.py --x\n  12 ietf2vcon convert 100\n  13 vim notes\n"
    monkeypatch.setattr(pp.subprocess, "run", lambda *a, **k: R())
    assert len(pp.running_blockers()) == 2


# ---------- command line ----------

def test_main_apply_requires_output_root():
    with pytest.raises(SystemExit):
        pp.main(["--apply", "--previous-rev", "HEAD"])


def test_main_requires_the_previous_revision():
    with pytest.raises(SystemExit):
        pp.main([])


def test_default_report_and_cache_locations_are_outside_the_repository(monkeypatch, tmp_path):
    repo = os.path.realpath(pp.REPO_ROOT)
    location = os.path.realpath(pp.user_cache_dir())
    assert not location.startswith(repo + os.sep) and location != repo
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert pp.user_cache_dir() == str(tmp_path / "xdg" / "ietf-meeting-vcons" / "reference-types")


def test_group_c_file_replaces_the_builtin_list(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# comment\nietf1/ietf1_a_1.vcon.json\n\nietf2/ietf2_b_2.vcon.json\n")
    assert pp.load_group_c(str(f)) == {"ietf1/ietf1_a_1.vcon.json", "ietf2/ietf2_b_2.vcon.json"}
    assert "ietf90/ietf90_pcp_23229.vcon.json" in pp.GROUP_C


# ---------- the previous release (read-only git) and the command line end to end ----------

def make_previous_repo(tmp_path, files):
    """A throwaway repository with one commit holding the given files (the previous release)."""
    repo = tmp_path / "previous-repo"
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    for rel, obj in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pp.dumps_file(obj), encoding="utf-8")
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "previous"]):
        subprocess.run(["git", "-C", str(repo), *command], check=True, env=env, capture_output=True)
    return str(repo)


def test_git_previous_reads_previous_release(tmp_path):
    url = "https://dt/meeting/104/materials/agenda-104-core"
    repo = make_previous_repo(tmp_path, {"ietf104/ietf104_core_1.vcon.json":
                                         vcon([ref(url, mimetype="text/html", purpose="agenda")])})
    b = pp.PreviousRelease(repo, "HEAD")
    try:
        assert b.body_ref_mimetypes("ietf104/ietf104_core_1.vcon.json")[url] == {"text/html"}
        assert b.body_ref_mimetypes("ietf104/does_not_exist.vcon.json") is None
    finally:
        b.close()
    with pytest.raises(SystemExit) as e:
        pp.PreviousRelease(repo, "no-such-revision")
    assert "no-such-revision" in str(e.value)


def test_main_plans_then_applies_to_a_scratch_copy_that_is_not_a_git_repository(tmp_path, capsys):
    url = "https://dt/meeting/104/materials/agenda-104-core"
    rel = "ietf104/ietf104_core_1.vcon.json"
    repo = make_previous_repo(tmp_path, {rel: vcon([ref(url, mimetype="text/html", purpose="agenda")])})
    scratch = write_tree(tmp_path, {rel: vcon([ref(url, purpose="agenda"),
                                               ref("https://dt/x", filename="x.pptx")])})
    reports = str(tmp_path / "reports")
    common = ["--git-dir", repo, "--previous-rev", "HEAD", "--report-dir", reports]
    assert pp.main(["--dataset-root", scratch, *common]) == 0
    before = open(os.path.join(scratch, rel), "rb").read()
    summary = json.loads(open(os.path.join(reports, "plan-summary.json")).read())
    assert summary["mimetype_changed_attachments"] == 2 and summary["previous_pdf"] == "ignore"
    assert open(os.path.join(scratch, rel), "rb").read() == before  # plan wrote nothing
    assert pp.main(["--apply", "--output-root", scratch, *common]) == 0
    after = json.load(open(os.path.join(scratch, rel)))
    assert [b["mimetype"] for b in ref_bodies(after)] == [
        "text/html", "application/vnd.openxmlformats-officedocument.presentationml.presentation"]
    changed = open(os.path.join(scratch, rel), "rb").read()
    assert pp.main(["--apply", "--output-root", scratch, *common]) == 0  # idempotent
    assert open(os.path.join(scratch, rel), "rb").read() == changed
