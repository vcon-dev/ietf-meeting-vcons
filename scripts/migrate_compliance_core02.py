#!/usr/bin/env python3
"""Bring the IETF meeting vCons into compliance with draft-ietf-vcon-vcon-core-02.

Fixes the issues found by validating every *.vcon.json against the IETF core
JSON schema + speckit rules:

  1. Remove empty `redacted: {}`  (schema requires uuid+type when present;
     empty means "not redacted", so the key is dropped).
  2. Remove reserved `group` key  (reserved for a future extension; do not use).
  3. Dialog `type: "video"` -> "recording"  (core enum has no 'video';
     mediatype video/mp4 and the url are preserved).
  4. De-duplicate dialog `meta`/`metadata`  (identical objects; keep `meta`).
  5. Attachments: add required `party` (0), `dialog` (0) and `start`
     (the vCon's created_at) where missing  (vCon-level attachment convention).
  6. Attachment/analysis `body`: stringify JSON objects/arrays and set
     `encoding: "json"`  (body MUST be a string per the spec).
  7. Add `vcon: "0.4.0"` when absent  (syntax parameter).
  8. Declare used extension field names (`role`, `meta`) in top-level
     `extensions`.

Usage:
    python scripts/migrate_compliance_core02.py [--dry-run] [--meeting 124] [-v]
"""
import argparse, glob, json, sys
from collections import Counter
from pathlib import Path


def migrate(v: dict) -> list[str]:
    changes = []

    # 7. vcon syntax param
    if v.get("vcon") != "0.4.0":
        v["vcon"] = "0.4.0"
        changes.append("set vcon='0.4.0'")

    # 1. empty redacted
    if isinstance(v.get("redacted"), dict) and not v["redacted"]:
        del v["redacted"]
        changes.append("removed empty redacted{}")

    # 2. reserved group
    if "group" in v:
        del v["group"]
        changes.append("removed reserved group")

    used_role = used_meta = False

    # 3/4. dialog fixes
    for d in v.get("dialog") or []:
        if d.get("type") == "video":
            d["type"] = "recording"
            changes.append("dialog type video->recording")
        if "metadata" in d:
            if "meta" not in d:
                d["meta"] = d.pop("metadata")
            else:
                d.pop("metadata")
            changes.append("dialog dropped duplicate metadata")
        if "meta" in d:
            used_meta = True

    for p in v.get("parties") or []:
        if isinstance(p, dict) and "role" in p:
            used_role = True

    created = v.get("created_at")

    # 5/6. attachment fixes
    for a in v.get("attachments") or []:
        if not isinstance(a, dict):
            continue
        if "party" not in a:
            a["party"] = 0; changes.append("attachment +party=0")
        if "dialog" not in a:
            a["dialog"] = 0; changes.append("attachment +dialog=0")
        if "start" not in a and created:
            a["start"] = created; changes.append("attachment +start")
        _stringify_body(a, changes, "attachment")

    # 6. analysis bodies
    for an in v.get("analysis") or []:
        if isinstance(an, dict):
            _stringify_body(an, changes, "analysis")

    # 8. declare extensions
    exts = v.get("extensions")
    if exts is None:
        exts = []
    for name, used in (("role", used_role), ("meta", used_meta)):
        if used and name not in exts:
            exts.append(name); changes.append(f"declared extension '{name}'")
    if exts:
        v["extensions"] = exts

    return changes


def _stringify_body(obj: dict, changes: list, label: str):
    body = obj.get("body")
    if body is not None and not isinstance(body, str):
        obj["body"] = json.dumps(body, ensure_ascii=False)
        obj["encoding"] = "json"
        changes.append(f"{label} body stringified (encoding=json)")
    elif "body" in obj and "encoding" not in obj:
        obj["encoding"] = "none"
        changes.append(f"{label} +encoding=none")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    pat = base / (f"ietf{args.meeting}" if args.meeting else "ietf*") / "*.vcon.json"
    files = sorted(glob.glob(str(pat)))
    if not files:
        print(f"No files matching {pat}"); sys.exit(1)

    modified = 0
    summary = Counter()
    for fp in files:
        data = json.loads(Path(fp).read_text(encoding="utf-8"))
        changes = migrate(data)
        if changes:
            modified += 1
            for c in changes:
                summary[c.split(" (")[0]] += 1
            if args.verbose:
                print(f"{'WOULD FIX' if args.dry_run else 'FIXED'} {Path(fp).name}: {changes}")
            if not args.dry_run:
                Path(fp).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")

    print(f"\n{'Would modify' if args.dry_run else 'Modified'} {modified}/{len(files)} files")
    print("Changes by type:")
    for k, c in summary.most_common():
        print(f"  {c:6d}  {k}")


if __name__ == "__main__":
    main()
