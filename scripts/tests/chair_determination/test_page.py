import chair_determination as rc

PAGE = """<html><body><table><tr><td>
      <h3>Chair(s):</h3>
      <ul>

        <li><a href="mailto:olaf.broan@example.org">Olaf Br&oslash;an</a></li>

        <li><a href="mailto:b%C3%B6rje@example.org?subject=x">B&ouml;rje  J&auml;rvinen</a></li>
        <li><a href="mailto:">Pat Nobody</a></li>

      </ul>
      <h3>IRTF Area Director(s):</h3>
      <ul><li><a href="mailto:ad@example.org">An AD</a></li></ul>
</td></tr></table></body></html>"""


def test_parse_with_entities_accents_and_odd_mailto():
    entries = rc.parse_chair_page(PAGE)
    assert entries == [
        ("Olaf Brøan", "olaf.broan@example.org"),
        ("Börje Järvinen", "börje@example.org"),
        ("Pat Nobody", None),
    ]


def test_literal_accents():
    page = '<h3>Chair(s):</h3><ul><li><a href="mailto:t@example.org">Þór Ásgeirsson</a></li></ul>'
    assert rc.parse_chair_page(page) == [("Þór Ásgeirsson", "t@example.org")]


def test_page_without_a_chair_list_is_none():
    assert rc.parse_chair_page("<html><h3>Agenda:</h3><ul><li>x</li></ul></html>") is None


def test_page_with_an_empty_chair_list_is_empty():
    assert rc.parse_chair_page("<h3>Chair(s):</h3>\n<ul>\n</ul><h3>Other</h3>") == []


def test_read_chair_page_statuses(tmp_path):
    (tmp_path / "84").mkdir()
    (tmp_path / "84" / "ok.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "84" / "bare.html").write_text("<html>nothing</html>")
    (tmp_path / "84" / "latin.html").write_bytes(
        '<h3>Chair(s):</h3><ul><li><a href="mailto:a@example.org">Zo\u00eb Quux</a></li></ul>'.encode("latin-1"))
    assert rc.read_chair_page(tmp_path, 84, "ok").status == "ok"
    assert rc.read_chair_page(tmp_path, 84, "bare").status == "unparsed"
    assert rc.read_chair_page(tmp_path, 84, "absent").status == "missing"
    assert rc.read_chair_page(tmp_path, 84, "latin").entries == [("Zoë Quux", "a@example.org")]


OLD_PAGE = """<html><title>Charter</title><body>
<i>NOTE: This charter is a snapshot of the 66th IETF Meeting.</i>
<h2>Chair(s):</h2>
<a href="mailto:ann.one@example.org"><b>Ann One &lt;ann.one@example.org&gt;</b></a><br>
<a href="mailto:bo.two@example.org"><b>Bo T&ouml;&ouml; &lt;bo.two@example.org&gt;</b></a><br>

<h2>Internet Area Director(s):</h2>
<a href="mailto:ad@example.org"><b>An AD &lt;ad@example.org&gt;</b></a><br>
</body></html>"""


def test_older_pages_with_a_second_level_heading_and_angle_addresses():
    assert rc.parse_chair_page(OLD_PAGE) == [("Ann One", "ann.one@example.org"), ("Bo Töö", "bo.two@example.org")]


def test_older_page_is_read_from_any_meeting(tmp_path):
    (tmp_path / "66").mkdir()
    (tmp_path / "66" / "grp.html").write_text(OLD_PAGE)
    page = rc.read_chair_page(tmp_path, 66, "grp")
    assert page.status == "ok" and len(page.entries) == 2
