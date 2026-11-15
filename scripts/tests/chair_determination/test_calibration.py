import chair_determination as rc
from helpers import T


def snaps(items, gid):
    return [rc.Snapshot(i, gid, T(t)) for i, t in items]


def test_points_from_next_snapshot_and_group_time():
    groups = {1: rc.Group(1, "aaa", "wg", T("2012-09-01T00:00:00")),
              2: rc.Group(2, "bbb", "wg", T("2012-04-01T00:00:00"))}
    snapshots = {
        1: snaps([(10, "2012-01-01T00:00:00"), (20, "2012-01-01T00:00:00"), (30, "2012-06-01T00:00:00")], 1),
        2: snaps([(15, "2011-05-01T00:00:00"), (25, "2012-03-01T00:00:00"), (35, "2012-04-01T00:00:00")], 2),
    }
    points = rc.calibration_points(snapshots, groups)
    # 10 -> next (20) has the same time: no point. 20 was written when 30's state began.
    # 30 is last and the group time is later. For group 2 the last snapshot ties the group time.
    assert points == [
        (15, T("2012-03-01T00:00:00")),
        (20, T("2012-06-01T00:00:00")),
        (25, T("2012-04-01T00:00:00")),
        (30, T("2012-09-01T00:00:00")),
    ]


def test_out_of_order_point_is_dropped_and_ends_are_kept():
    points = [
        (15, T("2012-03-01T00:00:00")),
        (20, T("2012-06-01T00:00:00")),  # above both neighbours: dropped
        (25, T("2012-04-01T00:00:00")),
        (30, T("2012-09-01T00:00:00")),
    ]
    cal = rc.Calibration(points)
    # 20 is above both neighbours; 25 is then below both of its (original) neighbours.
    assert [p[0] for p in cal.points] == [15, 30]
    assert [p[0] for p in cal.dropped] == [20, 25]
    assert cal.raw_count == 4


def test_single_out_of_order_point_among_many_is_dropped():
    points = [(10, T("2012-01-01T00:00:00")), (20, T("2012-02-01T00:00:00")), (30, T("2012-12-01T00:00:00")),
              (40, T("2012-03-01T00:00:00")), (50, T("2012-04-01T00:00:00")), (60, T("2012-05-01T00:00:00"))]
    cal = rc.Calibration(points)
    assert 30 not in cal.ids and 10 in cal.ids and 60 in cal.ids


def test_dip_is_dropped_too_but_a_plain_step_is_not():
    cal = rc.Calibration([(1, T("2012-05-01T00:00:00")), (2, T("2012-01-01T00:00:00")), (3, T("2012-06-01T00:00:00"))])
    assert [p[0] for p in cal.points] == [1, 3]
    cal = rc.Calibration([(1, T("2012-01-01T00:00:00")), (2, T("2012-02-01T00:00:00")), (3, T("2012-03-01T00:00:00"))])
    assert len(cal.points) == 3 and not cal.dropped


def test_bracket_between_calibrated_points():
    cal = rc.Calibration([
        (15, T("2012-03-01T00:00:00")), (25, T("2012-04-01T00:00:00")), (30, T("2012-09-01T00:00:00")),
    ])
    assert cal.bracket(25) == (T("2012-04-01T00:00:00"),) * 2  # calibrated id: a point
    assert cal.bracket(20) == (T("2012-03-01T00:00:00"), T("2012-04-01T00:00:00"))  # dropped or unknown id
    assert cal.bracket(10) == (rc.MIN_T, T("2012-03-01T00:00:00"))
    assert cal.bracket(99) == (T("2012-09-01T00:00:00"), rc.MAX_T)


def test_bracket_uses_running_max_below_and_running_min_above():
    # A descending run of three survives the one-pass drop (only its last point is a dip).
    cal = rc.Calibration([
        (10, T("2012-06-01T00:00:00")), (20, T("2012-05-01T00:00:00")),
        (30, T("2012-04-01T00:00:00")), (40, T("2012-03-01T00:00:00")), (50, T("2012-12-01T00:00:00")),
    ])
    assert [p[0] for p in cal.points] == [10, 20, 30, 50]
    assert cal.bracket(25) == (T("2012-06-01T00:00:00"), T("2012-04-01T00:00:00"))
