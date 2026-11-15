import chair_determination as rc
from helpers import T, make_data

CAL = rc.Calibration([(50, T("2012-01-01T00:00:00")), (150, T("2012-06-01T00:00:00"))])
GROUPS = {5: ("grp", "wg", "2013-06-01T00:00:00")}
SNAPS = {5: [(100, "2011-01-01T00:00:00"), (200, "2011-01-01T00:00:00")]}
NAMES = {1: "Olaf Brøan", 2: "Pat Nobody", 3: "Kim Other"}
T0 = T("2012-03-01T00:00:00")


def page(*entries, status="ok"):
    return rc.ChairPage(status, "/x/84/grp.html", list(entries))


def doc(*name_lists, readable=True, name="minutes-84-grp.txt"):
    return rc.MinutesDoc(name, "minutes", readable, [rc.ChairsLine("x", n) for n in name_lists])


def answer(rolehistory, snapshots=SNAPS):
    data = make_data(snapshots, rolehistory, groups=GROUPS)
    return rc.write_order_answer(data, CAL, 5, T0)


def test_recovered_when_all_three_name_the_same_people():
    wo = answer({100: [1, 2], 200: [1, 2]})
    d = rc.decide(wo, page(("Pat Nobody", "pat@example.org"), ("Olaf Broan", "olaf@example.org")),
                  [doc(["Broan", "Pat Nobody"])], NAMES)
    assert d.decision == "recover"
    assert d.chairs == [{"name": "Olaf Brøan", "mailto": "olaf@example.org"},
                        {"name": "Pat Nobody", "mailto": "pat@example.org"}]


def test_address_falls_back_to_the_role_email_when_the_page_has_none():
    wo = answer({100: [1], 200: [1]})
    d = rc.decide(wo, page(("Olaf Broan", None)), [doc(["Olaf Broan"])], NAMES)
    assert d.decision == "recover" and d.chairs[0]["mailto"] == "chair1@example.org"


def test_the_two_sources_alone_are_no_longer_enough():
    wo = answer({100: [1], 200: [1]})
    assert rc.decide(wo, page(("Olaf Broan", None)), [], NAMES).reason == "no minutes document"
    assert rc.decide(wo, page(("Olaf Broan", None)), [doc(readable=False)], NAMES).reason == "no minutes document"
    assert rc.decide(wo, page(("Olaf Broan", None)), [doc()], NAMES).reason == "no explicit chairs line"


def test_minutes_naming_a_subset_or_someone_else_do_not_confirm():
    wo = answer({100: [1, 2], 200: [1, 2]})
    p = page(("Olaf Broan", None), ("Pat Nobody", None))
    assert rc.decide(wo, p, [doc(["Olaf Broan"])], NAMES).reason == "minutes name a subset"
    assert rc.decide(wo, p, [doc(["Olaf Broan", "Kim Other"])], NAMES).reason == "minutes do not match the other sources"
    assert rc.decide(wo, p, [doc(["Kim Other"])], NAMES).reason == "minutes do not match the other sources"


def test_write_order_uncertain():
    wo = answer({100: [1], 200: [2]})
    assert rc.decide(wo, page(("Olaf Broan", None)), [doc(["Olaf Broan"])], NAMES).reason == "write order uncertain"


def test_no_proceedings_page_missing_or_without_list():
    wo = answer({100: [1], 200: [1]})
    for status in ("missing", "unparsed"):
        assert rc.decide(wo, page(status=status), [doc(["Olaf Broan"])], NAMES).reason == "no proceedings page"


def test_sources_disagree_on_who_or_how_many():
    wo = answer({100: [1], 200: [1]})
    docs = [doc(["Olaf Broan"])]
    assert rc.decide(wo, page(("Kim Other", None)), docs, NAMES).reason == "sources disagree"
    assert rc.decide(wo, page(("Olaf Broan", None), ("Kim Other", None)), docs, NAMES).reason == "sources disagree"
    assert rc.decide(wo, page(), docs, NAMES).reason == "sources disagree"
    empty = answer({100: [], 200: []})
    assert rc.decide(empty, page(("Kim Other", None)), docs, NAMES).reason == "sources disagree"


def test_no_chairs_in_either_source():
    empty = answer({100: [], 200: []})
    assert rc.decide(empty, page(), [doc()], NAMES).reason == "no chairs in either source"


def test_lookup_failed_without_an_answer_or_a_name():
    assert rc.decide(None, page(("Olaf Broan", None)), [doc(["Olaf Broan"])], NAMES).reason == "lookup failed"
    wo = answer({100: [1], 200: [1]})
    assert rc.decide(wo, page(("Olaf Broan", None)), [doc(["Olaf Broan"])], {}).reason == "lookup failed"
    assert rc.decide(wo, page(("Olaf Broan", None)), [doc(["Olaf Broan"])], None).reason == "lookup failed"
