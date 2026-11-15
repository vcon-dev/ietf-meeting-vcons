import chair_determination as rc
from helpers import T, make_data

CAL = rc.Calibration([(50, T("2012-01-01T00:00:00")), (150, T("2012-06-01T00:00:00")), (250, T("2012-12-01T00:00:00"))])
GROUPS = {5: ("grp", "wg", "2013-06-01T00:00:00")}
SNAPS = {5: [(100, "2011-01-01T00:00:00"), (120, "2011-01-01T00:00:00"), (200, "2011-01-01T00:00:00"), (300, "2011-01-01T00:00:00")]}


def test_intervals_used():
    assert CAL.bracket(100) == (T("2012-01-01T00:00:00"), T("2012-06-01T00:00:00"))
    assert CAL.bracket(200) == (T("2012-06-01T00:00:00"), T("2012-12-01T00:00:00"))


def test_confident_when_every_candidate_is_the_same_people():
    data = make_data(SNAPS, {100: [1, 2], 120: [2, 1], 200: [1, 2], 300: [7]}, groups=GROUPS)
    wo = rc.write_order_answer(data, CAL, 5, T("2012-03-01T00:00:00"))
    assert wo.status == "confident"
    assert wo.after == [200, 300] and wo.uncertain == [100, 120]
    assert wo.source == 200 and wo.persons == frozenset({1, 2})


def test_uncertain_when_a_snapshot_in_the_interval_differs():
    data = make_data(SNAPS, {100: [1, 2], 120: [1, 3], 200: [1, 2], 300: [7]}, groups=GROUPS)
    wo = rc.write_order_answer(data, CAL, 5, T("2012-03-01T00:00:00"))
    assert wo.status == "uncertain"


def test_uncertain_when_first_certainly_later_snapshot_differs_from_the_interval():
    data = make_data(SNAPS, {100: [1, 2], 120: [1, 2], 200: [4, 5], 300: [7]}, groups=GROUPS)
    assert rc.write_order_answer(data, CAL, 5, T("2012-03-01T00:00:00")).status == "uncertain"


def test_lowest_id_of_the_later_snapshots_is_used_not_the_first_listed():
    data = make_data(SNAPS, {100: [1], 120: [1], 200: [1], 300: [9]}, groups=GROUPS)
    wo = rc.write_order_answer(data, CAL, 5, T("2012-03-01T00:00:00"))
    assert wo.source == 200 and wo.status == "confident"


def test_live_roles_when_no_snapshot_is_certainly_later():
    data = make_data(SNAPS, {100: [1], 120: [1], 200: [1], 300: [1, 2]}, live={5: [1, 2]}, groups=GROUPS)
    wo = rc.write_order_answer(data, CAL, 5, T("2013-01-01T00:00:00"))
    # only 300 (written 2012-12 on) can hold t; snapshots 100-200 certainly predate it
    assert wo.after == [] and wo.uncertain == [300]
    assert wo.source == "live" and wo.status == "confident" and wo.persons == frozenset({1, 2})


def test_live_fallback_is_uncertain_if_it_differs_from_an_interval_snapshot():
    data = make_data(SNAPS, {100: [1], 120: [1], 200: [1], 300: [1]}, live={5: [1, 2]}, groups=GROUPS)
    assert rc.write_order_answer(data, CAL, 5, T("2013-01-01T00:00:00")).status == "uncertain"


def test_chairs_in_order_keeps_lowest_role_per_person_ascending():
    rows = [rc.RoleRow(30, 2), rc.RoleRow(10, 1), rc.RoleRow(20, 2), rc.RoleRow(5, 3)]
    assert [(r.id, r.person) for r in rc.chairs_in_order(rows)] == [(5, 3), (10, 1), (20, 2)]


def test_email_of_uses_the_uri_segment():
    assert rc.email_of(rc.RoleRow(1, 1, "/api/v1/person/email/a.b%40example.org/")) == "a.b@example.org"
    assert rc.email_of(rc.RoleRow(1, 1, None)) is None
