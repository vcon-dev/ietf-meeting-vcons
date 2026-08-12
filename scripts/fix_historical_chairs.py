#!/usr/bin/env python3
"""Replace chair parties with the chairs who actually served at the time.

`ietf2vcon` reads working group roles from the Datatracker's *current* group
record, so every vCon lists today's chairs regardless of when the session was
held. The TLS sessions are a clear example: IETF 95 (2016) through IETF 126
(2026) all listed the same three chairs, though one of them did not become a
chair until 2023.

The Datatracker keeps the history needed to fix this. `grouphistory` holds
timestamped snapshots of each group, and `rolehistory` holds the roles attached
to a given snapshot. Resolving the chairs for a session is therefore:

  1. take the newest grouphistory snapshot at or before the session date
  2. read that snapshot's rolehistory entries with rolename `chair`

The Datatracker's role history begins on 2011-12-09 for every group, which is
when the feature was switched on. Nothing before IETF 83 (March 2012) can be
resolved, so those sessions currently assert today's chairs, which for a 2006
session is close to certainly wrong. `--drop-unverifiable` removes the chair
parties there instead, leaving the attendees party: the vCon then asserts
nothing rather than something false.

Usage:
    python scripts/fix_historical_chairs.py --meetings 95-126 [--dry-run] [-v]
    python scripts/fix_historical_chairs.py --meetings 66-82 --drop-unverifiable
"""
import argparse
import datetime
import glob
import json
import sys
from pathlib import Path

import httpx

BASE = "https://datatracker.ietf.org/api/v1"
REPO = Path(__file__).resolve().parent.parent

client = httpx.Client(timeout=60, follow_redirects=True)

_snapshots: dict[str, list[tuple[datetime.datetime, str]]] = {}
_roles: dict[str, list[dict]] = {}
_people: dict[str, str] = {}


def _parse(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def snapshots(acronym: str) -> list[tuple[datetime.datetime, str]]:
    """Grouphistory snapshot ids for a group, newest first."""
    if acronym not in _snapshots:
        out = []
        try:
            r = client.get(
                f"{BASE}/group/grouphistory/",
                params={"acronym": acronym, "limit": 200, "format": "json"},
            ).json()
            for o in r.get("objects", []):
                out.append((_parse(o["time"]), o["resource_uri"].rstrip("/").split("/")[-1]))
        except Exception as e:
            print(f"  grouphistory lookup failed for {acronym}: {e}", file=sys.stderr)
        _snapshots[acronym] = sorted(out, reverse=True)
    return _snapshots[acronym]


def chairs_at(acronym: str, when: datetime.datetime) -> list[dict] | None:
    """Chair parties for a group as of `when`, or None if history is unavailable."""
    snap = next((sid for t, sid in snapshots(acronym) if t <= when), None)
    if snap is None:
        return None

    if snap not in _roles:
        try:
            r = client.get(
                f"{BASE}/group/rolehistory/",
                params={"group": snap, "limit": 100, "format": "json"},
            ).json()
            _roles[snap] = r.get("objects", [])
        except Exception as e:
            print(f"  rolehistory lookup failed for {acronym}@{snap}: {e}", file=sys.stderr)
            _roles[snap] = []

    parties = []
    for role in _roles[snap]:
        if not role.get("name", "").endswith("/chair/"):
            continue
        # The address is embedded in the email resource URI.
        mailto = role["email"].rstrip("/").split("/")[-1]
        person_uri = role["person"]
        if person_uri not in _people:
            try:
                p = client.get(
                    "https://datatracker.ietf.org" + person_uri, params={"format": "json"}
                ).json()
                _people[person_uri] = p.get("name", "")
            except Exception:
                _people[person_uri] = ""
        name = _people[person_uri]
        if name:
            parties.append({"name": name, "mailto": mailto, "role": "chair"})

    return parties


def session_date(vcon: dict) -> datetime.datetime | None:
    """Session start, from the meeting_metadata attachment or the dialog."""
    for a in vcon.get("attachments") or []:
        if a.get("purpose") == "meeting_metadata":
            body = a.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    continue
            if isinstance(body, dict) and body.get("start_time"):
                try:
                    return _parse(body["start_time"])
                except ValueError:
                    pass
    for d in vcon.get("dialog") or []:
        if d.get("start"):
            try:
                return _parse(d["start"])
            except ValueError:
                pass
    return None


def parse_meetings(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings", required=True, help="e.g. 95-126")
    ap.add_argument(
        "--drop-unverifiable",
        action="store_true",
        help="Remove chair parties where no role history exists, instead of "
        "leaving today's chairs asserted on a historical session",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    changed = unchanged = skipped = dropped = 0
    examples: list[str] = []

    for m in parse_meetings(args.meetings):
        d = REPO / f"ietf{m}"
        if not d.is_dir():
            continue
        for fp in sorted(d.glob("*.vcon.json")):
            vcon = json.loads(fp.read_text(encoding="utf-8"))
            acronym = fp.name.split("_")[1] if "_" in fp.name else None
            when = session_date(vcon)
            if not acronym or not when:
                skipped += 1
                continue

            historical = chairs_at(acronym, when)
            if historical is None:
                if not args.drop_unverifiable:
                    skipped += 1
                    continue
                # No role history for this date. Asserting today's chairs on a
                # historical session is a fabrication, so say nothing instead.
                remaining = [p for p in vcon.get("parties", []) if p.get("role") != "chair"]
                if len(remaining) == len(vcon.get("parties", [])):
                    unchanged += 1
                    continue
                if args.verbose or len(examples) < 5:
                    removed = [
                        p["name"] for p in vcon.get("parties", []) if p.get("role") == "chair"
                    ]
                    examples.append(f"{fp.parent.name}/{fp.name}: dropped {sorted(removed)}")
                vcon["parties"] = remaining
                dropped += 1
                if not args.dry_run:
                    fp.write_text(
                        json.dumps(vcon, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                continue

            current = [p for p in vcon.get("parties", []) if p.get("role") == "chair"]
            others = [p for p in vcon.get("parties", []) if p.get("role") != "chair"]

            if {p["name"] for p in current} == {p["name"] for p in historical}:
                unchanged += 1
                continue

            if args.verbose or len(examples) < 5:
                examples.append(
                    f"{fp.parent.name}/{fp.name}: "
                    f"{sorted(p['name'] for p in current)} -> "
                    f"{sorted(p['name'] for p in historical)}"
                )

            vcon["parties"] = historical + others
            changed += 1
            if not args.dry_run:
                fp.write_text(
                    json.dumps(vcon, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )

    for e in examples:
        print("  " + e)
    verb = "Would correct" if args.dry_run else "Corrected"
    line = f"\n{verb} {changed} files; {unchanged} already correct"
    if args.drop_unverifiable:
        line += f"; {dropped} had unverifiable chairs dropped"
    else:
        line += f"; {skipped} skipped (no history)"
    print(line)


if __name__ == "__main__":
    main()
