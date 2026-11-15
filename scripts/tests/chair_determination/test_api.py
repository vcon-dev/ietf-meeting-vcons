import json

import pytest

import chair_determination as rc


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_api(tmp_path, responses, **kwargs):
    clock = Clock()
    calls = []

    def opener(url, headers, timeout):
        calls.append(url)
        clock.now += 0.2
        status, body, hdrs = responses(url) if callable(responses) else responses
        return status, json.dumps(body).encode(), hdrs

    api = rc.ApiCache(tmp_path / "cache", opener=opener, clock=clock.time, sleep=clock.sleep, **kwargs)
    return api, calls, clock


def test_response_is_cached_on_disk_one_file_per_url(tmp_path):
    api, calls, _ = make_api(tmp_path, (200, {"objects": [1]}, {}))
    assert api.get("/api/v1/x/", {"a": 1}) == {"objects": [1]}
    assert api.get("/api/v1/x/", {"a": 1}) == {"objects": [1]}
    assert len(calls) == 1 and api.stats["requests"] == 1 and api.stats["from_cache"] == 1
    files = sorted(p.name for p in (tmp_path / "cache").glob("*.json*") )
    assert len(files) == 2 and files[1].endswith(".json.url")
    # a new process reads the same cache without a request
    api2, calls2, _ = make_api(tmp_path, (500, {}, {}))
    assert api2.get("/api/v1/x/", {"a": 1}) == {"objects": [1]} and calls2 == []


def test_requests_are_at_least_a_second_apart(tmp_path):
    api, calls, clock = make_api(tmp_path, (200, {"objects": []}, {}))
    stamps = []
    orig = api.opener

    def spy(url, headers, timeout):
        stamps.append(clock.now)
        return orig(url, headers, timeout)

    api.opener = spy
    for i in range(4):
        api.get("/api/v1/x/", {"i": i})
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(stamps, stamps[1:]))
    assert len(calls) == 4


def test_spacing_also_holds_across_runs(tmp_path):
    api, _, clock = make_api(tmp_path, (200, {"objects": []}, {}))
    api.get("/api/v1/x/", {"i": 0})
    api2, calls2, _ = make_api(tmp_path, (200, {"objects": []}, {}))
    api2.clock = clock.time
    api2.sleep = clock.sleep
    before = clock.now
    api2.get("/api/v1/x/", {"i": 1})
    assert clock.now - before >= 0.8  # waited out the rest of the second


def test_429_stops_live_requests_for_ten_minutes_but_not_cached_reads(tmp_path):
    state = {"n": 0}

    def responses(url):
        state["n"] += 1
        return (200, {"objects": ["cached"]}, {}) if state["n"] == 1 else (429, {}, {})

    api, calls, clock = make_api(tmp_path, responses)
    api.get("/api/v1/x/", {"i": 0})
    with pytest.raises(rc.RateLimited):
        api.get("/api/v1/x/", {"i": 1})
    n = len(calls)
    clock.now += 599
    with pytest.raises(rc.RateLimited):
        api.get("/api/v1/x/", {"i": 2})
    assert len(calls) == n  # no request at all during the stop
    api2, calls2, _ = make_api(tmp_path, responses)  # the stop holds across runs
    api2.clock = clock.time
    with pytest.raises(rc.RateLimited):
        api2.get("/api/v1/x/", {"i": 3})
    assert calls2 == []
    assert api.get("/api/v1/x/", {"i": 0})["objects"] == ["cached"]  # cached reads still work
    assert api.stats["http_429"] == 1


def test_429_with_a_longer_retry_after_is_respected(tmp_path):
    api, calls, clock = make_api(tmp_path, (429, {}, {"Retry-After": "1200"}))
    with pytest.raises(rc.RateLimited):
        api.get("/api/v1/x/", {})
    clock.now += 700
    with pytest.raises(rc.RateLimited):
        api.get("/api/v1/x/", {"again": 1})
    assert len(calls) == 1


def test_request_cap(tmp_path):
    api, calls, _ = make_api(tmp_path, (200, {"objects": []}, {}), max_requests=2)
    api.get("/api/v1/x/", {"i": 0})
    api.get("/api/v1/x/", {"i": 1})
    with pytest.raises(rc.BudgetExceeded):
        api.get("/api/v1/x/", {"i": 2})
    assert len(calls) == 2
    api.get("/api/v1/x/", {"i": 1})  # cache hits do not count


def test_errors_are_not_cached(tmp_path):
    api, calls, _ = make_api(tmp_path, (500, {}, {}))
    with pytest.raises(rc.DatatrackerError):
        api.get("/api/v1/x/", {})
    assert list((tmp_path / "cache").glob("*.json")) == []


def test_seed_cache_is_reused_and_copied_with_param_order_ignored(tmp_path):
    import hashlib
    seed = tmp_path / "seed"
    seed.mkdir()
    url = "https://datatracker.ietf.org/api/v1/x/?b=2&a=1&format=json"
    name = hashlib.md5(url.encode()).hexdigest()
    (seed / f"{name}.json").write_text(json.dumps({"objects": ["seeded"]}))
    (seed / f"{name}.json.url").write_text(url)
    api, calls, _ = make_api(tmp_path, (500, {}, {}), seed_dirs=[seed])
    assert api.get("/api/v1/x/", {"a": 1, "b": 2}) == {"objects": ["seeded"]}
    assert calls == [] and api.stats["from_seed"] == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1  # copied in
    assert len(list(seed.glob("*"))) == 2  # the seed itself is untouched


def test_get_all_pages_and_checks_the_total(tmp_path):
    def responses(url):
        offset = int(url.split("offset=")[1].split("&")[0])
        objects = [{"id": i} for i in range(offset, min(offset + 2, 5))]
        meta = {"total_count": 5, "next": "more" if offset + 2 < 5 else None}
        return 200, {"meta": meta, "objects": objects}, {}

    api, calls, _ = make_api(tmp_path, responses)
    assert [o["id"] for o in api.get_all("/api/v1/x/", {"name__slug": "chair"}, limit=2)] == [0, 1, 2, 3, 4]
    assert len(calls) == 3 and "name__slug=chair&limit=2&offset=0&order_by=id&format=json" in calls[0]

    def short(url):
        return 200, {"meta": {"total_count": 9, "next": None}, "objects": [{"id": 1}]}, {}

    api2, _, _ = make_api(tmp_path / "b", short)
    with pytest.raises(rc.DatatrackerError, match="reports 9"):
        api2.get_all("/api/v1/y/")
