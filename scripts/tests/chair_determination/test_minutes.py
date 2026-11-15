import pytest

import chair_determination as rc

PERSONS = {1: "Ann One", 2: "Bo Two"}


def lines(text, markup=False):
    return rc.extract_chairs_lines(text, markup)


@pytest.mark.parametrize("text,expected", [
    ("Chairs: Ann One and Bo Two", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One, Bo Two", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One & Bo Two", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One / Bo Two", ["Ann One", "Bo Two"]),
    ("Chair(s):  Ann One <ann@example.org>", ["Ann One"]),
    ("Co-Chairs: Ann One (ann@example.org), Bo Two (Example Corp)", ["Ann One", "Bo Two"]),
    ("WG Chairs:\tAnn One", ["Ann One"]),
    ("CHAIRs: Ann One.", ["Ann One"]),
    ("Chairs: Ann One, Bo Two (remote from Norway)", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One, Bo Two   Minutes: Cy Three", ["Ann One", "Bo Two"]),
    ("### Chairs: Ann One", ["Ann One"]),
    ("              - Chairs: Ann One & Bo Two  ", ["Ann One", "Bo Two"]),
    ("Chairs: Ann van der One, Bo Two", ["Ann van der One", "Bo Two"]),
])
def test_explicit_chairs_lines_are_read(text, expected):
    found = lines(text)
    assert len(found) == 1 and found[0].names == expected


def test_names_may_continue_on_following_lines():
    text = "Chairs:\n   Ann One <ann@example.org>\n   Bo Two <bo@example.org>\n\nNote-Takers:\n   Cy Three"
    assert lines(text)[0].names == ["Ann One", "Bo Two"]
    assert lines("Chairs: Ann One\n  , Bo Two\nSecretary: Cy Three")[0].names == ["Ann One", "Bo Two"]
    assert lines("Chairs:\n- Ann One <a@example.org>\n- Bo Two <b@example.org>\n")[0].names == ["Ann One", "Bo Two"]


@pytest.mark.parametrize("text", [
    "Chairs: Please take this discussion to the list.",
    "Chair: Moderate a discussion to find a way forward",
    "Chairs: 1) Hum if you want to change",
    "[ Chairs: Thanks to Ann One for taking notes ]",
    "Chairs: TBD",
    "Chairs: Ann One (acting)",
    "Chairs: Ann One (for the first half)",
])
def test_lines_that_are_not_clean_lists_of_names_do_not_count(text):
    found = lines(text)
    assert all(l.names is None for l in found)


@pytest.mark.parametrize("text,expected", [
    # absence and departure remarks leave the person a role holder
    ("Chairs: Ann One, Bo Two (in absentia)", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One (and Bo Two (not present))", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One (absent), Bo Two (unable to attend)", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One, Bo Two (remote)", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One, Bo Two (could not attend)", ["Ann One", "Bo Two"]),
    # someone acting or standing in is not a role holder; the person they stand in for is
    ("Chairs: Ann One, Cy Three (standin for Bo Two)", ["Ann One", "Bo Two"]),
    ("Working group chairs: Ann One, Cy Three (standing in for Bo Two)", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One and Cy Three standing in for Bo Two", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One, Bo Two, Cy Three (acting)", ["Ann One", "Bo Two"]),
    ("Chairs: Ann One, Cy Three (substitute)", ["Ann One"]),
])
def test_remarks_on_a_labelled_chairs_line(text, expected):
    found = lines(text)
    assert len(found) == 1 and found[0].names == expected


def test_a_line_that_says_only_who_is_acting_is_not_a_chairs_line_with_names():
    assert [l.names for l in lines("Chairs: Cy Three (acting)")] == [None]


def test_a_document_without_a_chairs_line_has_none_and_nothing_is_inferred():
    assert lines("Ann One: I think we should proceed.\nBo Two chaired the session.\nAgenda\n") == []
    assert lines("Acting chairs: Ann One") == []
    assert lines("The chairs thank everyone") == []


def test_html_is_reduced_to_text_first():
    html = "<html><body><p>Chairs: Ann One &amp; Bo Two<br>Minutes: Cy</p><p>Chairs:<br><b>Ann One</b><br>Bo&nbsp;Two</p></body></html>"
    found = lines(html, markup=True)
    assert [l.names for l in found] == [["Ann One", "Bo Two"], ["Ann One", "Bo Two"]]


def test_accents_survive():
    assert lines("Chairs: Ann Öne, Bö Two")[0].names == ["Ann Öne", "Bö Two"]


def make_tree(tmp_path, meeting=84, **files):
    for relative, content in files.items():
        kind, name = relative.split("/")
        path = tmp_path / str(meeting) / kind / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def test_documents_are_found_by_name_including_revisions(tmp_path):
    make_tree(tmp_path, **{
        "minutes/minutes-84-grp.txt": "Chairs: Ann One, Bo Two\n",
        "agenda/agenda-84-grp-00.html": "<p>Chairs: Ann One</p>",
        "agenda/agenda-84-grp-01.txt": "no chairs here",
        "minutes/minutes-84-grp-extra.txt": "Chairs: Cy Three",   # a different group
        "minutes/minutes-84-grpx.txt": "Chairs: Cy Three",
        "agenda/agenda-84-grp.pdf": b"%PDF",
    })
    rc._listing_cache.clear()
    docs = rc.load_documents(tmp_path, 84, "grp")
    assert sorted(d.name for d in docs) == ["agenda-84-grp-00.html", "agenda-84-grp-01.txt", "agenda-84-grp.pdf",
                                            "minutes-84-grp.txt"]
    by_name = {d.name: d for d in docs}
    assert by_name["minutes-84-grp.txt"].named == [["Ann One", "Bo Two"]]
    assert by_name["agenda-84-grp-00.html"].named == [["Ann One"]]
    assert by_name["agenda-84-grp-01.txt"].named == []
    assert not by_name["agenda-84-grp.pdf"].readable


def test_docx_is_readable_and_pdf_is_not(tmp_path):
    import zipfile
    path = tmp_path / "84" / "minutes" / "minutes-84-grp.docx"
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:t>Chairs: Ann One, Bo Two</w:t></w:p></w:document>")
    rc._listing_cache.clear()
    docs = rc.load_documents(tmp_path, 84, "grp")
    assert docs[0].readable and docs[0].named == [["Ann One", "Bo Two"]]


def doc(*name_lists, readable=True):
    return rc.MinutesDoc("minutes-84-grp.txt", "minutes", readable, [rc.ChairsLine("x", n) for n in name_lists])


CHAIR_NAMES = ["Ann One", "Bo Two"]


def remarks(text, names=CHAIR_NAMES, markup=False):
    return [(r.line, r.kinds) for r in rc.find_remarks(text, markup, names)]


def test_the_four_documents_of_the_review_are_read_as_decided():
    # minutes-85-cdni: prose, not a chairs line, but the absence is quoted
    cdni = "CDNI Working Group Minutes\n- Chaired by Ann One and Cy Three (since Bo could not attend)\n- notes\n"
    assert lines(cdni) == []
    assert remarks(cdni) == [("- Chaired by Ann One and Cy Three (since Bo could not attend)", ["absent"])]
    # minutes-94-saag: a labelled line with both role holders, one in absentia
    saag = "IETF94 SAAG Meeting Agenda\n\nChairs: Ann One, Bo Two (in absentia)\n\nThanks to Cy Three for notes.\n"
    assert lines(saag)[0].names == ["Ann One", "Bo Two"]
    assert remarks(saag) == [("Chairs: Ann One, Bo Two (in absentia)", ["absent"])]
    # minutes-88-netmod: a departure announced by initials, two lines after the chairs line
    netmod = "WG Chairs: Ann One\n\n  AO announces that he will step down as a co-chair. People should\n  talk to BT if they are interested.\n"
    assert lines(netmod)[0].names == ["Ann One"]
    assert remarks(netmod) == [("AO announces that he will step down as a co-chair. People should", ["departure"])]
    # minutes-91-iccrg: a labelled line the extractor did not recognise at all before
    iccrg = "ICCRG IETF 91 MEETING NOTES\nChairs: Ann One (and Bo Two (not present))\nNote-takers: Cy Three\n"
    assert lines(iccrg)[0].names == ["Ann One", "Bo Two"]
    assert remarks(iccrg) == [("Chairs: Ann One (and Bo Two (not present))", ["absent"])]


@pytest.mark.parametrize("phrase,kind", [
    ("in absentia", "absent"), ("is not present", "absent"), ("was absent", "absent"), ("could not attend", "absent"),
    ("unable to attend", "absent"), ("is remote", "remote"), ("took part remotely", "remote"),
    ("is acting chair", "acting"), ("is standing in", "acting"), ("will step down", "departure"),
    ("is stepping down", "departure"),
])
def test_phrases_near_a_chair_are_quoted(phrase, kind):
    assert remarks(f"Some text\nAnn One {phrase} today\nmore text") == [(f"Ann One {phrase} today", [kind])]


def test_a_phrase_is_found_on_the_same_line_or_the_next_two_lines_of_a_chairs_name_only():
    assert remarks("Ann One said hello\nline\nBo is absent today") == [("Bo is absent today", ["absent"])]
    assert remarks("Ann One said hello\nline\nline\nBo is absent today") == []
    assert remarks("Cy Three is absent today") == []  # not a chair of the set
    assert remarks("Ann One: absent any objection we proceed") == []
    assert remarks("These drafts were not presented. Ann One") == []
    assert remarks("Ann One: the remote participants can hear?") == []


def test_chaired_by_is_quoted_whoever_is_named_and_acting_chairs_labels_too():
    assert remarks("Chaired by Cy Three and Di Four\n", names=[]) == [("Chaired by Cy Three and Di Four", [])]
    assert remarks("Acting chairs: Cy Three, Di Four\n", names=[]) == [("Acting chairs: Cy Three, Di Four", ["acting"])]
    assert remarks("Ann One chaired the meeting and Bo Two took notes\n") == []  # general prose is not attempted


def test_remarks_are_trimmed_to_300_characters_and_keep_their_text():
    long = "Ann One could not attend " + "x" * 400
    assert remarks(long)[0][0] == long[:300]


def test_html_remarks_are_quoted_as_text():
    assert remarks("<p>Chairs: Ann One &amp; Bo Two (remote)</p>", markup=True) == [("Chairs: Ann One & Bo Two (remote)", ["remote"])]


def test_documents_carry_their_remarks_when_chair_names_are_given(tmp_path):
    make_tree(tmp_path, **{"minutes/minutes-84-grp.txt": "Chairs: Ann One, Bo Two (in absentia)\n"})
    rc._listing_cache.clear()
    assert rc.load_documents(tmp_path, 84, "grp")[0].remarks == []
    doc = rc.load_documents(tmp_path, 84, "grp", [])[0]
    assert [r.line for r in doc.remarks] == ["Chairs: Ann One, Bo Two (in absentia)"]


def test_remarks_never_change_what_a_document_confirms():
    persons = {1: "Ann One", 2: "Bo Two"}
    plain = rc.MinutesDoc("m.txt", "minutes", True, [rc.ChairsLine("x", ["Ann One", "Bo Two"])])
    marked = rc.MinutesDoc("m.txt", "minutes", True, [rc.ChairsLine("x", ["Ann One", "Bo Two"])],
                           [rc.Remark("Chairs: Ann One, Bo Two (in absentia)", ["absent"]),
                            rc.Remark("Chaired by Ann One", [])])
    assert rc.judge_documents([plain], persons) == rc.judge_documents([marked], persons) == "confirmed"


def test_judge_names():
    assert rc.judge_names(["Bo Two", "Ann One"], PERSONS) == "exact"
    assert rc.judge_names(["Ann One"], PERSONS) == "subset"
    assert rc.judge_names(["Ann One", "Cy Three"], PERSONS) == "different"
    assert rc.judge_names(["Ann One", "Bo Two", "Cy Three"], PERSONS) == "different"
    assert rc.judge_names(["Cy Three"], PERSONS) == "different"


def test_judge_names_uses_accent_folded_surnames_and_initials():
    persons = {1: "Olaf Brøan", 2: "Bo Two"}
    assert rc.judge_names(["Olaf Broan", "Bo Two"], persons) == "exact"
    assert rc.judge_names(["Brøan", "Two"], persons) == "exact"
    wangs = {1: "Ann Wang", 2: "Bo Wang"}  # the first initial separates people who share a surname
    assert rc.judge_names(["Bo Wang", "Ann Wang"], wangs) == "exact"
    assert rc.judge_names(["Cy Wang", "Bo Wang"], wangs) == "different"


def test_documents_confirm_only_if_one_names_exactly_the_set_and_none_names_anyone_else():
    assert rc.judge_documents([doc(["Ann One", "Bo Two"])], PERSONS) == "confirmed"
    assert rc.judge_documents([doc(["Ann One", "Bo Two"]), doc(["Ann One"])], PERSONS) == "confirmed"
    assert rc.judge_documents([doc(["Ann One", "Bo Two"]), doc(["Ann One", "Cy Three"])], PERSONS) == "different"
    assert rc.judge_documents([doc(["Ann One"])], PERSONS) == "subset"
    assert rc.judge_documents([doc(["Cy Three"])], PERSONS) == "different"
    assert rc.judge_documents([doc()], PERSONS) == "no explicit chairs line"
    assert rc.judge_documents([], PERSONS) == "no minutes document"
    assert rc.judge_documents([doc(readable=False)], PERSONS) == "no minutes document"
    assert rc.judge_documents([doc(readable=False), doc(["Ann One", "Bo Two"])], PERSONS) == "confirmed"
