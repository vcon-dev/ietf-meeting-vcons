#!/usr/bin/env python3
"""Chair determination for the IETF meeting vCon dataset.

What it does
------------
For every file of a completed meeting the tool appends one ``chair-determination``
analysis entry (see README-tools.md) that records the outcome, the basis, what the
Datatracker said and, where chairs were omitted or changed, the candidates that were
considered. For ordinary sessions it recomputes the converter's chair rule offline from
the Datatracker's tables (read from an on-disk cache) and requires the result to equal
the chairs in the file and the converter's own log line for that session.

For a session whose history is ambiguous (several history snapshots share one timestamp
but hold different chairs, so the converter wrote no chairs) chairs are added to
``parties`` only when three sources name exactly the same people: the write order of the
history snapshots, the group's proceedings page, and the session's own minutes or agenda.
Otherwise the session stays without chairs and the candidates stay on record.

Order of operations
-------------------
1. Convert the meetings (``ietf2vcon convert-all ... -v``), keeping each run's output in
   ``<log-dir>/convert-<meeting>.log``.
2. Mark each finished meeting by creating ``<state-dir>/<meeting>.done``.
3. Plan (the default): read everything, write only reports. Read ``plan-summary.txt``
   and ``plan-mismatches.txt`` in the report directory.
4. ``--apply``: write the records (and any confirmed chairs) under ``--output-root``.
5. ``--verify``: check that every file carries its record and that it equals the plan.
6. After any later conversion of a meeting, run steps 4 and 5 again for that meeting:
   a conversion rebuilds ``parties`` without the recovered chairs but carries the record
   across, so apply puts the chairs back without adding a second record.

Modes
-----
Plan mode only reports. ``--apply`` rewrites files under ``--output-root``; when that is
the dataset itself (or inside a git working tree) it needs ``--allow-production`` and
refuses to run while a conversion is running or a requested meeting is not complete.
``--verify`` exits non-zero on any difference. ``--dry-run`` (with ``--apply``) runs every
check and writes nothing.

Network use is gentle and normally nil: the Datatracker tables are read from the cache
(``--cache-dir``, optionally seeded from other caches with ``--seed-cache``). A live
request, only when the cache lacks a table, is made one at a time at least one second
apart, capped by ``--max-requests``, with a 10 minute hard stop on any HTTP 429.

Requires Python 3.11 or later and the standard library only. Applying with validation
runs the converter's validator as a separate program (``--converter``); nothing is
imported from the converter package.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import bisect
import copy
import csv
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

log = logging.getLogger("chair-determination")

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

BASE_URL = "https://datatracker.ietf.org"
REPO_ROOT = Path(__file__).resolve().parent.parent


def user_cache_dir() -> Path:
    """The tool's directory under the user's cache location (never inside the repository)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "ietf-meeting-vcons" / "chair-determination"


ENTRY_VENDOR = "ietf2vcon"
ENTRY_PRODUCT = "chair-determination"
ENTRY_SCHEMA = "chair-determination-v1"
# The version the ``previous-release`` candidates record, and that dataset.json must say at --previous-rev.
DEFAULT_PREVIOUS_VERSION = "2.1.0"
BASES = (
    "live-roles", "history-snapshot", "minutes-confirmed",
    "ambiguous-history", "no-history", "no-chairs-recorded",
)
NAMED_BASES = ("live-roles", "history-snapshot", "minutes-confirmed")
USER_AGENT = "ietf-meeting-vcons chair-determination (read-only, throttled)"


def user_agent(contact: str | None = None) -> str:
    return f"{USER_AGENT}; contact: {contact}" if contact else USER_AGENT

MIN_T = datetime(1, 1, 1, tzinfo=UTC)
MAX_T = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

REASONS = (
    "write order uncertain",
    "no proceedings page",
    "sources disagree",
    "no chairs in either source",
    "no minutes document",
    "no explicit chairs line",
    "minutes name a subset",
    "minutes do not match the other sources",
    "lookup failed",
)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def api_time(text) -> datetime | None:
    """An API timestamp as an aware UTC datetime (naive values are UTC)."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def uri_id(uri) -> int:
    """The numeric id at the end of a resource URI such as /api/v1/group/group/12/."""
    return int(str(uri).rstrip("/").rsplit("/", 1)[-1])


def uri_slug(uri) -> str:
    return str(uri).rstrip("/").rsplit("/", 1)[-1]


def show_time(t: datetime | None) -> str:
    if t is None or t == MIN_T:
        return "the beginning"
    if t == MAX_T:
        return "now"
    return t.isoformat()


def file_text(obj) -> str:
    """The converter's file layout: two-space indent, raw UTF-8, one trailing newline."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    """Write so the file is entirely the old content or entirely the new.

    Temporary file in the same directory (hidden, not named ``*.vcon.json``),
    flushed and fsynced, then ``os.replace``. A leftover temporary file is
    removed on failure.
    """
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


# --------------------------------------------------------------------------
# Datatracker access: one at a time, spaced, cached on disk, 429 stop
# --------------------------------------------------------------------------

class DatatrackerError(Exception):
    pass


class RateLimited(DatatrackerError):
    pass


class BudgetExceeded(DatatrackerError):
    pass


def _canonical(url: str):
    parsed = urlparse(url)
    return parsed.path, tuple(sorted(parse_qsl(parsed.query)))


def _real_opener(url: str, headers: dict, timeout: float):
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers or {})


class ApiCache:
    """GET JSON from the Datatracker through an on-disk cache.

    One file per URL: ``<md5(url)>.json`` plus ``<md5(url)>.json.url`` (the
    format a seed cache directory uses, which is read, never
    written: a seed hit is copied into this cache). Only live
    requests count against ``max_requests``. After an HTTP 429 no live request
    is made for ``stop_seconds`` (persisted in ``.stop-until``, so it also
    holds across runs); cached reads keep working.
    """

    def __init__(
        self,
        cache_dir: Path,
        seed_dirs=(),
        max_requests: int = 600,
        min_interval: float = 1.0,
        stop_seconds: float = 600.0,
        base_url: str = BASE_URL,
        opener=None,
        clock=time.time,
        sleep=time.sleep,
        user_agent_text: str | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.seed_dirs = [Path(d) for d in seed_dirs]
        self.max_requests = max_requests
        self.min_interval = min_interval
        self.stop_seconds = stop_seconds
        self.base_url = base_url
        self.user_agent_text = user_agent_text or user_agent()
        self.opener = opener or _real_opener
        self.clock = clock
        self.sleep = sleep
        self.stats = Counter()
        self._seed_index: dict | None = None

    # -- URLs and files
    def url_for(self, path: str, params=None) -> str:
        items = list(dict(params or {}).items())
        items.append(("format", "json"))
        return f"{self.base_url}{path}?{urlencode(items)}"

    def _file(self, url: str) -> Path:
        return self.cache_dir / (hashlib.md5(url.encode()).hexdigest() + ".json")

    def _index(self, directory: Path) -> dict:
        found = {}
        if directory.is_dir():
            for marker in directory.glob("*.json.url"):
                try:
                    url = marker.read_text().strip()
                except OSError:
                    continue
                data_file = marker.with_suffix("")
                if data_file.exists():
                    found[_canonical(url)] = data_file
        return found

    def _seeds(self) -> dict:
        if self._seed_index is None:
            merged = {}
            for directory in self.seed_dirs:
                for key, value in self._index(directory).items():
                    merged.setdefault(key, value)
            self._seed_index = merged
        return self._seed_index

    def _store(self, url: str, data) -> None:
        target = self._file(url)
        atomic_write_text(target, json.dumps(data))
        atomic_write_text(target.with_name(target.name + ".url"), url)

    # -- stop and spacing
    def _stop_file(self) -> Path:
        return self.cache_dir / ".stop-until"

    def _stopped_until(self) -> float:
        try:
            return float(self._stop_file().read_text().strip())
        except (OSError, ValueError):
            return 0.0

    def _wait_turn(self) -> None:
        marker = self.cache_dir / ".last-request"
        try:
            last = float(marker.read_text().strip())
        except (OSError, ValueError):
            last = 0.0
        delay = last + self.min_interval - self.clock()
        if delay > 0:
            self.sleep(delay)

    def _mark_request(self) -> None:
        atomic_write_text(self.cache_dir / ".last-request", repr(self.clock()))

    # -- reads
    def get(self, path: str, params=None) -> dict:
        url = self.url_for(path, params)
        target = self._file(url)
        if target.exists():
            self.stats["from_cache"] += 1
            return json.loads(target.read_text(encoding="utf-8"))
        seed = self._seeds().get(_canonical(url))
        if seed is not None:
            data = json.loads(seed.read_text(encoding="utf-8"))
            self._store(url, data)
            self.stats["from_cache"] += 1
            self.stats["from_seed"] += 1
            return data
        return self._live(url)

    def _live(self, url: str) -> dict:
        now = self.clock()
        until = self._stopped_until()
        if now < until:
            raise RateLimited(
                f"hard stop after HTTP 429: no live requests for another {until - now:.0f}s"
            )
        if self.stats["requests"] >= self.max_requests:
            raise BudgetExceeded(f"request cap of {self.max_requests} reached")
        self._wait_turn()
        self.stats["requests"] += 1
        self._mark_request()
        try:
            status, body, headers = self.opener(url, {"User-Agent": self.user_agent_text}, 60)
        except (OSError, urllib.error.URLError) as error:
            self._mark_request()
            raise DatatrackerError(f"request failed: {url}: {error}") from error
        self._mark_request()
        if status == 429:
            retry = 0.0
            try:
                retry = float(headers.get("Retry-After", 0))
            except (TypeError, ValueError):
                pass
            atomic_write_text(self._stop_file(), repr(self.clock() + max(self.stop_seconds, retry)))
            self.stats["http_429"] += 1
            raise RateLimited(f"HTTP 429 for {url}; stopping live requests for {self.stop_seconds:.0f}s")
        if status != 200:
            raise DatatrackerError(f"HTTP {status} for {url}")
        try:
            data = json.loads(body)
        except ValueError as error:
            raise DatatrackerError(f"response is not JSON: {url}: {error}") from error
        self._store(url, data)
        return data

    def get_all(self, path: str, params=None, limit: int = 1000) -> list[dict]:
        """Every object of a list resource, paged by offset, checked against total_count."""
        collected: list[dict] = []
        offset = 0
        total = None
        while True:
            page_params = dict(params or {})
            page_params.update({"limit": limit, "offset": offset, "order_by": "id"})
            data = self.get(path, page_params)
            objects = data.get("objects") or []
            meta = data.get("meta") or {}
            total = meta.get("total_count", total)
            collected.extend(objects)
            if not meta.get("next") or not objects:
                break
            offset += limit
            if offset > 1_000_000:
                raise DatatrackerError(f"paging {path} did not end")
        if total is not None and total != len(collected):
            raise DatatrackerError(
                f"{path}: read {len(collected)} objects but the server reports {total}"
            )
        return collected

    def scan(self, prefix: str):
        """Yield (url, data) for every cached or seeded response of a resource path."""
        seen = set()
        entries = list(self._index(self.cache_dir).items()) + list(self._seeds().items())
        for key, data_file in entries:
            if key in seen or not key[0].startswith(prefix):
                continue
            seen.add(key)
            try:
                yield key, json.loads(data_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue


# --------------------------------------------------------------------------
# History data
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Group:
    id: int
    acronym: str
    type: str
    time: datetime | None


@dataclass(frozen=True)
class Snapshot:
    id: int
    group: int
    time: datetime | None


@dataclass(frozen=True)
class RoleRow:
    id: int
    person: int
    email: str | None = None


def _chair_rows(objects: list[dict]) -> dict[int, list[RoleRow]]:
    rows: dict[int, list[RoleRow]] = defaultdict(list)
    for obj in objects:
        if obj.get("name") and uri_slug(obj["name"]) != "chair":
            continue
        if not obj.get("person"):
            continue
        rows[uri_id(obj["group"])].append(
            RoleRow(int(obj["id"]), uri_id(obj["person"]), obj.get("email") or None)
        )
    for group_rows in rows.values():
        group_rows.sort(key=lambda r: r.id)
    return dict(rows)


class ChairData:
    """Groups, history snapshots, and the chair roles of both snapshots and groups."""

    def __init__(self, groups, snapshots, rolehistory, live):
        self.groups: dict[int, Group] = groups
        self.by_acronym: dict[str, int] = {g.acronym: g.id for g in groups.values()}
        self.snapshots: dict[int, list[Snapshot]] = {
            gid: sorted(items, key=lambda s: s.id) for gid, items in snapshots.items()
        }
        self.rolehistory: dict[int, list[RoleRow]] = rolehistory  # snapshot id -> rows
        self.live: dict[int, list[RoleRow]] = live  # group id -> rows

    @classmethod
    def load(cls, api: ApiCache) -> "ChairData":
        groups = {}
        for obj in api.get_all("/api/v1/group/group/"):
            groups[int(obj["id"])] = Group(
                int(obj["id"]), obj["acronym"], uri_slug(obj["type"]) if obj.get("type") else "",
                api_time(obj.get("time")),
            )
        snapshots: dict[int, list[Snapshot]] = defaultdict(list)
        for obj in api.get_all("/api/v1/group/grouphistory/"):
            snapshots[uri_id(obj["group"])].append(
                Snapshot(int(obj["id"]), uri_id(obj["group"]), api_time(obj.get("time")))
            )
        chair = {"name__slug": "chair"}
        rolehistory = _chair_rows(api.get_all("/api/v1/group/rolehistory/", chair))
        live = _chair_rows(api.get_all("/api/v1/group/role/", chair))
        return cls(groups, dict(snapshots), rolehistory, live)


# --------------------------------------------------------------------------
# Calibration: snapshot id -> write time
# --------------------------------------------------------------------------

def calibration_points(
    snapshots: dict[int, list[Snapshot]], groups: dict[int, Group], only_groups=None
) -> list[tuple[int, datetime]]:
    """(snapshot id, write time) points readable from each group's own history.

    A snapshot's ``time`` is when the state it holds began. Within a group,
    sorted by id (= write order), wherever the next snapshot's time is later
    than this one's, this snapshot was written at that later time. The last
    snapshot was written at the group's ``time`` if that is later than its own.
    """
    points: list[tuple[int, datetime]] = []
    for gid, items in snapshots.items():
        if only_groups is not None and gid not in only_groups:
            continue
        ordered = sorted(items, key=lambda s: s.id)
        for this, following in zip(ordered, ordered[1:]):
            if this.time and following.time and following.time > this.time:
                points.append((this.id, following.time))
        group = groups.get(gid)
        last = ordered[-1] if ordered else None
        if last and group and group.time and last.time and group.time > last.time:
            points.append((last.id, group.time))
    points.sort()
    return points


class Calibration:
    """A monotonic calibration from snapshot id to write time.

    ``raw`` points that are out of order with both neighbours (below both, or
    above both) are dropped, in one pass; the first and last point are kept.
    ``bracket(id)`` is the interval (lower, upper) that the write time lies in:
    the point itself if calibrated, else bounded by the latest write time of any
    lower id and the earliest of any higher id.
    """

    def __init__(self, raw: list[tuple[int, datetime]]):
        pts = sorted(raw)
        self.raw_count = len(pts)
        drop = set()
        for i in range(1, len(pts) - 1):
            before, here, after = pts[i - 1][1], pts[i][1], pts[i + 1][1]
            if (here < before and here < after) or (here > before and here > after):
                drop.add(i)
        self.points = [p for i, p in enumerate(pts) if i not in drop]
        self.dropped = [pts[i] for i in sorted(drop)]
        self.ids = [p[0] for p in self.points]
        running = MIN_T
        self._prefix_max = []
        for _, when in self.points:
            running = max(running, when)
            self._prefix_max.append(running)
        running = MAX_T
        self._suffix_min = [MAX_T] * len(self.points)
        for i in range(len(self.points) - 1, -1, -1):
            running = min(running, self.points[i][1])
            self._suffix_min[i] = running

    def bracket(self, snapshot_id: int) -> tuple[datetime, datetime]:
        i = bisect.bisect_left(self.ids, snapshot_id)
        if i < len(self.ids) and self.ids[i] == snapshot_id:
            return self.points[i][1], self.points[i][1]
        lower = self._prefix_max[i - 1] if i > 0 else MIN_T
        upper = self._suffix_min[i] if i < len(self.ids) else MAX_T
        return lower, upper


# --------------------------------------------------------------------------
# Write-order answer
# --------------------------------------------------------------------------

@dataclass
class WriteOrder:
    status: str  # "confident" | "uncertain"
    persons: frozenset
    source: object  # snapshot id, or "live"
    rows: list
    lower: datetime | None
    upper: datetime | None
    after: list = field(default_factory=list)
    uncertain: list = field(default_factory=list)
    candidates: list = field(default_factory=list)


def people(rows) -> frozenset:
    return frozenset(r.person for r in rows)


def write_order_answer(data: ChairData, calibration: Calibration, group_id: int, t: datetime) -> WriteOrder:
    """The chairs at ``t`` by write order.

    A = snapshots certainly written after t (lower bound > t); C = those whose
    interval contains t. Candidates are the chair sets of every snapshot in C
    plus that of the lowest-id snapshot in A (the live chairs if A is empty).
    Confident only if all candidates are the same set of people.
    """
    snaps = data.snapshots.get(group_id, [])
    after, middle = [], []
    for snap in snaps:
        lower, upper = calibration.bracket(snap.id)
        if lower > t:
            after.append(snap)
        elif lower <= t < upper:
            middle.append(snap)
    candidates = [(s.id, people(data.rolehistory.get(s.id, []))) for s in middle]
    if after:
        first = min(after, key=lambda s: s.id)
        rows = data.rolehistory.get(first.id, [])
        source: object = first.id
        lower, upper = calibration.bracket(first.id)
    else:
        rows = data.live.get(group_id, [])
        source = "live"
        group = data.groups.get(group_id)
        lower, upper = (group.time if group else None), MAX_T
    candidates.append((source, people(rows)))
    confident = len({c[1] for c in candidates}) == 1
    return WriteOrder(
        "confident" if confident else "uncertain",
        people(rows), source, rows, lower, upper,
        [s.id for s in after], [s.id for s in middle], candidates,
    )


def chairs_in_order(rows) -> list[RoleRow]:
    """One row per person (lowest role id), ascending role id: the converter's order."""
    chosen, seen = [], set()
    for row in sorted(rows, key=lambda r: r.id):
        if row.person in seen:
            continue
        seen.add(row.person)
        chosen.append(row)
    return chosen


def email_of(row: RoleRow) -> str | None:
    """The address of a role's email URI (/api/v1/person/email/<address>/)."""
    if not row.email:
        return None
    address = unquote(uri_slug(row.email)).strip()
    return address or None


# --------------------------------------------------------------------------
# Proceedings page
# --------------------------------------------------------------------------

_SECTION = re.compile(r"<h([23])>\s*Chair\(s\):\s*</h\1>(.*?)(?=<h[1-6][\s>]|\Z)", re.S | re.I)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S | re.I)
_ANCHOR = re.compile(r"<a\s[^>]*href\s*=\s*[\"']([^\"']*)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def parse_chair_page(text: str):
    """[(display name, mailto or None), ...] from a proceedings page, None if no chair list."""
    section = _SECTION.search(text)
    if not section:
        return None
    body = section.group(2)
    end = re.search(r"</ul>", body, re.I)
    if end:
        body = body[: end.start()]
    items = _ITEM.findall(body)
    if not items:
        # older pages (meetings 66 to 79): <a href="mailto:..."><b>Name &lt;address&gt;</b></a><br>
        items = [m.group(0) for m in _ANCHOR.finditer(body)]
    entries = []
    for item in items:
        anchor = _ANCHOR.search(item)
        mailto = None
        if anchor:
            name_html = anchor.group(2)
            href = html.unescape(anchor.group(1)).strip()
            if href.lower().startswith("mailto:"):
                mailto = unquote(href[7:].split("?", 1)[0]).strip() or None
        else:
            name_html = item
        name = " ".join(html.unescape(_TAG.sub("", name_html)).split())
        name = re.sub(r"\s*<[^<>]*>\s*$", "", name)
        if name:
            entries.append((name, mailto))
    return entries


@dataclass
class ChairPage:
    status: str  # "ok" | "missing" | "unparsed"
    path: str
    entries: list = field(default_factory=list)


def read_chair_page(root: Path, meeting: int, acronym: str) -> ChairPage:
    path = Path(root) / str(meeting) / f"{acronym}.html"
    try:
        raw = path.read_bytes()
    except OSError:
        return ChairPage("missing", str(path))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    entries = parse_chair_page(text)
    if entries is None:
        return ChairPage("unparsed", str(path))
    return ChairPage("ok", str(path), entries)


# --------------------------------------------------------------------------
# Name matching with accent folding
# --------------------------------------------------------------------------

_FOLD_PLAIN = str.maketrans({"ø": "o", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d"})
_FOLD_DIGRAPH = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ø": "oe", "å": "aa", "ð": "d", "þ": "th",
    "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d",
})
_TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms", "phd", "jr", "sr", "ii", "iii", "iv", "3rd", "md"}

# Switches that exist so the effect of a refinement can be measured against the plan without it.
OPTIONS = {"refined_names": True}


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text)


def fold_variants(text: str) -> set[str]:
    """Spellings of a word with accents dropped, e.g. ø -> o and oe, ä -> a and ae."""
    lowered = text.lower()
    variants = {_ascii(lowered.translate(_FOLD_PLAIN)), _ascii(lowered.translate(_FOLD_DIGRAPH))}
    variants.discard("")
    return variants


_OLD_SUFFIX = re.compile(r"(jr\.?|sr\.?|ii|iii|3rd|phd|dr\.?)", re.I)


def is_title(token: str) -> bool:
    """A title or suffix such as Dr, Prof, Ph.D., Jr, III."""
    if not OPTIONS["refined_names"]:
        return bool(_OLD_SUFFIX.fullmatch(token))
    return re.sub(r"[.,]", "", token.lower()) in _TITLES


def name_tokens(name: str) -> list[str]:
    name = re.sub(r"\(.*?\)", "", name)
    return [t for t in re.split(r"[\s,]+", name.strip()) if t and not is_title(t)]


def last_name(name: str) -> str:
    tokens = name_tokens(name)
    return tokens[-1] if tokens else ""


def first_name(name: str) -> str:
    tokens = name_tokens(name)
    return tokens[0] if len(tokens) > 1 else ""


def unordered_equal(a: str, b: str) -> bool:
    """Do two names consist of the same words in any order (family name first or last)?"""
    ta = [fold_variants(t) for t in name_tokens(a)]
    tb = [fold_variants(t) for t in name_tokens(b)]
    if len(ta) != len(tb) or len(ta) < 2:
        return False
    unused = list(range(len(tb)))
    for variants in ta:
        match = next((i for i in unused if variants & tb[i]), None)
        if match is None:
            return False
        unused.remove(match)
    return True


def _forms(person_name: str, alternates) -> list[str]:
    return [person_name] + (list(alternates or []) if OPTIONS["refined_names"] else [])


def surname_hit(name: str, person_name: str, alternates=None) -> bool:
    """Same last name, accent-folded, under the person's name or any of their alternate names."""
    last = fold_variants(last_name(name))
    for candidate in _forms(person_name, alternates):
        if last & fold_variants(last_name(candidate)):
            return True
        if OPTIONS["refined_names"] and unordered_equal(name, candidate):
            return True
    return False


def initial_ok(name: str, person_name: str, alternates=None) -> bool:
    first = {v[:1] for v in fold_variants(first_name(name))}
    for candidate in _forms(person_name, alternates):
        if first & {v[:1] for v in fold_variants(first_name(candidate))}:
            return True
        if OPTIONS["refined_names"] and unordered_equal(name, candidate):
            return True
    return False


def match_people(entries: list[tuple[str, str | None]], persons: dict[int, str], alternates: dict | None = None):
    """Match page entries to Datatracker people one-to-one, or return None.

    Page and people match by last name with accent folding (titles and suffixes
    such as Dr and Ph.D. ignored, the same words in another order accepted, and
    the alternate names the Datatracker records for a person counted); when several
    people share a last name the first name's folded initial separates them. The
    two sides agree only if there is exactly one perfect one-to-one matching.
    Returns {entry index: person id}.
    """
    alternates = alternates or {}
    unique, seen = [], set()
    for index, (name, _) in enumerate(entries):
        key = tuple(sorted(fold_variants(last_name(name)))) + tuple(sorted(fold_variants(first_name(name))))
        if key in seen:
            continue
        seen.add(key)
        unique.append(index)
    if len(unique) != len(persons):
        return None
    options: dict[int, list[int]] = {}
    for index in unique:
        name = entries[index][0]
        hits = [pid for pid, pname in persons.items() if surname_hit(name, pname, alternates.get(pid))]
        if len(hits) > 1:
            hits = [pid for pid in hits if initial_ok(name, persons[pid], alternates.get(pid))]
        if not hits:
            return None
        options[index] = hits
    solutions: list[dict[int, int]] = []

    def assign(position: int, used: dict[int, int]):
        if len(solutions) > 1:
            return
        if position == len(unique):
            solutions.append(dict(used))
            return
        index = unique[position]
        for pid in options[index]:
            if pid not in used.values():
                used[index] = pid
                assign(position + 1, used)
                del used[index]

    assign(0, {})
    if len(solutions) != 1:
        return None
    matching = solutions[0]
    # Duplicate entries (the same person listed twice) share the person of their twin.
    for index, (name, _) in enumerate(entries):
        if index not in matching:
            twin = next(
                u for u in unique
                if fold_variants(last_name(entries[u][0])) == fold_variants(last_name(name))
                and fold_variants(first_name(entries[u][0])) == fold_variants(first_name(name))
            )
            matching[index] = matching[twin]
    return matching


# --------------------------------------------------------------------------
# People names
# --------------------------------------------------------------------------

class PersonDirectory:
    """Person id -> name. Cached responses first; the rest in batches of id__in."""

    def __init__(self, api: ApiCache, batch: int = 100):
        self.api = api
        self.batch = batch
        self.names: dict[int, str] = {}
        self.alternates: dict[int, list[str]] = {}  # other names the person record carries
        self._scanned = False

    def _scan(self) -> None:
        if self._scanned:
            return
        self._scanned = True
        for _, data in self.api.scan("/api/v1/person/person/"):
            objects = data.get("objects") if isinstance(data, dict) and "objects" in data else [data]
            for obj in objects or []:
                if isinstance(obj, dict) and "id" in obj and obj.get("name"):
                    self._remember(obj)

    def resolve(self, ids) -> dict[int, str]:
        self._scan()
        missing = sorted(set(ids) - set(self.names))
        for start in range(0, len(missing), self.batch):
            chunk = missing[start : start + self.batch]
            data = self.api.get(
                "/api/v1/person/person/", {"id__in": ",".join(map(str, chunk)), "limit": 1000}
            )
            for obj in data.get("objects") or []:
                if obj.get("name"):
                    self._remember(obj)
        return {pid: self.names[pid] for pid in ids if pid in self.names}

    def _remember(self, obj: dict) -> None:
        pid = int(obj["id"])
        self.names[pid] = obj["name"]
        others = []
        for key in ("ascii", "ascii_short", "name_from_draft", "plain"):
            value = " ".join(str(obj.get(key) or "").split())
            if value and value != obj["name"] and value not in others:
                others.append(value)
        if others:
            self.alternates[pid] = others




# --------------------------------------------------------------------------
# The session's own minutes and agenda
# --------------------------------------------------------------------------

_DOC_KINDS = ("minutes", "agenda")
_READABLE_EXT = {"txt", "html", "htm", "md", "markdown", "text", "docx", ""}
_REVISION = re.compile(r"^(?:-\d{2})?(?:\.([A-Za-z0-9]+))?$")
_LABEL = re.compile(
    r"^\s*[\W_]{0,6}(?:(?:wg|bof|rg|ag|working\s+group|research\s+group|session)\s+)?"
    r"(?:co-?)?chair(?:s|\(s\)|person|persons|people)?\s*[:=\-\u2013]\s*(.*)$",
    re.I,
)
_ACTING_LABEL = re.compile(r"^\s*[\W_]{0,6}acting\s+(?:co-?)?chair(?:s|\(s\)|person|persons)?\s*[:=\-\u2013]", re.I)
_STOP_LINE = re.compile(
    r"(minute|note.?tak|jabber|scribe|agenda|responsible|area director|\bads?\b|secretary|etherpad|"
    r"meetecho|presenter|webex|\bremote\b|\btime\b|\broom\b)", re.I)
_EMAIL = re.compile(r"\S+@\S+")
_LABEL_CUT = re.compile(
    r"\b(?:minutes?|notes?|note-?tak\w*|scribes?|jabber|secretary|agenda|responsible|ad|area director|"
    r"mailing list|etherpad|meetecho|presenter|date|time|room|location)\s*[:=]", re.I)
_PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "du", "la", "le", "del", "dos", "ter", "bin",
              "al", "el", "ben", "vander", "dela", "des", "della", "zu", "af"}
_NOT_NAMES = {"tbd", "tba", "none", "n/a", "na", "chair", "chairs", "wg", "bof", "the", "we", "please",
              "remote", "remotely", "present", "absent", "minutes", "agenda", "scribe", "notetaker",
              "secretary", "jabber", "acting", "standing", "temporarily", "co", "and", "or", "unknown"}
_NAME_TOKEN = re.compile(r"^[^\W\d_]+(?:[.'\u2019\-][^\W\d_]+)*\.?$")

# Remarks about a chair's presence, by kind. Fixed note sentences below go with them.
_ABSENT = re.compile(
    r"\bin absentia\b|\bnot present\b|\babsent\b(?!\s+(?:any|an|a|the|objections?|other)\b)|"
    r"\bcould\s?n[o']?t\s+(?:attend|make it)\b|\bcan(?:not|'t)\s+attend\b|\bunable to attend\b|"
    r"\bnot able to attend\b|\bdid not attend\b|\bnot available\b|\bunavailable\b", re.I)
_REMOTE = re.compile(
    r"\bremote(?:ly)?\b(?!\s+(?:participants?|presentations?|presenters?|mic|microphone|access|attendees?|queue|"
    r"users?|people|jabber|speakers?|audience|site|hub|node|procedure|call|control|direct|work|host|sensing))",
    re.I)
_ACTING = re.compile(r"\bacting\b(?!\s+(?:on|as|upon)\b)|standing in|stands in|standin\b|stand-in|substitut(?:e|ing)\b", re.I)
_DEPARTURE = re.compile(r"\bstep(?:s|ped|ping)?\s+down\b", re.I)
_CHAIRED_BY = re.compile(r"\bchaired by\b", re.I)
REMARK_KINDS = (("absent", _ABSENT), ("remote", _REMOTE), ("acting", _ACTING), ("departure", _DEPARTURE))
REMARK_NOTES = {
    "absent": "The session minutes indicate that a named chair was not present at the session.",
    "remote": "The session minutes indicate that a named chair took part remotely.",
    "acting": "The session minutes mention someone acting or standing in for a chair.",
    "departure": "The session minutes record a chair announcing a departure.",
}
_MARK = "\u27e6ACT\u27e7"
_ACT_FOR = re.compile(r"\s+(?:standing in|stands in|standin|stand-in|substituting|acting)\s+for\s+", re.I)
_PAREN_OTHER = re.compile(r"(replac|instead|filling|temporar|interim|\bfor\b)", re.I)


def plausible_name(text: str) -> bool:
    tokens = text.split()
    if not 1 <= len(tokens) <= 5:
        return False
    for index, token in enumerate(tokens):
        if not _NAME_TOKEN.match(token):
            return False
        lowered = token.lower().rstrip(".")
        if lowered in _NOT_NAMES:
            return False
        if token == token.lower() and token.lower() not in _PARTICLES:
            return False
        if index == 0 and token == token.lower():
            return False
    return True


def remark_kinds(text: str) -> list[str]:
    return [kind for kind, pattern in REMARK_KINDS if pattern.search(text)]


def parse_name_list(text: str):
    """The role holders named in the text after a chairs label, names as written; None if it is not a clean list.

    Addresses, affiliations and remarks in parentheses are dropped. A remark that a
    chair was absent, remote or announced a departure leaves that person a role
    holder. Someone acting or standing in is not a role holder and is dropped, while
    the person they stand in for ("standing in for X") is named. "(and X (remark))"
    names X.
    """
    cleaned = re.sub(r"(?m)^\s*[-*\u2022\u00b7]+\s+", "", text.replace("\u00a0", " "))
    for _ in range(8):
        match = re.search(r"\(([^()]*)\)", cleaned)
        if not match:
            break
        inner = match.group(1)
        if _EMAIL.search(inner):
            replacement = " "
        elif _ACTING.search(inner):
            stood = re.match(r"\s*(?:acting|standing in|stands in|standin|stand-in|substitut\w*)\s+for\s+(.+?)\s*$", inner, re.I)
            replacement = f" , {_MARK} , {stood.group(1)} " if stood else f" , {_MARK} , "
        elif remark_kinds(inner):
            replacement = " "
        elif re.match(r"\s*and\s+\S", inner, re.I):
            replacement = " , " + re.sub(r"^\s*and\s+", "", inner, flags=re.I) + " , "
        elif _PAREN_OTHER.search(inner):
            return None
        else:
            replacement = " "
        cleaned = cleaned[:match.start()] + replacement + cleaned[match.end():]
    cleaned = _ACT_FOR.sub(f" , {_MARK} , ", cleaned)
    cleaned = re.sub(r"<[^<>]*>", " ", cleaned).replace("<", " ").replace(">", " ")
    cleaned = re.sub(r"\[[^\[\]]*\]", " ", cleaned)
    cleaned = _EMAIL.sub(" ", cleaned)
    names = []
    for part in re.split(r"[,;&/+\n]|\band\b|\s-\s", cleaned, flags=re.I):
        part = " ".join(part.split()).strip(" .\t")
        if not part:
            continue
        if part == _MARK:
            if names:
                names.pop()
            continue
        if not plausible_name(part):
            return None
        names.append(part)
    return names or None


def _plain_text(raw: str, markup: bool) -> str:
    if markup:
        raw = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</tr>|</div>|</h\d>|</td>", "\n", raw)
        raw = re.sub(r"<(?:/?[A-Za-z][^>]*|!--.*?--)>", " ", raw, flags=re.S)
        raw = html.unescape(raw)
    return raw.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")


@dataclass
class ChairsLine:
    raw: str
    names: list | None  # None: the text after the label is not a list of names


def extract_chairs_lines(text: str, markup: bool = False) -> list[ChairsLine]:
    """Explicit chairs lines (``Chairs:``, ``Chair(s):``, ``Co-chairs:``, ``WG Chairs:``) of a document.

    The names may continue on up to three following lines (one name or a few per
    line). Chairs are never inferred from who speaks.
    """
    lines = _plain_text(text, markup).split("\n")
    found = []
    for index, line in enumerate(lines):
        match = _LABEL.match(line)
        if not match:
            continue
        parts = [_LABEL_CUT.split(match.group(1), maxsplit=1)[0]]
        blanks = 0
        for follow in lines[index + 1:index + 6]:
            if len(parts) >= 4:
                break
            stripped = follow.strip()
            if not stripped:
                if any(p.strip() for p in parts) or blanks >= 2:
                    break
                blanks += 1
                continue
            if ":" in stripped or _LABEL.match(follow) or _STOP_LINE.search(stripped) or len(stripped) > 80:
                break
            if parse_name_list(stripped) is None:
                break
            parts.append(stripped)
        raw = "\n".join(p for p in parts if p.strip())
        if not raw.strip():
            continue
        found.append(ChairsLine(" ".join(raw.split()), parse_name_list(raw)))
    return found


def _name_hit(line: str, names: list[str]) -> bool:
    """Does the line mention one of the chairs: by last name (accent-folded) or by initials such as DK?"""
    lowered = line.lower()
    folded = [lowered.translate(_FOLD_PLAIN), lowered.translate(_FOLD_DIGRAPH)]
    for name in names:
        for variant in fold_variants(last_name(name)):
            if len(variant) > 2 and any(re.search(rf"(?<![a-z]){re.escape(variant)}(?![a-z])", f) for f in folded):
                return True
        tokens = name_tokens(name)
        if len(tokens) >= 2:
            initials = (tokens[0][:1] + tokens[-1][:1]).upper()
            if initials.isalpha() and re.search(rf"(?<![A-Za-z]){initials}(?![A-Za-z])", line):
                return True
    return False


@dataclass
class Remark:
    line: str
    kinds: list  # absent | remote | acting | departure; empty for a "Chaired by" line


def find_remarks(text: str, markup: bool, chair_names: list[str]) -> list[Remark]:
    """Lines of the document that bear on who the chairs were, quoted as they stand.

    A line is quoted when it, or one of the two lines before it, mentions a chair of
    the set and it holds a phrase about absence, remote participation, acting or
    standing in, or stepping down; when it is a labelled "Acting chairs:" line; or
    when it contains "Chaired by". None of this changes who the chairs are.
    """
    lines = _plain_text(text, markup).split("\n")
    wanted: dict[int, list[str]] = {}
    for index, line in enumerate(lines):
        if _ACTING_LABEL.match(line):
            wanted.setdefault(index, [])
            wanted[index] = sorted(set(wanted[index]) | {"acting"})
        if _CHAIRED_BY.search(line):
            wanted.setdefault(index, [])
        if chair_names and _name_hit(line, chair_names):
            for follow in range(index, min(index + 3, len(lines))):
                kinds = remark_kinds(lines[follow])
                if kinds:
                    wanted[follow] = sorted(set(wanted.get(follow, [])) | set(kinds), key=lambda k: [x for x, _ in REMARK_KINDS].index(k))
    return [Remark(" ".join(lines[i].split())[:300], kinds) for i, kinds in sorted(wanted.items()) if lines[i].strip()]


@dataclass
class MinutesDoc:
    name: str
    kind: str
    readable: bool
    lines: list = field(default_factory=list)  # ChairsLine, only those that are clean name lists
    remarks: list = field(default_factory=list)  # Remark

    @property
    def named(self):
        return [line.names for line in self.lines if line.names]


_listing_cache: dict[str, list[str]] = {}


def _listing(directory: Path) -> list[str]:
    key = str(directory)
    if key not in _listing_cache:
        try:
            _listing_cache[key] = sorted(os.listdir(directory))
        except OSError:
            _listing_cache[key] = []
    return _listing_cache[key]


def read_document_text(path: Path):
    """(text, is markup) of a minutes or agenda file, or None if its type cannot be read here."""
    extension = path.suffix.lower().lstrip(".")
    if extension not in _READABLE_EXT:
        return None
    try:
        if extension == "docx":
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            return html.unescape(re.sub(r"<[^>]+>", "", xml)), False
        raw = path.read_bytes()
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return text, extension in ("html", "htm")


def load_documents(root: Path, meeting, acronym: str, chair_names: list[str] | None = None) -> list[MinutesDoc]:
    """The session's minutes and agenda files in the mirror, with their explicit chairs lines.

    With ``chair_names`` (the chairs of the session, from any source) the remarks of
    each document are collected as well.
    """
    docs = []
    for kind in _DOC_KINDS:
        directory = Path(root) / str(meeting) / kind
        prefix = f"{kind}-{meeting}-{acronym}"
        for name in _listing(directory):
            if not name.startswith(prefix) or not _REVISION.match(name[len(prefix):]):
                continue
            read = read_document_text(directory / name)
            if read is None:
                docs.append(MinutesDoc(name, kind, False))
                continue
            text, markup = read
            doc = MinutesDoc(name, kind, True, [l for l in extract_chairs_lines(text, markup) if l.names])
            if chair_names is not None:
                own = [n for line in doc.lines for n in line.names]
                doc.remarks = find_remarks(text, markup, list(chair_names) + own)
            docs.append(doc)
    return docs


def person_matches(name: str, person_name: str, alternates=None) -> bool:
    return any(same_person(name, c) for c in _forms(person_name, alternates))


def judge_names(names: list[str], persons: dict[int, str], alternates: dict | None = None) -> str:
    """"exact" if the names are the same people as ``persons``, "subset" if all are but not every
    person is named, else "different"."""
    alternates = alternates or {}
    entries = [(n, None) for n in names]
    if match_people(entries, persons, alternates) is not None:
        return "exact"
    if names and all(any(person_matches(n, p, alternates.get(pid)) for pid, p in persons.items()) for n in names):
        hit = {pid for pid, p in persons.items() if any(person_matches(n, p, alternates.get(pid)) for n in names)}
        if len(hit) < len(persons) and len(names) <= len(persons):
            return "subset"
    return "different"


def judge_documents(docs: list[MinutesDoc], persons: dict[int, str], alternates: dict | None = None) -> str:
    """Do the session's own documents name exactly ``persons`` as the chairs?

    "confirmed" needs at least one explicit chairs line naming exactly the same
    people and no line naming anyone else; a line naming only some of them does
    not confirm but does not contradict. Otherwise the reason it fails:
    "no minutes document", "no explicit chairs line", "subset" or "different".
    """
    readable = [d for d in docs if d.readable]
    if not readable:
        return "no minutes document"
    verdicts = [judge_names(names, persons, alternates) for d in readable for names in d.named]
    if not verdicts:
        return "no explicit chairs line"
    if "different" in verdicts:
        return "different"
    return "confirmed" if "exact" in verdicts else "subset"


# --------------------------------------------------------------------------
# The converter's own rule, recomputed offline, and its log
# --------------------------------------------------------------------------

@dataclass
class ConverterAnswer:
    kind: str  # "live" | "snapshot" | "no-history" | "ambiguous"
    group: Group
    rows: list = field(default_factory=list)
    snapshot: Snapshot | None = None
    source: str = ""  # ambiguous: "live roles" or "snapshot <id>"
    tied: list = field(default_factory=list)
    tied_time: datetime | None = None


def converter_answer(data: ChairData, group_id: int, cutoff: datetime) -> ConverterAnswer:
    """What the converter does for a dated lookup (datatracker._chair_roles_at), from the cached tables."""
    group = data.groups[group_id]
    snaps = data.snapshots.get(group_id, [])
    if group.time is None:
        raise KeyError(f"group {group.acronym} has no usable time")
    if group.time <= cutoff:
        roles = data.live.get(group_id, [])
        tied = sorted(s.id for s in snaps if s.time == group.time)
        sets = [people(data.rolehistory.get(sid, [])) for sid in tied] + [people(roles)]
        if tied and len(set(sets)) > 1:
            return ConverterAnswer("ambiguous", group, source="live roles", tied=tied, tied_time=group.time)
        return ConverterAnswer("live", group, rows=roles)
    dated = sorted(((s.time, s.id, s) for s in snaps if s.time is not None), key=lambda i: (i[0], i[1]), reverse=True)
    in_force = next((item for item in dated if item[0] <= cutoff), None)
    if in_force is None:
        return ConverterAnswer("no-history", group)
    when, snapshot_id, snapshot = in_force
    tied = sorted(sid for ts, sid, _ in dated if ts == when)
    rows = data.rolehistory.get(snapshot_id, [])
    if len(tied) > 1 and len({people(data.rolehistory.get(sid, [])) for sid in tied}) > 1:
        return ConverterAnswer("ambiguous", group, source=f"snapshot {snapshot_id}", tied=tied, tied_time=when)
    return ConverterAnswer("snapshot", group, rows=rows, snapshot=snapshot)


_CONVERTING = re.compile(r"Converting IETF (\S+) (\S+) session (\S+)\s*$")
_NO_START = re.compile(
    r"Session (\S+) of (\S+) has no start time; looking up chairs as of the end of the meeting's first day \((\S+)\)")
_LIVE = re.compile(r"Chairs for (\S+) at (\S+): live roles, group last edited (\S+)\s*$")
_SNAPSHOT = re.compile(r"Chairs for (\S+) at (\S+): snapshot (\d+) from (\S+)\s*$")
_NO_HISTORY = re.compile(
    r"No (\S+) chairs at (\S+): no history snapshot at or before it \(group last edited (\S+)\)")
_AMBIGUOUS = re.compile(
    r"Chairs for (\S+) at (\S+): (.+?) is ambiguous; snapshots ([\d, ]+) share time (\S+) "
    r"with different chairs; omitting chairs"
)


@dataclass
class LogBlock:
    acronym: str
    session_id: str
    cutoff: datetime | None = None
    kind: str = ""  # live | snapshot | no-history | ambiguous | ""
    group_time: datetime | None = None
    snapshot_id: int | None = None
    snapshot_time: datetime | None = None
    source: str = ""
    tied: list = field(default_factory=list)
    tied_time: datetime | None = None
    no_start: datetime | None = None  # the cutoff the converter logged for a session with no start


def parse_log_blocks(path: Path) -> dict[str, LogBlock]:
    """What the converter logged about each session's chair lookup; the last block per session id wins."""
    blocks: dict[str, LogBlock] = {}
    current = None
    with open(path, errors="replace") as handle:
        for line in handle:
            converting = _CONVERTING.search(line)
            if converting:
                current = LogBlock(converting.group(2), converting.group(3))
                blocks[current.session_id] = current
                continue
            if current is None:
                continue
            if (m := _NO_START.search(line)) and m.group(1) == current.session_id:
                current.no_start = api_time(m.group(3))
            elif (m := _LIVE.search(line)) and m.group(1) == current.acronym:
                current.kind, current.cutoff, current.group_time = "live", api_time(m.group(2)), api_time(m.group(3))
            elif (m := _SNAPSHOT.search(line)) and m.group(1) == current.acronym:
                current.kind, current.cutoff = "snapshot", api_time(m.group(2))
                current.snapshot_id, current.snapshot_time = int(m.group(3)), api_time(m.group(4))
            elif (m := _NO_HISTORY.search(line)) and m.group(1) == current.acronym:
                current.kind, current.cutoff, current.group_time = "no-history", api_time(m.group(2)), api_time(m.group(3))
            elif (m := _AMBIGUOUS.search(line)) and m.group(1) == current.acronym:
                current.kind, current.cutoff = "ambiguous", api_time(m.group(2))
                current.source = m.group(3)
                current.tied = sorted(int(x) for x in m.group(4).replace(" ", "").split(",") if x)
                current.tied_time = api_time(m.group(5))
    return blocks


def log_disagreements(block: LogBlock | None, answer: ConverterAnswer, cutoff: datetime) -> list[str]:
    """Differences between the recomputed lookup and what the converter logged for the session."""
    if block is None:
        return ["the convert log has no record of this session"]
    problems = []
    if block.cutoff != cutoff:
        problems.append(f"log cutoff {show_time(block.cutoff)} vs recomputed {show_time(cutoff)}")
    if block.kind != answer.kind:
        problems.append(f"log says {block.kind or 'nothing'}, recomputation says {answer.kind}")
        return problems
    if answer.kind in ("live", "no-history") and block.group_time != answer.group.time:
        problems.append(
            f"log has group last edited {show_time(block.group_time)}, the cached table {show_time(answer.group.time)}"
            " (the group was probably edited between the cache and the conversion)")
    if answer.kind == "snapshot" and (
        block.snapshot_id != answer.snapshot.id or block.snapshot_time != answer.snapshot.time
    ):
        problems.append(f"log used snapshot {block.snapshot_id}, recomputation snapshot {answer.snapshot.id}")
    if answer.kind == "ambiguous" and (
        block.source != answer.source or block.tied != answer.tied or block.tied_time != answer.tied_time
    ):
        problems.append(
            f"log: {block.source}, snapshots {block.tied}; recomputed: {answer.source}, snapshots {answer.tied}")
    return problems


# --------------------------------------------------------------------------
# Decision: three sources must name the same people
# --------------------------------------------------------------------------

@dataclass
class Decision:
    decision: str  # "recover" | "omit"
    reason: str = ""
    chairs: list = field(default_factory=list)  # [{"name", "mailto"}]


def decide(wo: WriteOrder | None, page: ChairPage, docs: list[MinutesDoc], names: dict[int, str] | None,
           alternates: dict | None = None) -> Decision:
    """Apply the decision rule; every failure names one reason, first match wins.

    The write-order answer must be confident, the proceedings page and the
    session's own minutes or agenda must each name exactly the same people, and
    the group type does not matter. Order of reasons: lookup failed, write order
    uncertain, no proceedings page, no chairs in either source, sources disagree
    (write order and page), then what the documents say: no minutes document, no
    explicit chairs line, minutes name a subset, minutes do not match the other sources.
    """
    if wo is None:
        return Decision("omit", "lookup failed")
    if wo.status != "confident":
        return Decision("omit", "write order uncertain")
    if page.status != "ok":
        return Decision("omit", "no proceedings page")
    if not wo.persons and not page.entries:
        return Decision("omit", "no chairs in either source")
    if not wo.persons or not page.entries:
        return Decision("omit", "sources disagree")
    chair_rows = chairs_in_order(wo.rows)
    if names is None or any(r.person not in names for r in chair_rows):
        return Decision("omit", "lookup failed")
    persons = {r.person: names[r.person] for r in chair_rows}
    matching = match_people(page.entries, persons, alternates)
    if matching is None:
        return Decision("omit", "sources disagree")
    verdict = judge_documents(docs, persons, alternates)
    if verdict != "confirmed":
        return Decision("omit", {
            "no minutes document": "no minutes document",
            "no explicit chairs line": "no explicit chairs line",
            "subset": "minutes name a subset",
            "different": "minutes do not match the other sources",
        }[verdict])
    by_person = {}
    for index in sorted(matching):
        by_person.setdefault(matching[index], page.entries[index][1])
    chairs = [
        {"name": names[r.person], "mailto": by_person.get(r.person) or email_of(r)}
        for r in chair_rows
    ]
    return Decision("recover", "", converter_dedupe(chairs))


# --------------------------------------------------------------------------
# The determination record
# --------------------------------------------------------------------------

def rfc3339(when: datetime | None) -> str | None:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if when else None


def converter_dedupe(chairs: list[dict]) -> list[dict]:
    """Drop a later chair with the same address (or name, if none): the builder's add_party does."""
    kept, seen = [], set()
    for chair in chairs:
        key = chair.get("mailto") or chair["name"]
        if key in seen:
            continue
        seen.add(key)
        kept.append(chair)
    return kept


def chair_entry(chair: dict) -> dict:
    """A chair as the record gives it: name, and address if there is one."""
    entry = {"name": chair["name"]}
    if chair.get("mailto"):
        entry["mailto"] = chair["mailto"]
    return entry


def chair_party(chair: dict) -> dict:
    """A chair party in the key order of the converter's files."""
    party = {}
    if chair.get("mailto"):
        party["mailto"] = chair["mailto"]
    party["name"] = chair["name"]
    party["role"] = "chair"
    return party


REASON_TEXT = {
    "write order uncertain": "the write order of the history records does not settle who chaired",
    "no proceedings page": "there is no proceedings page for the session to confirm the write order of the history",
    "sources disagree": "the write order of the history and the proceedings page name different people",
    "no chairs in either source": "neither the write order of the history nor the proceedings page names any chair",
    "no minutes document": "there is no readable session minutes or agenda document to confirm them",
    "no explicit chairs line": "the session minutes or agenda have no explicit chairs line",
    "minutes name a subset": "the session minutes or agenda name only some of those chairs",
    "minutes do not match the other sources": "the session minutes or agenda do not match the other sources",
    "lookup failed": "the history could not be read completely",
}


def build_body(
    outcome: str, basis: str, as_of: datetime, as_of_basis: str, datatracker: dict, chairs: list[dict],
    candidates: list[dict], reason: str = "", note: str = "",
) -> dict:
    """The body of the determination entry, members in their fixed order."""
    body = {"outcome": outcome, "basis": basis, "as_of": rfc3339(as_of), "as_of_basis": as_of_basis,
            "datatracker": datatracker}
    if outcome == "named":
        body["chairs"] = [chair_entry(c) for c in chairs]
    if candidates:
        body["candidates"] = candidates
    if reason:
        body["reason"] = reason
    if note:
        body["note"] = note
    return body


def make_entry(body: dict) -> dict:
    """The analysis entry, keys in the order the converter's builder writes an analysis entry."""
    return {
        "type": "report",
        "sessions": 0,
        "vendor": ENTRY_VENDOR,
        "body": json.dumps(body, ensure_ascii=False),
        "encoding": "json",
        "mediatype": "application/json",
        "product": ENTRY_PRODUCT,
        "schema": ENTRY_SCHEMA,
    }


ENTRY_KEYS = list(make_entry({}))
_BODY_ORDER = ["outcome", "basis", "as_of", "as_of_basis", "datatracker", "chairs", "candidates", "reason", "note"]
_DT_KEYS = ["group", "group_last_edited", "snapshot", "snapshot_time"]
_CANDIDATE_KEYS = {
    "previous-release": ["source", "version", "chairs"],
    "history-write-order": ["source", "confident", "snapshot", "written_between", "chairs"],
    "proceedings-page": ["source", "url", "chairs"],
    "session-minutes": ["source", "document", "chairs_named", "remarks"],
    "tied-history-records": ["source", "timestamp", "chair_sets"],
}


def is_determination(entry) -> bool:
    return isinstance(entry, dict) and entry.get("product") == ENTRY_PRODUCT and entry.get("schema") == ENTRY_SCHEMA


def body_problems(body) -> list[str]:
    """Shape problems of a determination body (empty if well formed)."""
    problems = []
    if not isinstance(body, dict):
        return ["body is not an object"]
    keys = list(body)
    if keys != [k for k in _BODY_ORDER if k in body]:
        problems.append(f"body members {keys} are not in the fixed order")
    if body.get("outcome") not in ("named", "none"):
        problems.append(f"outcome {body.get('outcome')!r} is not named or none")
    if body.get("basis") not in BASES:
        problems.append(f"basis {body.get('basis')!r} is not one of the defined values")
    if (body.get("basis") in NAMED_BASES) != (body.get("outcome") == "named"):
        problems.append("outcome and basis do not go together")
    if body.get("as_of_basis") not in ("session-start", "meeting-first-day"):
        problems.append("as_of_basis is not session-start or meeting-first-day")
    for key in ("as_of",):
        if not isinstance(body.get(key), str) or api_time(body.get(key)) is None:
            problems.append(f"{key} is not an RFC 3339 time")
    datatracker = body.get("datatracker")
    if not isinstance(datatracker, dict) or any(k not in _DT_KEYS for k in datatracker) or "group" not in datatracker:
        problems.append("datatracker member is malformed")
    if (body.get("outcome") == "named") != ("chairs" in body):
        problems.append("chairs must be present exactly when the outcome is named")
    for chair in body.get("chairs") or []:
        if not isinstance(chair, dict) or "name" not in chair or set(chair) - {"name", "mailto"}:
            problems.append("a chair entry is malformed")
    if body.get("outcome") == "none" and not body.get("reason"):
        problems.append("outcome none needs a reason")
    for candidate in body.get("candidates") or []:
        source = candidate.get("source") if isinstance(candidate, dict) else None
        if source not in _CANDIDATE_KEYS:
            problems.append(f"unknown candidate source {source!r}")
            continue
        allowed = _CANDIDATE_KEYS[source]
        if [k for k in candidate if k not in allowed] or list(candidate) != [k for k in allowed if k in candidate]:
            problems.append(f"candidate {source} has members out of shape")
        if any(v is None for k, v in candidate.items() if k != "snapshot"):
            problems.append(f"candidate {source} has a null member")
    return problems


def entry_problems(entry) -> list[str]:
    """Shape problems of a whole determination entry, including its body."""
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    problems = []
    if list(entry) != ENTRY_KEYS:
        problems.append(f"entry keys {list(entry)} are not {ENTRY_KEYS}")
    for key, value in make_entry({}).items():
        if key != "body" and entry.get(key) != value:
            problems.append(f"entry {key} is {entry.get(key)!r}, not {value!r}")
    text = entry.get("body")
    if not isinstance(text, str):
        return problems + ["entry body is not a string"]
    try:
        body = json.loads(text)
    except ValueError:
        return problems + ["entry body is not JSON"]
    if text != json.dumps(body, ensure_ascii=False):
        problems.append("entry body is not in the converter's serialization")
    return problems + body_problems(body)


def chairs_listing(rows, names: dict[int, str]) -> list[dict]:
    return converter_dedupe([
        {"name": names[r.person], "mailto": email_of(r)} for r in chairs_in_order(rows)
    ])


# --------------------------------------------------------------------------
# Files and the plan
# --------------------------------------------------------------------------

_FILE_RE = re.compile(r"^(ietf\d+|interim-[^_]+)_(.+)_(\d+)\.vcon\.json$")


def parse_meeting(text: str):
    """A numbered meeting is an int; an interim meeting keeps its name (interim-2026-vcon-01)."""
    return int(text) if str(text).isdigit() else str(text)


def meeting_dir(meeting) -> str:
    """The directory (and file name stem) of a meeting: ietf84, interim-2026-vcon-01."""
    return f"ietf{meeting}" if isinstance(meeting, int) else str(meeting)


def meeting_key(meeting):
    return (0, meeting, "") if isinstance(meeting, int) else (1, 0, str(meeting))


def completed_meetings(state_dir: Path) -> list:
    """Meetings with a ``<state-dir>/<N>.done`` marker: numbers first, then interim meetings by name."""
    found = []
    for marker in Path(state_dir).glob("*.done"):
        if marker.stem.isdigit() or marker.stem.startswith("interim-"):
            found.append(parse_meeting(marker.stem))
    return sorted(found, key=meeting_key)


def chairs_of(vcon: dict) -> list[dict]:
    return [p for p in vcon.get("parties") or [] if isinstance(p, dict) and p.get("role") == "chair"]


def chair_dicts(vcon: dict) -> list[dict]:
    return [{"name": p.get("name"), "mailto": p.get("mailto") or None} for p in chairs_of(vcon)]


def determinations(vcon: dict) -> list[dict]:
    return [a for a in vcon.get("analysis") or [] if is_determination(a)]


class PublishedReader:
    """Chair parties of the previous release's files, read through one ``git cat-file --batch``."""

    def __init__(self, git_dir: Path, rev: str = "HEAD"):
        self.rev = rev
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        self.process = subprocess.Popen(
            ["git", "-C", str(git_dir), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )

    def chairs(self, relpath: str):
        """[] if the published file has no chair party, None if it is not published."""
        self.process.stdin.write(f"{self.rev}:{relpath}\n".encode())
        self.process.stdin.flush()
        header = self.process.stdout.readline().decode().split()
        if len(header) != 3 or header[1] != "blob":
            return None
        data = self.process.stdout.read(int(header[2]))
        self.process.stdout.read(1)
        try:
            return chair_dicts(json.loads(data.decode("utf-8")))
        except ValueError:
            return None

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.process.kill()


def release_problem(git_dir: Path, rev: str, expected_version: str = DEFAULT_PREVIOUS_VERSION) -> str | None:
    """Why ``rev`` is not the previous release the records name (None if it is)."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "-C", str(git_dir), "cat-file", "blob", f"{rev}:dataset.json"],
                            capture_output=True, env=env)
    if result.returncode != 0:
        return f"cannot read dataset.json at {rev}"
    try:
        version = json.loads(result.stdout.decode("utf-8")).get("version")
    except ValueError:
        return f"dataset.json at {rev} is not JSON"
    if version != expected_version:
        return f"dataset.json at {rev} says version {version}, not {expected_version}"
    return None


def same_person(a: str, b: str) -> bool:
    """Same last name (accent-folded) and, where both have one, the same first initial; or the same words in another order."""
    if not fold_variants(last_name(a)) & fold_variants(last_name(b)):
        return bool(OPTIONS["refined_names"] and unordered_equal(a, b))
    fa, fb = first_name(a), first_name(b)
    if not fa or not fb:
        return True
    return bool({v[:1] for v in fold_variants(fa)} & {v[:1] for v in fold_variants(fb)})


def compare_people(recovered: list[dict], published) -> str:
    """How the chairs now in the file relate to the published file's chairs."""
    if published is None:
        return "no published file"
    if not published:
        return "published has no chairs"
    ours = [c["name"] for c in recovered]
    theirs = [p.get("name") or "" for p in published]
    if len(ours) == len(theirs) and ours and match_people(
        [(n, None) for n in theirs], dict(enumerate(ours))
    ) is not None:
        return "same people"
    if any(same_person(a, b) for a in ours for b in theirs):
        return "overlap"
    return "different people"


def format_chairs(chairs) -> str:
    return "; ".join(f"{c.get('name')} <{c.get('mailto') or ''}>" for c in chairs or [])


@dataclass
class Context:
    data: ChairData
    calibration: Calibration
    persons: object  # PersonDirectory-like: resolve(ids) -> {id: name}
    proceedings: Path
    output_root: Path
    published: object = None  # callable(relpath) -> chairs list | None
    meeting_dates: dict = field(default_factory=dict)  # meeting number -> date
    previous_version: str = DEFAULT_PREVIOUS_VERSION  # the version recorded in previous-release candidates


def load_meeting_dates(api: ApiCache) -> dict[int, object]:
    """First day of each numbered meeting (one cached request for the whole table)."""
    from datetime import date
    dates = {}
    data = api.get("/api/v1/meeting/meeting/", {"type": "ietf", "limit": 1000})
    for obj in data.get("objects") or []:
        if str(obj.get("number", "")).isdigit() and obj.get("date"):
            dates[int(obj["number"])] = date.fromisoformat(obj["date"])
    return dates


@dataclass
class _Stage:
    path: Path
    relpath: str
    meeting: int
    acronym: str
    session_id: str
    vcon: dict | None
    group: Group | None = None
    cutoff: datetime | None = None
    as_of_basis: str = "session-start"
    answer: ConverterAnswer | None = None
    wo: WriteOrder | None = None
    problems: list = field(default_factory=list)


def _stage_file(ctx: Context, path: Path, meeting, blocks: dict) -> _Stage:
    match = _FILE_RE.match(path.name)
    relpath = f"{meeting_dir(meeting)}/{path.name}"
    if not match or match.group(1) != meeting_dir(meeting):
        return _Stage(path, relpath, meeting, "", "", None,
                      problems=["file name is not <meeting>_<group>_<id>.vcon.json"])
    stage = _Stage(path, relpath, meeting, match.group(2), match.group(3), None)
    try:
        vcon = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(vcon, dict):
            raise ValueError("not an object")
        stage.vcon = vcon
    except (OSError, ValueError) as error:
        stage.problems.append(f"file cannot be read: {error}")
        return stage
    block = blocks.get(stage.session_id)
    group_id = ctx.data.by_acronym.get(stage.acronym)
    stage.group = ctx.data.groups.get(group_id) if group_id is not None else None
    if stage.group is None:
        stage.problems.append(f"group {stage.acronym} is not in the cached group table")
        return stage
    sessions = vcon.get("sessions") or [{}]
    start = api_time(sessions[0].get("start")) if isinstance(sessions[0], dict) else None
    if start is not None:
        stage.cutoff, stage.as_of_basis = start, "session-start"
        if block is not None and block.no_start is not None:
            stage.problems.append("the log says the session had no start time but the file has one")
    else:
        stage.as_of_basis = "meeting-first-day"
        first = ctx.meeting_dates.get(meeting)
        derived = datetime(first.year, first.month, first.day, 23, 59, 59, tzinfo=UTC) if first else None
        logged = block.no_start if block is not None else None
        if derived and logged and derived != logged:
            stage.problems.append(f"meeting first day {show_time(derived)} vs logged {show_time(logged)}")
        stage.cutoff = derived or logged
        if stage.cutoff is None:
            stage.problems.append("no start time and no meeting date to date the lookup")
            return stage
    try:
        stage.answer = converter_answer(ctx.data, group_id, stage.cutoff)
    except KeyError as error:
        stage.problems.append(str(error))
        return stage
    stage.problems += log_disagreements(block, stage.answer, stage.cutoff)
    if stage.answer.kind == "ambiguous":
        stage.wo = write_order_answer(ctx.data, ctx.calibration, group_id, stage.cutoff)
    return stage


def _persons_needed(ctx: Context, stage: _Stage) -> set[int]:
    needed = set()
    if stage.answer is None:
        return needed
    needed |= {r.person for r in stage.answer.rows}
    for sid in stage.answer.tied:
        needed |= {r.person for r in ctx.data.rolehistory.get(sid, [])}
    if stage.wo is not None:
        needed |= {r.person for r in stage.wo.rows}
    return needed


def _dt_member(stage: _Stage, snapshot: Snapshot | None) -> dict:
    member = {"group": stage.acronym, "group_last_edited": rfc3339(stage.group.time)}
    if snapshot is not None:
        member["snapshot"] = snapshot.id
        member["snapshot_time"] = rfc3339(snapshot.time)
    return member


def _evidence_candidates(ctx, stage, names, page, docs) -> list[dict]:
    """Candidate entries for an ambiguous session: write order, tied records, page, minutes."""
    out = []
    wo, answer = stage.wo, stage.answer
    if wo is not None:
        entry = {"source": "history-write-order", "confident": wo.status == "confident",
                 "snapshot": wo.source if isinstance(wo.source, int) else None}
        if wo.source != "live" and wo.lower not in (None, MIN_T) and wo.upper not in (None, MAX_T):
            entry["written_between"] = [rfc3339(wo.lower), rfc3339(wo.upper)]
        entry["chairs"] = [chair_entry(c) for c in chairs_listing(wo.rows, names)]
        out.append(entry)
    seen, sets = set(), []
    for sid in answer.tied:
        rows = ctx.data.rolehistory.get(sid, [])
        key = people(rows)
        if key not in seen:
            seen.add(key)
            sets.append([chair_entry(c) for c in chairs_listing(rows, names)])
    if sets:
        out.append({"source": "tied-history-records", "timestamp": rfc3339(answer.tied_time), "chair_sets": sets})
    out += _page_and_minutes_candidates(stage, page, docs)
    return out


def _page_and_minutes_candidates(stage, page, docs) -> list[dict]:
    out = []
    if page is not None and page.status == "ok" and page.entries:
        out.append({"source": "proceedings-page",
                    "url": f"https://www.ietf.org/proceedings/{stage.meeting}/{stage.acronym}.html",
                    "chairs": [chair_entry({"name": n, "mailto": m}) for n, m in page.entries]})
    for doc in docs or []:
        if doc.named or doc.remarks:
            entry = {"source": "session-minutes", "document": doc.name}
            if doc.named:
                entry["chairs_named"] = list(doc.named[0])
            if doc.remarks:
                entry["remarks"] = [r.line for r in doc.remarks]
            out.append(entry)
    return out


def _two_source(wo, page, names, alternates=None) -> bool:
    """Do the write order and the page agree (the earlier rule, research groups aside)?"""
    if wo is None or wo.status != "confident" or page.status != "ok" or not wo.persons or not page.entries:
        return False
    rows = chairs_in_order(wo.rows)
    if names is None or any(r.person not in names for r in rows):
        return False
    return match_people(page.entries, {r.person: names[r.person] for r in rows}, alternates) is not None


def plan_meeting(ctx: Context, meeting, blocks: dict) -> list[dict]:
    """One row per file of the meeting: what to record, and the chairs to add where confirmed."""
    directory = Path(ctx.output_root) / meeting_dir(meeting)
    stages = [_stage_file(ctx, p, meeting, blocks) for p in sorted(directory.glob("*.vcon.json"))]
    wanted = set()
    for stage in stages:
        wanted |= _persons_needed(ctx, stage)
    names, failure = {}, ""
    try:
        names = ctx.persons.resolve(sorted(wanted))
    except (RateLimited, BudgetExceeded):
        raise
    except DatatrackerError as error:
        failure = str(error)
    return [_plan_row(ctx, stage, names, failure) for stage in stages]


def _plan_row(ctx: Context, stage: _Stage, names: dict, failure: str) -> dict:
    vcon, answer, group = stage.vcon, stage.answer, stage.group
    row = {
        "meeting": stage.meeting, "file": stage.path.name, "path": stage.relpath, "session_id": stage.session_id,
        "acronym": stage.acronym, "group_type": group.type if group else "",
        "as_of": rfc3339(stage.cutoff), "as_of_basis": stage.as_of_basis,
        "kind": answer.kind if answer else "", "ambiguous": bool(answer and answer.kind == "ambiguous"),
        "basis": "", "outcome": "", "action": "mismatch", "decision": "", "reason": "",
        "chairs": [], "file_chairs": chair_dicts(vcon) if vcon else [],
        "entry": None, "problems": list(stage.problems),
        "snapshot_used": "", "written_after": "", "written_before": "", "uncertain_snapshots": [],
        "page": "", "page_status": "", "page_chairs": [], "minutes_verdict": "", "minutes_docs": [],
        "minutes_unread": [], "two_source": False, "write_order_status": "",
        "remark_kinds": [], "chaired_by_remarks": 0,
        "previous_chairs": None, "comparison": "", "existing_entry": bool(vcon and determinations(vcon)),
    }
    if answer is None or vcon is None or row["problems"]:
        return row  # disagreements with the converter's log are reported, never written
    if any(pid not in names for pid in _persons_needed(ctx, stage)):
        row["problems"].append(
            f"person names could not be read: {failure}" if failure else "a chair's name is not available")
        return row

    file_chairs = chair_dicts(vcon)
    previous = ctx.published(stage.relpath) if ctx.published else None
    row["previous_chairs"] = previous
    page = docs = None
    decision = None
    if answer.kind in ("live", "snapshot"):
        recomputed = chairs_listing(answer.rows, names)
        if recomputed != file_chairs:
            row["problems"].append(
                f"the file has chairs {format_chairs(file_chairs) or '(none)'} but the converter's rule gives "
                f"{format_chairs(recomputed) or '(none)'}")
            return row
        current = recomputed
        snapshot = answer.snapshot
        if current:
            outcome, basis, reason = "named", "live-roles" if answer.kind == "live" else "history-snapshot", ""
        else:
            outcome, basis, reason = "none", "no-chairs-recorded", "The Datatracker record for that time lists no chairs."
    else:
        # An ambiguous session whose file has chairs is acceptable only if this tool put them there: the
        # file then carries its determination entry, and the three sources must still confirm those chairs.
        if file_chairs and not (answer.kind == "ambiguous" and row["existing_entry"]):
            row["problems"].append(
                f"the file has chairs {format_chairs(file_chairs)} but the converter's rule gives none ({answer.kind})")
            return row
        current, snapshot = [], None
        if answer.kind == "no-history":
            outcome, basis = "none", "no-history"
            reason = ("The session predates the group's history in the Datatracker, and the group was edited "
                      "later, so the chairs at the time are not recorded.")
        else:
            outcome, basis, reason = "none", "ambiguous-history", ""
    note = ""
    has_remarks = False
    if answer.kind == "ambiguous":
        wo = stage.wo
        page = read_chair_page(ctx.proceedings, stage.meeting, stage.acronym)
        alternates = getattr(ctx.persons, "alternates", {}) or {}
        known = [names[r.person] for r in chairs_in_order(wo.rows)] + [n for n, _ in page.entries]
        docs = load_documents(ctx.proceedings, stage.meeting, stage.acronym, known)
        decision = decide(wo, page, docs, names, alternates)
        if file_chairs and not (decision.decision == "recover" and decision.chairs == file_chairs):
            row["problems"].append(
                f"the file has chairs {format_chairs(file_chairs)} that the three sources do not confirm "
                f"({decision.reason or 'they name ' + format_chairs(decision.chairs)})")
            return row
        row.update(
            decision=decision.decision, reason=decision.reason, write_order_status=wo.status,
            snapshot_used=wo.source, written_after=show_time(wo.lower), written_before=show_time(wo.upper),
            uncertain_snapshots=wo.uncertain, page=page.path, page_status=page.status,
            page_chairs=[{"name": n, "mailto": m} for n, m in page.entries],
            two_source=_two_source(wo, page, names, alternates),
            minutes_docs=[d.name for d in docs if d.readable], minutes_unread=[d.name for d in docs if not d.readable],
        )
        persons = {r.person: names[r.person] for r in chairs_in_order(wo.rows)}
        row["minutes_verdict"] = judge_documents(docs, persons, alternates) if wo.status == "confident" and persons else ""
        kinds = [k for k, _ in REMARK_KINDS if any(k in r.kinds for d in docs for r in d.remarks)]
        row["remark_kinds"] = kinds
        row["chaired_by_remarks"] = sum(1 for d in docs for r in d.remarks if _CHAIRED_BY.search(r.line))
        has_remarks = any(d.remarks for d in docs)
        if decision.decision == "recover":
            current = decision.chairs
            outcome, basis = "named", "minutes-confirmed"
            snapshot = next((s for s in ctx.data.snapshots.get(stage.group.id, []) if s.id == wo.source), None)
            reason = ("History records share one timestamp with different chairs; the write order of the history, "
                      "the proceedings page and the session minutes or agenda name the same chairs.")
            note = " ".join(
                ["Addresses come from the proceedings page, which reflects the chairs some weeks after the meeting."]
                + [REMARK_NOTES[k] for k in kinds])
        else:
            reason = ("History records share one timestamp with different chairs, and "
                      + REASON_TEXT[decision.reason] + ".")
    elif outcome == "none":
        page = read_chair_page(ctx.proceedings, stage.meeting, stage.acronym)
        docs = load_documents(ctx.proceedings, stage.meeting, stage.acronym)
        row.update(page=page.path, page_status=page.status,
                   page_chairs=[{"name": n, "mailto": m} for n, m in page.entries],
                   minutes_docs=[d.name for d in docs if d.readable], minutes_unread=[d.name for d in docs if not d.readable])

    candidates = []
    differs = bool(previous) and compare_people(current, previous) != "same people"
    confirmed = decision is not None and decision.decision == "recover"
    if outcome == "none" or differs or has_remarks or confirmed:
        if differs:
            candidates.append({"source": "previous-release", "version": ctx.previous_version,
                               "chairs": [chair_entry(c) for c in previous]})
        if answer.kind == "ambiguous":
            candidates += _evidence_candidates(ctx, stage, names, page, docs)
        elif outcome == "none":
            candidates += _page_and_minutes_candidates(stage, page, docs)
    row["comparison"] = compare_people(current, previous) if (previous is not None) else "no published file"
    datatracker = _dt_member(stage, snapshot)
    body = build_body(outcome, basis, stage.cutoff, stage.as_of_basis, datatracker, current, candidates, reason, note)
    entry = make_entry(body)
    problems = entry_problems(entry)
    if problems:
        row["problems"] += problems
        return row
    recover = decision is not None and decision.decision == "recover"
    row.update(basis=basis, outcome=outcome, action="recover" if recover else "record",
               chairs=current if recover else [], entry=entry)
    if recover:
        log.info("Recovered chairs for %s at %s: write-order %s agrees with proceedings/%s/%s.html and the session minutes",
                 stage.acronym, row["as_of"],
                 f"snapshot {stage.wo.source} (written between {show_time(stage.wo.lower)} and {show_time(stage.wo.upper)})"
                 if stage.wo.source != "live" else f"live roles (group last edited {show_time(stage.wo.lower)})",
                 stage.meeting, stage.acronym)
    elif answer.kind == "ambiguous":
        log.info("Chairs for %s at %s remain omitted: %s", stage.acronym, row["as_of"], decision.reason)
    return row


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

TSV_COLUMNS = [
    "meeting", "file", "acronym", "group_type", "as_of", "as_of_basis", "kind", "basis", "outcome", "action",
    "decision", "reason", "chairs_to_add", "snapshot_used", "minutes_verdict", "page_status",
    "previous_chairs", "comparison", "problems", "session_id",
]


def summarize(rows: list[dict]) -> dict:
    by_meeting: dict[int, dict] = {}
    for row in rows:
        e = by_meeting.setdefault(row["meeting"], {
            "files": 0, "bases": Counter(), "ambiguous": 0, "recovered": 0, "omitted": Counter(),
            "mismatches": 0, "earlier_two_source": 0, "earlier_now_omitted": Counter(),
            "research_groups_recovered": 0,
        })
        e["files"] += 1
        if row["action"] == "mismatch":
            e["mismatches"] += 1
            continue
        e["bases"][row["basis"]] += 1
        if row["ambiguous"]:
            e["ambiguous"] += 1
            if row["decision"] == "recover":
                e["recovered"] += 1
                if row["group_type"] in ("rg", "rag"):
                    e["research_groups_recovered"] += 1
            else:
                e["omitted"][row["reason"]] += 1
            if row["two_source"] and row["group_type"] not in ("rg", "rag"):
                e["earlier_two_source"] += 1
                if row["decision"] != "recover":
                    e["earlier_now_omitted"][row["reason"]] += 1
    return {
        str(m): {k: (dict(v) if isinstance(v, Counter) else v) for k, v in e.items()}
        for m, e in sorted(by_meeting.items(), key=lambda item: meeting_key(item[0]))
    }


def summary_text(summary: dict, stats: dict) -> str:
    out = []
    bases = list(BASES)
    out.append("meeting  files  " + "  ".join(bases) + "  mismatches")
    total = Counter()
    for meeting, e in summary.items():
        counts = [e["bases"].get(b, 0) for b in bases]
        out.append(f"{meeting:>7}  {e['files']:>5}  " + "  ".join(str(c).rjust(len(b)) for c, b in zip(counts, bases))
                   + f"  {e['mismatches']:>10}")
        total["files"] += e["files"]
        total["mismatches"] += e["mismatches"]
        for b in bases:
            total[b] += e["bases"].get(b, 0)
    out.append(f"  total  {total['files']:>5}  " + "  ".join(str(total[b]).rjust(len(b)) for b in bases)
               + f"  {total['mismatches']:>10}")
    reasons = list(REASONS)
    out += ["", "ambiguous sessions: meeting  ambiguous  recovered  " + "  ".join(reasons)]
    tot = Counter()
    for meeting, e in summary.items():
        if not e["ambiguous"]:
            continue
        counts = [e["omitted"].get(r, 0) for r in reasons]
        out.append(f"{meeting:>7}  {e['ambiguous']:>9}  {e['recovered']:>9}  "
                   + "  ".join(str(c).rjust(len(r)) for c, r in zip(counts, reasons)))
        tot["ambiguous"] += e["ambiguous"]
        tot["recovered"] += e["recovered"]
        for r in reasons:
            tot[r] += e["omitted"].get(r, 0)
    out.append(f"  total  {tot['ambiguous']:>9}  {tot['recovered']:>9}  " + "  ".join(str(tot[r]).rjust(len(r)) for r in reasons))
    out += ["", "earlier two-source recoveries (research groups excluded): meeting  earlier  now omitted by reason"]
    ear = Counter()
    for meeting, e in summary.items():
        if not e["earlier_two_source"]:
            continue
        omitted = e["earlier_now_omitted"]
        out.append(f"{meeting:>7}  {e['earlier_two_source']:>7}  " + ", ".join(f"{k}: {v}" for k, v in omitted.items()))
        ear["earlier"] += e["earlier_two_source"]
        for k, v in omitted.items():
            ear[k] += v
    out.append(f"  total  {ear['earlier']:>7}  " + ", ".join(f"{k}: {v}" for k, v in ear.items() if k != "earlier"))
    rg = sum(e["research_groups_recovered"] for e in summary.values())
    out.append(f"research groups confirmed by all three sources: {rg}")
    out += ["", "run: " + ", ".join(f"{k}={v}" for k, v in stats.items())]
    return "\n".join(out) + "\n"


def write_reports(rows: list[dict], summary: dict, stats: dict, report_dir: Path, prefix: str = "plan") -> None:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    with open(report_dir / f"{prefix}.tsv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(TSV_COLUMNS)
        for row in rows:
            previous = row["previous_chairs"]
            writer.writerow([
                str(value).replace("\t", " ").replace("\n", " ") for value in (
                    row["meeting"], row["file"], row["acronym"], row["group_type"], row["as_of"], row["as_of_basis"],
                    row["kind"], row["basis"], row["outcome"], row["action"], row["decision"], row["reason"],
                    format_chairs(row["chairs"]), row["snapshot_used"], row["minutes_verdict"], row["page_status"],
                    "(not published)" if previous is None else format_chairs(previous), row["comparison"],
                    " | ".join(row["problems"]), row["session_id"],
                )
            ])
    atomic_write_text(
        report_dir / f"{prefix}.json",
        json.dumps({"summary": summary, "run": stats, "sessions": rows}, indent=1, ensure_ascii=False, default=str) + "\n",
    )
    atomic_write_text(report_dir / f"{prefix}-summary.txt", summary_text(summary, stats))
    mismatches = [r for r in rows if r["action"] == "mismatch"]
    lines = [f"{r['path']}\t{' | '.join(r['problems'])}" for r in mismatches]
    atomic_write_text(report_dir / f"{prefix}-mismatches.txt", "\n".join(lines) + ("\n" if lines else ""))


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

class ApplyError(Exception):
    pass


def agrees_with_parties(vcon: dict, body: dict) -> bool:
    """Do the chairs the record names equal the chair parties of the file?"""
    named = [{"name": c["name"], "mailto": c.get("mailto") or None} for c in body.get("chairs") or []]
    return chair_dicts(vcon) == (named if body.get("outcome") == "named" else [])


def build_updated(old: dict, entry: dict, chairs: list[dict], now: datetime, append: bool = True) -> dict:
    """The file with the determination appended and, for confirmed chairs, the chairs added.

    Chair parties first, the attendees party last; every index list is
    range(len(parties)); attachments keep party 0 (the first chair now, as in the
    converter's files); ``extensions`` and every attachment stay as they are;
    ``updated_at`` is the time of the change. Raises ApplyError for any file that
    is not in the shape of a regenerated session.
    """
    if not isinstance(old.get("analysis"), list):
        raise ApplyError("analysis is not a list")
    new = copy.deepcopy(old)
    if chairs:
        if chairs_of(old):
            raise ApplyError("file already has a chair party")
        parties = old.get("parties")
        if not isinstance(parties, list) or len(parties) != 1:
            raise ApplyError(f"expected exactly one party (the attendees), found {len(parties or [])}")
        if parties[0].get("role") != "attendee" or parties[0].get("name") != "IETF Attendees":
            raise ApplyError("the only party is not the IETF Attendees party")
        sessions = old.get("sessions")
        if not isinstance(sessions, list) or len(sessions) != 1 or sessions[0].get("parties") != [0]:
            raise ApplyError("sessions[0].parties is not [0]")
        for index, dialog in enumerate(old.get("dialog") or []):
            if dialog.get("parties") != [0]:
                raise ApplyError(f"dialog {index} parties are not [0]")
        for index, item in enumerate(old.get("attachments") or []):
            if item.get("party") != 0:
                raise ApplyError(f"attachment {index} party is {item.get('party')!r}, not 0")
        new["parties"] = [chair_party(c) for c in chairs] + copy.deepcopy(parties)
        indices = list(range(len(new["parties"])))
        new["sessions"][0]["parties"] = list(indices)
        for dialog in new.get("dialog") or []:
            dialog["parties"] = list(indices)
    if append:
        new["analysis"].append(copy.deepcopy(entry))
    new["updated_at"] = now.isoformat()
    return new


def verify_only_expected(old: dict, new: dict, chairs: list[dict], entry: dict, appended: bool = True) -> list[str]:
    """Problems found comparing the new file with the file as read; [] if only the allowed changed.

    Allowed: the one appended analysis entry, ``updated_at``, and, when chairs are
    confirmed, ``parties`` (chairs prepended) with the index lists of the Sessions
    and Dialogs. Attachments, including meeting_metadata, never change.
    """
    problems = []
    if list(old) != list(new):
        problems.append("top-level keys or their order changed")
    for key in old:
        if key in ("parties", "updated_at", "sessions", "dialog", "analysis"):
            continue
        if old[key] != new.get(key):
            problems.append(f"{key} changed")
    expected_analysis = list(old.get("analysis") or []) + ([entry] if appended else [])
    if old.get("analysis") is None or new.get("analysis") != expected_analysis:
        problems.append("analysis is not the old entries plus the one appended entry" if appended
                        else "analysis changed")
    count = len(chairs or [])
    old_parties, new_parties = old.get("parties") or [], new.get("parties") or []
    if count:
        if new_parties[count:] != old_parties:
            problems.append("the original parties are not intact after the chairs")
        if [p.get("name") for p in new_parties[:count]] != [c["name"] for c in chairs] or any(
            p.get("role") != "chair" for p in new_parties[:count]
        ):
            problems.append("the chair parties are not the chairs asked for")
        for party, chair in zip(new_parties[:count], chairs):
            if party.get("mailto") != (chair.get("mailto") or None) or list(party) != list(chair_party(chair)):
                problems.append(f"chair party {chair.get('name')} differs in address or key order")
    elif new_parties != old_parties:
        problems.append("parties changed although no chairs were confirmed")
    indices = list(range(len(new_parties)))
    old_sessions, new_sessions = old.get("sessions") or [], new.get("sessions") or []
    if len(old_sessions) != len(new_sessions):
        problems.append("sessions count changed")
    for a, b in zip(old_sessions, new_sessions):
        if {k: v for k, v in a.items() if k != "parties"} != {k: v for k, v in b.items() if k != "parties"} or list(a) != list(b):
            problems.append("a session changed beyond parties")
        if (b.get("parties") != indices) if count else (a.get("parties") != b.get("parties")):
            problems.append("sessions parties are wrong")
    old_dialog, new_dialog = old.get("dialog") or [], new.get("dialog") or []
    if len(old_dialog) != len(new_dialog):
        problems.append("dialog count changed")
    for a, b in zip(old_dialog, new_dialog):
        if {k: v for k, v in a.items() if k != "parties"} != {k: v for k, v in b.items() if k != "parties"} or list(a) != list(b):
            problems.append("a Dialog changed beyond parties")
        if (b.get("parties") != indices) if count else (a.get("parties") != b.get("parties")):
            problems.append("a Dialog's parties are wrong")
    when = api_time(new.get("updated_at"))
    if when is None or new.get("updated_at") == old.get("updated_at"):
        problems.append("updated_at was not set to the time of the change")
    return problems


def entry_member_changes(old_body: dict, new_body: dict) -> dict:
    """Which members of a record body a replacement adds, removes or changes."""
    return {
        "added": [k for k in new_body if k not in old_body],
        "removed": [k for k in old_body if k not in new_body],
        "changed": [k for k in new_body if k in old_body and new_body[k] != old_body[k]],
    }


def verify_only_body_replaced(old: dict, new: dict, index: int, entry: dict) -> list[str]:
    """Problems found if anything but one entry's body and ``updated_at`` differs; [] if only those do."""
    problems = []
    if list(old) != list(new):
        problems.append("top-level keys or their order changed")
    for key in old:
        if key not in ("updated_at", "analysis") and old[key] != new.get(key):
            problems.append(f"{key} changed")
    before, after = old.get("analysis") or [], new.get("analysis") or []
    if len(before) != len(after):
        problems.append("analysis count changed")
    else:
        for position, (a, b) in enumerate(zip(before, after)):
            if position != index:
                if a != b:
                    problems.append(f"analysis entry {position} changed")
            else:
                if list(a) != list(b) or {k: v for k, v in a.items() if k != "body"} != {k: v for k, v in b.items() if k != "body"}:
                    problems.append("the entry changed beyond its body")
                if b.get("body") != entry["body"]:
                    problems.append("the entry's body is not the planned body")
    when = api_time(new.get("updated_at"))
    if when is None or new.get("updated_at") == old.get("updated_at"):
        problems.append("updated_at was not set to the time of the change")
    return problems


def apply_file(path: Path, row: dict, now=None, update: bool = False, dry_run: bool = False,
               report: dict | None = None) -> str:
    """Record the determination in one file and, if confirmed, add the chairs.

    Returns "applied", "already recorded", "restored" or "updated"; raises
    ApplyError and leaves the file untouched on any problem. A file that already
    has a chair-determination entry is checked against the plan, never given a
    second one. "restored" is an exception that changes such a file: a later
    conversion removed confirmed chairs but carried the entry across, so the
    chairs are put back (and nothing else but ``updated_at`` changes). "updated"
    needs ``update=True``: the one entry's body is replaced in place by the planned
    body (same position, same other keys) and only that body and ``updated_at``
    change. With ``dry_run`` every check runs and nothing is written; ``report``
    receives the members an update would change.
    """
    path = Path(path)
    entry = row.get("entry")
    if not entry or row.get("action") not in ("recover", "record"):
        raise ApplyError("the plan has no entry for this file")
    problems = entry_problems(entry)
    if problems:
        raise ApplyError("planned entry is malformed: " + "; ".join(problems))
    body = json.loads(entry["body"])
    chairs = converter_dedupe(row.get("chairs") or []) if row["action"] == "recover" else []
    try:
        text = path.read_text(encoding="utf-8")
        old = json.loads(text)
    except (OSError, ValueError) as error:
        raise ApplyError(f"cannot read {path.name}: {error}") from error
    if not isinstance(old, dict):
        raise ApplyError("not a vCon object")
    existing = determinations(old)
    if len(existing) > 1:
        raise ApplyError(f"file has {len(existing)} chair-determination entries")
    write = (lambda target, content: None) if dry_run else atomic_write_text
    if existing:
        if existing[0] != entry and update:
            if text != file_text(old):
                raise ApplyError("file is not in the converter's layout (indent 2, UTF-8, one trailing newline)")
            current = existing[0]
            if list(current) != list(entry) or {k: v for k, v in current.items() if k != "body"} != {
                k: v for k, v in entry.items() if k != "body"
            }:
                raise ApplyError("the existing entry differs from the plan beyond its body")
            if not agrees_with_parties(old, body):
                raise ApplyError("the planned record and the chair parties of the file disagree")
            try:
                old_body = json.loads(current["body"])
            except (TypeError, ValueError) as error:
                raise ApplyError("the existing entry's body is not JSON") from error
            index = next(i for i, a in enumerate(old["analysis"]) if a is current)
            new = copy.deepcopy(old)
            new["analysis"][index]["body"] = entry["body"]
            new["updated_at"] = (now or datetime.now(UTC)).isoformat()
            problems = verify_only_body_replaced(old, new, index, entry)
            if problems:
                raise ApplyError("; ".join(problems))
            if report is not None:
                report["changes"] = entry_member_changes(old_body, body)
            write(path, file_text(new))
            return "updated"
        if existing[0] != entry:
            raise ApplyError("file has a chair-determination entry that differs from the plan")
        if agrees_with_parties(old, body):
            return "already recorded"
        if row["action"] != "recover" or chairs_of(old):
            raise ApplyError("the entry and the chair parties of the file disagree")
        if text != file_text(old):
            raise ApplyError("file is not in the converter's layout (indent 2, UTF-8, one trailing newline)")
        new = build_updated(old, entry, chairs, now or datetime.now(UTC), append=False)
        problems = verify_only_expected(old, new, chairs, entry, appended=False)
        if problems or not agrees_with_parties(new, body):
            raise ApplyError("; ".join(problems) or "after the change the entry and the chair parties would disagree")
        write(path, file_text(new))
        return "restored"
    if text != file_text(old):
        raise ApplyError("file is not in the converter's layout (indent 2, UTF-8, one trailing newline)")
    if not chairs and not agrees_with_parties(old, body):
        raise ApplyError("the chair parties of the file are not what the record would name")
    new = build_updated(old, entry, chairs, now or datetime.now(UTC))
    problems = verify_only_expected(old, new, chairs, entry)
    if problems:
        raise ApplyError("; ".join(problems))
    if not agrees_with_parties(new, body):
        raise ApplyError("after the change the entry and the chair parties would disagree")
    write(path, file_text(new))
    return "applied"


def apply_plan(rows: list[dict], output_root: Path, now=None, update: bool = False, dry_run: bool = False) -> dict:
    results = {"applied": [], "already recorded": [], "restored": [], "updated": [], "errors": [], "not written": [],
               "changes": {}}
    for row in rows:
        if row["action"] == "mismatch":
            results["not written"].append(row["path"])
            continue
        path = Path(output_root) / row["path"]
        info: dict = {}
        try:
            outcome = apply_file(path, row, now, update, dry_run, info)
        except ApplyError as error:
            log.error("Not applied to %s: %s", row["path"], error)
            results["errors"].append((row["path"], str(error)))
            continue
        results[outcome].append(row["path"])
        if "changes" in info:
            results["changes"][row["path"]] = info["changes"]
    return results


def run_validator(converter: Path, directory: Path) -> dict:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", COLUMNS="200")
    result = subprocess.run(
        [str(Path(converter) / ".venv" / "bin" / "python"), "scripts/validate_vcons.py", str(directory), "--all"],
        cwd=str(converter), capture_output=True, text=True, env=env,
    )
    tail = [line.strip() for line in result.stdout.splitlines() if line.strip()][-8:]
    return {"directory": str(directory), "exit_code": result.returncode, "tail": tail}


# --------------------------------------------------------------------------
# Safety checks for applying to the real output
# --------------------------------------------------------------------------

class SystemProcesses:
    """The process table, as the batch driver's own checks read it (ps)."""

    def command(self, pid: int) -> str | None:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        text = result.stdout.strip()
        return text or None

    def all(self) -> list[tuple[int, str]]:
        result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True)
        found = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                found.append((int(parts[0]), parts[1]))
        return found


def apply_safety_problems(state_dir: Path, lock_file: Path | None, requested, done: list[int], procs,
                          own_pid: int | None = None) -> list[str]:
    """Reasons it is not safe to modify the real output now; [] if safe.

    1. With ``--lock-file``: the file (or, if it is a directory, the file ``pid`` in it)
       exists and names a live process. A lock whose process is gone is stale and does not
       count; a lock with no readable pid might be a driver starting, and does count.
       A reused pid makes a stale lock look live, which errs on the side of refusing.
    2. A process whose command line contains ``ietf2vcon convert`` is running.
    3. A meeting asked for has no ``<state-dir>/<N>.done``.
    4. A ``<state-dir>/<N>.failed`` has no matching ``.done`` (a retry is pending). With
       ``requested`` only the failures among the requested meetings count, and
       those are covered by 3.

    There is deliberately no way to override 1 and 2.
    """
    problems = []
    state_dir = Path(state_dir)
    if lock_file is not None and Path(lock_file).exists():
        lock = Path(lock_file)
        try:
            text = (lock / "pid" if lock.is_dir() else lock).read_text().strip()
        except OSError:
            text = ""
        if not text.isdigit():
            problems.append(f"the lock {lock} exists without a readable pid (a driver may be starting)")
        elif procs.command(int(text)):
            problems.append(f"the process named by the lock is running (pid {text}, lock {lock})")
    own = own_pid if own_pid is not None else os.getpid()
    for pid, command in procs.all():
        if pid != own and "ietf2vcon convert" in command:
            problems.append(f"a conversion is running (pid {pid}: {command[:120]})")
    done_set = set(done)
    for number in requested or []:
        if number not in done_set:
            problems.append(f"meeting {number} is not complete (no {number}.done in {state_dir})")
    if not requested:
        done_names = {m.stem for m in state_dir.glob("*.done")}
        for marker in sorted(state_dir.glob("*.failed")):
            if marker.stem not in done_names:
                problems.append(f"meeting {marker.stem} failed and has no .done (a retry is pending)")
    return problems


# --------------------------------------------------------------------------
# Verify: does every file carry its determination, and does it agree with the file and the plan?
# --------------------------------------------------------------------------

_VERIFY_LISTS = ("without_entry", "several_entries", "bad_shape", "disagrees_with_parties",
                 "differs_from_plan", "legacy_chair_source", "unreadable")


def verify_tree(rows: list[dict], output_root: Path, meetings: list) -> dict:
    """Per meeting: every ``*.vcon.json`` in the directory has exactly one well-formed entry that agrees with
    the file's parties and with the plan. The directory is read, not only the plan, so a file the plan
    does not know of is found too."""
    result = {}
    planned_by_meeting: dict = defaultdict(dict)
    for row in rows:
        planned_by_meeting[row["meeting"]][row["file"]] = row
    for meeting in meetings:
        entry = {"files": 0, "with_entry": 0, **{k: [] for k in _VERIFY_LISTS}}
        planned = planned_by_meeting.get(meeting, {})
        directory = Path(output_root) / meeting_dir(meeting)
        on_disk = sorted(p.name for p in directory.glob("*.vcon.json"))
        for name in on_disk + sorted(n for n in planned if n not in on_disk):
            row = planned.get(name)
            entry["files"] += 1
            try:
                vcon = json.loads((directory / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entry["unreadable"].append(name)
                continue
            body = _metadata_body(vcon)
            if body is not None and any(k.startswith("chair_source") for k in body):
                entry["legacy_chair_source"].append(name)
            found = determinations(vcon)
            if not found:
                entry["without_entry"].append(name)
                continue
            if len(found) > 1:
                entry["several_entries"].append(f"{name} ({len(found)})")
                continue
            entry["with_entry"] += 1
            one = found[0]
            problems = entry_problems(one)
            if problems:
                entry["bad_shape"].append(f"{name}: {'; '.join(problems)}")
                continue
            parsed = json.loads(one["body"])
            if not agrees_with_parties(vcon, parsed):
                entry["disagrees_with_parties"].append(name)
            if row is None:
                entry["differs_from_plan"].append(f"{name}: the file is not in the plan")
            elif row["action"] == "mismatch" or row["entry"] is None:
                entry["differs_from_plan"].append(f"{name}: the plan could not determine this file")
            elif one != row["entry"]:
                entry["differs_from_plan"].append(f"{name}: entry differs from the plan")
            elif row["action"] == "recover" and chair_dicts(vcon) != [
                {"name": c["name"], "mailto": c.get("mailto") or None} for c in converter_dedupe(row["chairs"])
            ]:
                entry["differs_from_plan"].append(f"{name}: chair parties differ from the plan")
        result[meeting] = entry
    return result


def verify_text(result: dict, not_checked: list[int]) -> str:
    columns = ["without_entry", "several_entries", "bad_shape", "disagrees_with_parties", "differs_from_plan",
               "legacy_chair_source", "unreadable"]
    lines = ["meeting  files  with_entry  " + "  ".join(columns)]
    for meeting, e in result.items():
        lines.append(f"{meeting:>7}  {e['files']:>5}  {e['with_entry']:>10}  "
                     + "  ".join(str(len(e[c])).rjust(len(c)) for c in columns))
    for meeting, e in result.items():
        for key in columns:
            for item in e[key][:20]:
                lines.append(f"  meeting {meeting}: {key}: {item}")
            if len(e[key]) > 20:
                lines.append(f"  meeting {meeting}: {key}: ... and {len(e[key]) - 20} more")
    for meeting in not_checked:
        lines.append(f"  meeting {meeting}: not complete (no .done), NOT checked")
    return "\n".join(lines) + "\n"


def verify_clean(result: dict, not_checked: list[int]) -> bool:
    return not not_checked and all(not any(e[k] for k in _VERIFY_LISTS) for e in result.values())


def _metadata_body(vcon: dict):
    """The parsed meeting_metadata body (a JSON string in the file), or None."""
    items = [a for a in vcon.get("attachments") or [] if isinstance(a, dict) and a.get("purpose") == "meeting_metadata"]
    if len(items) != 1:
        return None
    try:
        parsed = json.loads(items[0].get("body"))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

HELP_DESCRIPTION = """\
Records, in every file of a completed meeting, how its chairs were determined (one
chair-determination entry appended to analysis), and adds chairs to a session only when the
write order of the Datatracker history, the group's proceedings page and the session's own
minutes all name exactly the same people. It also recomputes the converter's chair rule for
ordinary sessions and requires it to match the file and the converter's log.

Order of operations: convert the meeting; keep the converter's log and mark the meeting done;
plan (the default, writes only reports); --apply; --verify. Run --apply and --verify again
after any later conversion of a meeting. Plan and --verify never modify the dataset.
"""

HELP_EPILOG = """\
inputs the tool cannot guess (all required, in every mode):
  --previous-rev   the commit of the previous release, read through git (see below)
  --proceedings    the proceedings mirror root, the directory that holds <meeting>/<group>.html
                   and <meeting>/minutes/ and <meeting>/agenda/ (ietf2vcon sync writes it)
  --log-dir        the converter's logs, one convert-<meeting>.log per meeting
  --state-dir      the completed-meeting markers, one <meeting>.done per meeting

The logs and markers come from the batch driver that runs the conversions, or from your own
run: `NO_COLOR=1 ietf2vcon convert-all --meeting 127 ... -v > logs/convert-127.log 2>&1`, then
`touch state/127.done` once the meeting converted cleanly. Interim meetings use their names
(convert-interim-2026-vcon-01.log, interim-2026-vcon-01.done).
"""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=HELP_DESCRIPTION, epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="record the determination in every file under --output-root and add confirmed chairs "
                             "(default: plan only, writes nothing there)")
    parser.add_argument("--update-records", action="store_true",
                        help="with --apply: also replace the body of an existing chair-determination entry that "
                             "differs from the plan (only that body and updated_at change)")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply: run every check and write nothing; lists what would change")
    parser.add_argument("--verify", action="store_true",
                        help="check every file carries exactly one well-formed determination that agrees with its "
                             "parties and the plan; exits non-zero on any difference; writes nothing there")
    parser.add_argument("--meetings", nargs="*", type=parse_meeting,
                        help="meetings to process: numbers, or interim names such as interim-2026-vcon-01 "
                             "(default: every meeting with a .done marker)")
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT,
                        help="directory holding the ietf<N>/ and interim-*/ meeting directories "
                             "(default: the repository root, found relative to this script)")
    parser.add_argument("--output-root", type=Path,
                        help="directory tree holding the meeting directories; read in plan and verify mode "
                             "(default: --dataset-root), modified in place in --apply mode (required there)")
    parser.add_argument("--git-dir", type=Path,
                        help="git repository to read the previous release from (default: --dataset-root). "
                             "Needed only when the files being processed are not in a git repository, for "
                             "example a scratch copy used for testing")
    parser.add_argument("--previous-rev",
                        help="commit of the previous release, the release whose chairs are recorded as "
                             "previous-release candidates. The tool checks that dataset.json at this revision "
                             "says --previous-version, and reads each file's earlier chairs from it")
    parser.add_argument("--previous-version", default=DEFAULT_PREVIOUS_VERSION,
                        help="version that dataset.json must say at --previous-rev, and that previous-release "
                             "candidates record (default %(default)s)")
    parser.add_argument("--allow-production", action="store_true",
                        help="permit --apply on the dataset itself or on any git working tree (it still refuses "
                             "while a conversion is running, while the --lock-file process is alive, or while a "
                             "requested meeting is not done)")
    parser.add_argument("--log-dir", type=Path,
                        help="directory with the converter's logs, one convert-<meeting>.log per meeting "
                             "(the batch driver or `ietf2vcon convert-all -v` output)")
    parser.add_argument("--state-dir", type=Path,
                        help="directory with one <meeting>.done marker per completed meeting, and "
                             "<meeting>.failed for a meeting whose conversion failed (the batch driver writes "
                             "them; otherwise create them by hand)")
    parser.add_argument("--lock-file", type=Path,
                        help="optional: a batch driver's lock (a file holding its pid, or a directory with a file "
                             "pid). Applying to the real output is refused while that process is alive")
    parser.add_argument("--proceedings", type=Path,
                        help="root of the proceedings mirror: the directory holding <meeting>/<group>.html, "
                             "<meeting>/minutes/ and <meeting>/agenda/")
    parser.add_argument("--cache-dir", type=Path, default=user_cache_dir() / "datatracker",
                        help="on-disk cache of Datatracker responses (default: %(default)s, never inside the "
                             "repository)")
    parser.add_argument("--seed-cache", type=Path, action="append",
                        help="read-only cache directory to reuse first (repeatable; default: none)")
    parser.add_argument("--report-dir", type=Path, default=user_cache_dir() / "reports",
                        help="where the plan, apply and verify reports and logs are written "
                             "(default: %(default)s)")
    parser.add_argument("--converter", type=Path,
                        help="checkout of the converter (ietf2vcon) with its virtual environment in .venv, whose "
                             "scripts/validate_vcons.py checks each touched meeting after --apply. Required "
                             "with --apply unless --no-validate or --dry-run")
    parser.add_argument("--max-requests", type=int, default=200,
                        help="cap on live Datatracker requests (default %(default)s; 0 forbids any)")
    parser.add_argument("--contact", help="optional contact text added to the User-Agent of live requests")
    parser.add_argument("--no-validate", action="store_true", help="skip the validator after --apply")
    parser.add_argument("--no-refined-names", action="store_true",
                        help="turn off the name-matching refinements (titles, word order, alternate names), "
                             "to measure their effect on a plan")
    return parser.parse_args(argv)


class RunStop(Exception):
    def __init__(self, code: int):
        self.code = code


def in_git_work_tree(path: Path) -> bool:
    """Is the path inside a directory that has a .git entry?"""
    current = Path(path).resolve()
    return any((candidate / ".git").exists() for candidate in (current, *current.parents))


def is_production(output_root: Path, dataset_root: Path) -> bool:
    """The output is the dataset itself or a git working tree: applying there needs --allow-production."""
    return Path(output_root).resolve() == Path(dataset_root).resolve() or in_git_work_tree(output_root)


def compute_plan(args, meetings: list[int], output_root: Path, git_dir: Path):
    """Plan every file of the meetings, from the cached tables, the mirror and the convert logs."""
    api = ApiCache(args.cache_dir, args.seed_cache or [], max_requests=args.max_requests,
                   user_agent_text=user_agent(args.contact))
    reader = None
    try:
        data = ChairData.load(api)
        calibration = Calibration(calibration_points(data.snapshots, data.groups))
        log.info("Calibration: %d points, %d dropped as out of order", len(calibration.points), len(calibration.dropped))
        try:
            dates = load_meeting_dates(api)
        except (RateLimited, BudgetExceeded):
            raise
        except DatatrackerError as error:
            log.warning("Meeting dates could not be read (%s); sessions with no start use the converter's log", error)
            dates = {}
        problem = release_problem(git_dir, args.previous_rev, args.previous_version)
        if problem:
            log.error("Previous release: %s", problem)
            raise RunStop(2)
        reader = PublishedReader(git_dir, rev=args.previous_rev)
        ctx = Context(data, calibration, PersonDirectory(api), args.proceedings, output_root, reader.chairs, dates,
                      args.previous_version)
        rows = []
        for number in meetings:
            logfile = args.log_dir / f"convert-{number}.log"
            blocks = parse_log_blocks(logfile) if logfile.is_file() else {}
            if not blocks:
                log.warning("No usable convert log for meeting %s", number)
            rows += plan_meeting(ctx, number, blocks)
            log.info("Meeting %s planned: %d files", number, sum(1 for r in rows if r["meeting"] == number))
    except RateLimited as error:
        log.error("Stopped: %s", error)
        raise RunStop(3)
    except BudgetExceeded as error:
        log.error("Stopped: %s", error)
        raise RunStop(4)
    except DatatrackerError as error:
        log.error("Stopped: %s", error)
        raise RunStop(5)
    finally:
        if reader is not None:
            reader.close()
    recovered = sum(1 for r in rows if r["action"] == "recover")
    stats = {
        "requests_made": api.stats["requests"],
        "served_from_cache": api.stats["from_cache"],
        "calibration_points": len(calibration.points),
        "calibration_points_raw": calibration.raw_count,
        "files": len(rows),
        "recorded": sum(1 for r in rows if r["action"] == "record"),
        "recovered": recovered,
        "mismatches": sum(1 for r in rows if r["action"] == "mismatch"),
    }
    return rows, stats


def main(argv=None, procs=None) -> int:
    args = parse_args(argv)
    OPTIONS["refined_names"] = not args.no_refined_names
    if args.apply and args.verify:
        sys.stderr.write("--apply and --verify cannot be combined\n")
        return 2
    if (args.update_records or args.dry_run) and not args.apply:
        sys.stderr.write("--update-records and --dry-run need --apply\n")
        return 2
    missing = [flag for flag, value in (
        ("--previous-rev", args.previous_rev), ("--proceedings", args.proceedings),
        ("--log-dir", args.log_dir), ("--state-dir", args.state_dir)) if value is None]
    if missing:
        sys.stderr.write(f"missing required option(s): {', '.join(missing)} (see --help)\n")
        return 2
    if args.apply and not (args.no_validate or args.dry_run):
        if args.converter is None:
            sys.stderr.write("--apply needs --converter (to validate what it wrote) unless --no-validate is given\n")
            return 2
        if not (args.converter / "scripts" / "validate_vcons.py").is_file() or not (
                args.converter / ".venv" / "bin" / "python").exists():
            sys.stderr.write(f"--converter {args.converter} has no scripts/validate_vcons.py or .venv/bin/python\n")
            return 2
    mode = "apply" if args.apply else ("verify" if args.verify else "plan")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(args.report_dir / f"{mode}.log", mode="w")],
    )
    procs = procs or SystemProcesses()
    dataset_root = args.dataset_root
    git_dir = args.git_dir or dataset_root
    output_root = args.output_root or dataset_root
    production = is_production(output_root, dataset_root)
    requested = list(args.meetings) if args.meetings else None

    def refuse_if_unsafe() -> bool:
        problems = apply_safety_problems(args.state_dir, args.lock_file, requested,
                                         completed_meetings(args.state_dir), procs)
        for problem in problems:
            log.error("Refusing to apply to the real output: %s", problem)
        return bool(problems)

    if args.apply:
        if args.output_root is None:
            log.error("--apply needs --output-root")
            return 2
        if production and not args.dry_run:
            if not args.allow_production:
                log.error("--output-root is the dataset itself or a git working tree; pass --allow-production "
                          "to apply there")
                return 2
            if refuse_if_unsafe():
                return 2

    done = completed_meetings(args.state_dir)
    meetings, not_checked = [], []
    for number in (requested if requested else done):
        if number not in done:
            log.warning("Meeting %s is not complete (no .done marker): not read", number)
            not_checked.append(number)
            continue
        meetings.append(number)

    try:
        rows, stats = compute_plan(args, meetings, output_root, git_dir)
    except RunStop as stop:
        return stop.code
    stats["mode"] = mode
    summary = summarize(rows)

    if args.verify:
        result = verify_tree(rows, output_root, meetings)
        text = verify_text(result, not_checked)
        sys.stdout.write(text)
        write_reports(rows, summary, stats, args.report_dir, "verify-plan")
        atomic_write_text(args.report_dir / "verify.txt", text)
        atomic_write_text(args.report_dir / "verify.json",
                          json.dumps({"result": result, "not_checked": not_checked}, indent=1, ensure_ascii=False) + "\n")
        clean = verify_clean(result, not_checked)
        print("verify: clean" if clean else "verify: DIFFERENCES FOUND")
        return 0 if clean else 1

    write_reports(rows, summary, stats, args.report_dir, "apply-plan" if args.apply else "plan")
    sys.stdout.write(summary_text(summary, stats))
    if not args.apply:
        return 0
    if production and not args.dry_run and refuse_if_unsafe():
        return 2
    results = apply_plan(rows, output_root, update=args.update_records, dry_run=args.dry_run)
    print(("DRY RUN, nothing written. " if args.dry_run else "")
          + f"applied {len(results['applied'])}, already recorded {len(results['already recorded'])}, "
          f"restored {len(results['restored'])}, updated {len(results['updated'])}, errors {len(results['errors'])}, "
          f"not written (plan mismatch) {len(results['not written'])}")
    changed = results["applied"] + results["restored"] + results["updated"]
    if results["updated"]:
        members = Counter(
            "+".join([f"added {','.join(c['added'])}" if c["added"] else "", f"removed {','.join(c['removed'])}" if c["removed"] else "",
                      f"changed {','.join(c['changed'])}" if c["changed"] else ""]).strip("+").replace("++", "+")
            for c in results["changes"].values())
        print("record members the update changes:", dict(members))
        lines = [f"{path}\t{json.dumps(results['changes'][path])}" for path in results["updated"]]
        atomic_write_text(args.report_dir / ("dry-run-updates.txt" if args.dry_run else "updates.txt"),
                          "\n".join(lines) + "\n")
    touched = sorted({str(Path(p).parent) for p in (changed or results["already recorded"])})
    validations = []
    if not args.no_validate and not args.dry_run:
        for directory in touched:
            validations.append(run_validator(args.converter, Path(output_root) / directory))
        for item in validations:
            print(item["directory"], "exit", item["exit_code"], "|", " / ".join(item["tail"][-5:]))
    atomic_write_text(
        args.report_dir / "apply-result.json",
        json.dumps({"results": results, "validations": validations}, indent=1, ensure_ascii=False) + "\n",
    )
    return 1 if results["errors"] or any(v["exit_code"] for v in validations) else 0


if __name__ == "__main__":
    sys.exit(main())
