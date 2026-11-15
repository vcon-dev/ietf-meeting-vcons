#!/usr/bin/env python3
"""Independent check of reference_types.py: compare a tree with a copy from before the run.

usage: check_reference_types.py ORIGINAL_ROOT NEW_ROOT

Both arguments are directories holding the meeting directories (for the original, for
example the result of ``git archive <commit> | tar -x -C <dir>``). Every file that differs
is parsed, attachment bodies included, and every differing JSON path must be one of:

* ``/updated_at``
* ``/attachments/<i>/body/mimetype``       (an unhashed reference)
* ``/attachments/<i>/body/recording_note`` (the meeting_metadata body)

The check also fails if a differing file has an unchanged ``updated_at``, if an
attachment with a ``url`` (a hashed reference) or an ingress_info or lawful_basis
attachment differs, or if a changed file is not in the converter's layout. Files that did
not change must be byte identical by construction (the tool does not rewrite them); this
script reports how many differ and how many are the same. It shares no code with
reference_types.py. Reads only. Exits 1 on any violation. Standard library only.
"""
import collections
import json
import os
import re
import sys

sys.dont_write_bytecode = True

if len(sys.argv) != 3 or sys.argv[1].startswith("-"):
    sys.exit(__doc__)
orig, new = sys.argv[1], sys.argv[2]


def files(root):
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p) and (d.startswith("ietf") or d.startswith("interim-")):
            for f in sorted(os.listdir(p)):
                if f.endswith(".vcon.json"):
                    yield f"{d}/{f}"


def expand(o):
    o = json.loads(json.dumps(o))
    for a in o.get("attachments", []):
        if a.get("encoding") == "json" and isinstance(a.get("body"), str):
            a["body"] = json.loads(a["body"])
    return o


def diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                yield (path + "/" + k, "added", None, b[k])
            elif k not in b:
                yield (path + "/" + k, "removed", a[k], None)
            else:
                yield from diff(a[k], b[k], path + "/" + k)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield (path, "length", len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            yield from diff(x, y, f"{path}/{i}")
    elif a != b:
        yield (path, "changed", a, b)


ok_re = re.compile(r"^/attachments/\d+/body/(mimetype|recording_note)$")
orig_files, new_files = set(files(orig)), set(files(new))
bad = len(orig_files ^ new_files)
if bad:
    print("file-set difference:", sorted(orig_files ^ new_files)[:10])
changed, same = [], 0
for f in sorted(orig_files & new_files):
    if open(os.path.join(orig, f), "rb").read() != open(os.path.join(new, f), "rb").read():
        changed.append(f)
    else:
        same += 1
print("changed files:", len(changed), "byte-identical files:", same)
kinds = collections.Counter()
for f in changed:
    a = json.load(open(os.path.join(orig, f), encoding="utf-8"))
    b = json.load(open(os.path.join(new, f), encoding="utf-8"))
    d = list(diff(expand(a), expand(b)))
    for path, kind, x, y in d:
        if path == "/updated_at":
            kinds["updated_at"] += 1
        elif ok_re.match(path):
            kinds[path.rsplit("/", 1)[1] + ":" + kind] += 1
        else:
            bad += 1
            print("UNEXPECTED", f, path, kind)
    if not any(p == "/updated_at" for p, *_ in d):
        bad += 1
        print("NO updated_at change", f)
    for i, (x, y) in enumerate(zip(a["attachments"], b["attachments"])):
        if x["purpose"] in ("ingress_info", "lawful_basis") and x != y:
            bad += 1
            print("PROTECTED CHANGED", f, i)
        if "url" in x and x != y:
            bad += 1
            print("HASHED CHANGED", f, i)
    t = open(os.path.join(new, f), encoding="utf-8").read()
    if t != json.dumps(json.loads(t), indent=2, ensure_ascii=False) + "\n":
        bad += 1
        print("FORMAT", f)
print("checked", len(changed), "files; member changes:", dict(kinds), "; violations:", bad)
sys.exit(1 if bad else 0)
