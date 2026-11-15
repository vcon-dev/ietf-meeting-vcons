#!/usr/bin/env python3
"""Independent check of a dataset tree against the commit it was produced from.

Compares every ``*/*.vcon.json`` file of the tree with the same file at a git revision
(the converter's output before chair_determination.py ran) and reports any file that
differs in a way the chair determination pass does not allow. It shares no code with
chair_determination.py, so it can confirm that tool's guarantees: the file keeps the
converter's layout and key order, exactly one ``chair-determination`` analysis entry is
appended in the fixed shape, ``updated_at`` moves, and nothing else changes except, for
sessions whose chairs were confirmed from three sources (basis ``minutes-confirmed``),
``parties`` (chairs first, the old parties intact after them) with the index lists of the
session and its dialogs.

usage: check_chair_determination.py --rev REV [--dataset-root DIR] [--git-dir DIR]

Reads only. Exits 1 if any file has another difference. Standard library only; needs git.
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--rev", required=True,
                help="git revision holding the files as the converter wrote them, before the chair pass")
ap.add_argument("--dataset-root", default=REPO_ROOT,
                help="tree to check: the directory holding the meeting directories (default: the repository root)")
ap.add_argument("--git-dir", help="git repository holding --rev (default: --dataset-root)")
args = ap.parse_args()

OUT = os.path.abspath(args.dataset_root)
REV = args.rev
env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
paths = sorted(os.path.relpath(p, OUT) for p in glob.glob(OUT + "/*/*.vcon.json"))
batch = subprocess.Popen(["git", "-C", args.git_dir or OUT, "cat-file", "--batch"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)


def committed(rel):
    batch.stdin.write(f"{REV}:{rel}\n".encode())
    batch.stdin.flush()
    head = batch.stdout.readline().split()
    if len(head) != 3:
        return None
    data = batch.stdout.read(int(head[2]))
    batch.stdout.read(1)
    return data.decode("utf-8")


cat = collections.Counter()
bad = []
for rel in paths:
    base_text = committed(rel)
    if base_text is None:
        bad.append((rel, "not in commit"))
        continue
    new_text = open(os.path.join(OUT, rel), encoding="utf-8").read()
    old, new = json.loads(base_text), json.loads(new_text)
    problems = []
    if new_text != json.dumps(new, indent=2, ensure_ascii=False) + "\n":
        problems.append("layout")
    if list(old) != list(new):
        problems.append("top-level keys")
    entry = None
    if len(new["analysis"]) == len(old["analysis"]) + 1 and new["analysis"][:-1] == old["analysis"]:
        entry = new["analysis"][-1]
    else:
        problems.append("analysis is not old + one entry")
    body = None
    if entry is not None:
        if (entry.get("type"), entry.get("sessions"), entry.get("product"), entry.get("schema")) != (
                "report", 0, "chair-determination", "chair-determination-v1"):
            problems.append("entry identity")
        if list(entry) != ["type", "sessions", "vendor", "body", "encoding", "mediatype", "product", "schema"]:
            problems.append("entry keys")
        try:
            body = json.loads(entry["body"])
        except Exception:
            problems.append("entry body")
    if new["updated_at"] == old["updated_at"]:
        problems.append("updated_at unchanged")
    confirmed = body is not None and body.get("basis") == "minutes-confirmed"
    for k in old:
        if k in ("updated_at", "analysis"):
            continue
        if k in ("parties", "sessions", "dialog") and confirmed:
            continue
        if old[k] != new[k]:
            problems.append(f"{k} changed")
    if confirmed:
        chairs = [p for p in new["parties"] if p.get("role") == "chair"]
        n = len(chairs)
        if n == 0 or new["parties"][:n] != chairs:
            problems.append("chairs not first")
        if new["parties"][n:] != old["parties"]:
            problems.append("old parties not intact")
        if [(c["name"], c.get("mailto")) for c in chairs] != [(c["name"], c.get("mailto")) for c in body["chairs"]]:
            problems.append("chairs differ from record")
        idx = list(range(len(new["parties"])))
        if new["sessions"][0].get("parties") != idx:
            problems.append("sessions[0].parties")
        if {k: v for k, v in new["sessions"][0].items() if k != "parties"} != {
                k: v for k, v in old["sessions"][0].items() if k != "parties"} or len(new["sessions"]) != len(old["sessions"]):
            problems.append("session otherwise changed")
        if len(new["dialog"]) != len(old["dialog"]):
            problems.append("dialog count")
        for a, b in zip(old["dialog"], new["dialog"]):
            if {k: v for k, v in a.items() if k != "parties"} != {k: v for k, v in b.items() if k != "parties"}:
                problems.append("dialog changed")
            if b.get("parties") != idx:
                problems.append("dialog parties")
        if any(a.get("party") != 0 for a in new["attachments"]):
            problems.append("attachment party")
    else:
        if body is not None and (body["outcome"] == "named") != bool([p for p in new["parties"] if p.get("role") == "chair"]):
            problems.append("record vs parties")
    if problems:
        bad.append((rel, problems))
    else:
        cat["confirmed (parties + index lists + updated_at + entry)" if confirmed else "updated_at + entry only"] += 1
batch.stdin.close()
batch.wait()
print("files compared", len(paths))
print(dict(cat))
print("files with any other difference:", len(bad))
for b in bad[:20]:
    print(b)
sys.exit(1 if bad else 0)
