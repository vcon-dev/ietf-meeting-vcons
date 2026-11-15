#!/usr/bin/env python3
"""Reference types and recording notes for the IETF meeting vCon dataset (syntax 0.5.0).

What it does
------------
Correction 1, reference types. An unhashed material reference is an attachment whose
inline JSON body is ``{"url", "mimetype", "filename", "title"}`` (a meeting document that
was not fetched), with no ``content_hash``. The converter labels most of them
``application/pdf`` as a default guess. This tool corrects the ``mimetype`` member of the
references that claim ``application/pdf`` (and of any reference whose file name says
another type), in this order for each one:

1. a ``filename`` with a known extension sets the type from a fixed table;
2. otherwise the previous release's value for the same url in the same file is used
   (``--previous-rev``); with ``--previous-pdf ignore`` (the default) a previous
   ``application/pdf`` counts as no information, because it was the same default guess;
3. otherwise a single unambiguous file in the proceedings mirror
   (``<mirror>/<meeting>/``, name beginning with the document name taken from the url)
   gives its extension (needs ``--proceedings``);
4. otherwise the ``mimetype`` member is removed: an unknown type is stated as unknown.

Correction 2, recording note. A ``recording_note`` member is added to the
``meeting_metadata`` body of files whose recording may not belong to the session named:
sessions that did not meet but carry a recording (group A), sessions with no time in the
official schedule that carry one (B), and files whose audio file name indicates another
session of the group (C, a fixed list for the 3.0.0 release, replaceable with
``--group-c-file``).

Nothing else changes. Attachments with a ``url``, and attachments whose purpose is
``meeting_metadata`` (except the note), ``ingress_info`` or ``lawful_basis``, are never
touched. A file is rewritten only if a member changes; before writing, the new file is
compared with the file as read and skipped, with an error, if anything other than the
``mimetype`` member of a reference, the ``recording_note`` member, or ``updated_at``
differs. Writes are atomic. A second run changes nothing.

Order of operations
-------------------
Run it after the converter and after chair_determination.py (see README-tools.md):
plan first (the default, reads only), then ``--apply --output-root <tree>``, then check
with check_reference_types.py against a copy of the tree from before the run.

Requires Python 3.11 or later and the standard library only (tested with 3.14). The tool
reads the previous release through ``git cat-file`` and does not import the converter.
"""

import argparse
import bisect
import collections
import copy
import datetime
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_cache_dir():
    """The tool's directory under the user's cache location (never inside the repository)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ietf-meeting-vcons", "reference-types")


EXCLUDED_PURPOSES = {"meeting_metadata", "ingress_info", "lawful_basis"}
DEFAULT_MIMETYPE = "application/pdf"

EXT_MIME = {
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".xml": "application/xml",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pps": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".key": "application/vnd.apple.keynote",
    ".rtf": "application/rtf",
    ".csv": "text/csv",
    ".ps": "application/postscript",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

NOTE_A = (
    "This session did not meet. The recording attached to this file belongs to another "
    "session of the same group at this meeting; its start time is as previously published "
    "and has not been verified."
)
NOTE_B = (
    "This session has no time in the official schedule. The recording's start time is as "
    "previously published and has not been verified."
)
NOTE_C = (
    "The recording's file name indicates it was made at a different session of the same "
    "group at this meeting than the one this file is named for."
)

# Group C comes from the independent audit (audio file name timestamp matches another session).
GROUP_C = {
    "ietf90/ietf90_mpls_23162.vcon.json",
    "ietf90/ietf90_tsvwg_23197.vcon.json",
    "ietf90/ietf90_pcp_23229.vcon.json",
}

PROCESS_MARKERS = ("chair_determination.py", "ietf2vcon convert")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def ext_to_mime(name):
    """Mime type for a file name by its last extension, or None."""
    if not isinstance(name, str):
        return None
    ext = os.path.splitext(name.strip())[1].lower()
    return EXT_MIME.get(ext)


def ext_of(name):
    if not isinstance(name, str):
        return None
    ext = os.path.splitext(name.strip())[1].lower()
    return ext or None


def dumps_file(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def dumps_body(obj):
    return json.dumps(obj, ensure_ascii=False)


def parse_body(att):
    """Parse an inline JSON body to a dict, or None."""
    if att.get("encoding") != "json" or not isinstance(att.get("body"), str):
        return None
    try:
        b = json.loads(att["body"])
    except ValueError:
        return None
    return b if isinstance(b, dict) else None


def is_body_ref(att):
    """Unhashed material reference: inline JSON body with a url, no hash, no attachment url."""
    if att.get("purpose") in EXCLUDED_PURPOSES:
        return False
    if "url" in att or "content_hash" in att:
        return False
    b = parse_body(att)
    return b is not None and isinstance(b.get("url"), str)


def metadata_body(obj):
    for att in obj.get("attachments", []):
        if att.get("purpose") == "meeting_metadata":
            b = parse_body(att)
            if b is not None:
                return att, b
    return None, None


def has_recording(obj):
    return any(isinstance(d, dict) and d.get("type") == "recording" for d in obj.get("dialog", []))


def note_for(relpath, obj, group_c):
    """Return (groups, note text) for a file, or ([], None)."""
    _, md = metadata_body(obj)
    groups = []
    if md is not None and has_recording(obj):
        status = md.get("status")
        if status and status != "sched":
            groups.append("A")
        elif status == "sched" and not any(
            isinstance(s, dict) and s.get("start") for s in obj.get("sessions", [])
        ):
            groups.append("B")
    if relpath in group_c:
        groups.append("C")
    text = " ".join({"A": NOTE_A, "B": NOTE_B, "C": NOTE_C}[g] for g in groups)
    return groups, (text or None)


class PreviousRelease:
    """Read-only access to files at a git ref (git cat-file --batch)."""

    def __init__(self, git_dir, ref):
        self.git_dir = git_dir
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        found = subprocess.run(
            ["git", "-C", git_dir, "rev-parse", "--verify", ref + "^{commit}"],
            capture_output=True, text=True, env=env,
        )
        if found.returncode != 0:
            raise SystemExit(f"cannot find the previous release {ref!r} in the git repository {git_dir}: "
                             f"{found.stderr.strip()}")
        self.ref = found.stdout.strip()
        self.proc = subprocess.Popen(
            ["git", "-C", git_dir, "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
        )

    def read(self, relpath):
        self.proc.stdin.write(f"{self.ref}:{relpath}\n".encode())
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode().split()
        if len(header) == 2 and header[1] == "missing":
            return None
        size = int(header[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)
        return data.decode("utf-8")

    def body_ref_mimetypes(self, relpath):
        """{url: set(mimetype)} of the previous release's body references in the file (None if no file)."""
        text = self.read(relpath)
        if text is None:
            return None
        try:
            obj = json.loads(text)
        except ValueError:
            return None
        out = collections.defaultdict(set)
        for att in obj.get("attachments", []):
            if is_body_ref(att):
                b = parse_body(att)
                out[b["url"]].add(b.get("mimetype"))
        return out

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


class MirrorIndex:
    """Read-only lookup of a document by name under <mirror>/<meeting>/."""

    def __init__(self, root):
        self.root = root
        self.available = bool(root) and os.path.isdir(root)
        self._cache = {}

    def _index(self, meeting):
        if meeting in self._cache:
            return self._cache[meeting]
        entries = []
        base = os.path.join(self.root, str(meeting))
        if os.path.isdir(base):
            for dirpath, _dirs, files in os.walk(base):
                for fn in files:
                    if not fn.startswith("."):
                        entries.append((fn, os.path.relpath(os.path.join(dirpath, fn), base)))
        entries.sort()
        names = [e[0] for e in entries]
        self._cache[meeting] = (names, entries)
        return self._cache[meeting]

    def lookup(self, meeting, docname):
        """('unique', relpath) | ('none', None) | ('ambiguous', [relpaths])."""
        if not self.available or meeting is None or not docname:
            return "none", None
        names, entries = self._index(meeting)
        found = []
        i = bisect.bisect_left(names, docname)
        while i < len(names) and names[i].startswith(docname):
            rest = names[i][len(docname):]
            if rest[:1] in (".", "-"):
                found.append(entries[i][1])
            i += 1
        if len(found) == 1:
            return "unique", found[0]
        if not found:
            return "none", None
        return "ambiguous", found


def doc_name(url):
    seg = url.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return seg or None


class Context:
    def __init__(self, previous=None, mirror=None, group_c=None, now=None, previous_pdf="ignore"):
        self.previous = previous
        self.previous_pdf = previous_pdf
        self.mirror = mirror
        self.group_c = GROUP_C if group_c is None else set(group_c)
        self.now = now or utc_now
        self.stats = collections.Counter()
        self.unmapped_ext = collections.Counter()
        self.rule3_examples = []
        self.rule3_ambiguous = []
        self.rule3_unmapped = []
        self.rule1_vs_previous_conflicts = []
        self.previous_now = collections.Counter()
        self.mimetype_now = collections.Counter()
        self.mimetype_previous_all = collections.Counter()
        self.scope_previous_value = collections.Counter()


def resolve_mimetype(relpath, obj, att_index, body, base_map, ctx):
    """Apply rules 1-4. Returns (rule, new_mimetype_or_None, detail)."""
    filename = body.get("filename")
    ext = ext_of(filename)
    base_vals = None
    if base_map is not None:
        vals = {v for v in base_map.get(body["url"], set()) if v}
        if ctx.previous_pdf == "ignore":
            vals.discard(DEFAULT_MIMETYPE)
        base_vals = vals
    if ext is not None:
        mapped = EXT_MIME.get(ext)
        if mapped is not None:
            if base_vals and base_vals != {mapped}:
                ctx.rule1_vs_previous_conflicts.append(
                    (relpath, body["url"], filename, mapped, sorted(base_vals)))
            return "1", mapped, filename
        ctx.unmapped_ext[ext] += 1
    if base_vals is not None and len(base_vals) == 1:
        return "2", next(iter(base_vals)), "previous release"
    if base_vals is not None and len(base_vals) > 1:
        ctx.stats["rule2_ambiguous_previous"] += 1
    if ctx.mirror is not None and ctx.mirror.available:
        _, md = metadata_body(obj)
        meeting = md.get("ietf_meeting_number") if md else None
        name = doc_name(body["url"])
        status, found = ctx.mirror.lookup(meeting, name)
        if status == "unique":
            mext = ext_of(found)
            mapped = EXT_MIME.get(mext) if mext else None
            if mapped is not None:
                return "3", mapped, found
            ctx.rule3_unmapped.append((relpath, body["url"], found))
            ctx.unmapped_ext[mext or "(none)"] += 1
        elif status == "ambiguous":
            ctx.rule3_ambiguous.append((relpath, body["url"], found))
    return "4", None, "no information"


def compute_changes(relpath, obj, ctx, base_map_loader=None):
    """Return (new_obj, changes). ``obj`` is not modified."""
    new = copy.deepcopy(obj)
    changes = []
    base_map = None
    base_loaded = False
    for i, att in enumerate(new.get("attachments", [])):
        if not is_body_ref(att):
            continue
        body = parse_body(att)
        cur = body.get("mimetype")
        filename_mime = ext_to_mime(body.get("filename"))
        if cur != DEFAULT_MIMETYPE and filename_mime is None:
            continue
        if cur != DEFAULT_MIMETYPE and filename_mime == cur:
            continue
        if not base_loaded:
            base_map = base_map_loader(relpath) if base_map_loader else None
            base_loaded = True
        rule, mime, detail = resolve_mimetype(relpath, obj, i, body, base_map, ctx)
        change = {
            "file": relpath, "att_index": i, "kind": "mimetype", "rule": rule,
            "group": "", "url": body["url"], "old": cur, "new": mime,
            "changed": mime != cur, "detail": detail,
        }
        changes.append(change)
        if mime == cur:
            continue
        if mime is None:
            del body["mimetype"]
        else:
            body["mimetype"] = mime
        att["body"] = dumps_body(body)

    groups, note = note_for(relpath, obj, ctx.group_c)
    if note:
        att, md = metadata_body(new)
        if att is None:
            changes.append({
                "file": relpath, "att_index": None, "kind": "recording_note", "rule": "+".join(groups),
                "group": "+".join(groups), "url": "", "old": None, "new": note,
                "changed": False, "detail": "error: no meeting_metadata body",
            })
        else:
            old = md.get("recording_note")
            changes.append({
                "file": relpath,
                "att_index": next(j for j, x in enumerate(new["attachments"]) if x is att),
                "kind": "recording_note",
                "rule": "+".join(groups), "group": "+".join(groups), "url": "", "old": old,
                "new": note, "changed": old != note, "detail": "",
            })
            if old != note:
                md["recording_note"] = note
                att["body"] = dumps_body(md)

    if any(c["changed"] for c in changes if c["kind"] == "mimetype") or any(
            c["changed"] for c in changes if c["kind"] == "recording_note"):
        new["updated_at"] = ctx.now()
    return new, changes


def _body_diff_ok(a, b, allowed, errs, where):
    pa, pb = parse_body(a), parse_body(b)
    if pa is None or pb is None:
        errs.append(f"{where}: body is not parseable JSON")
        return
    ra = {k: v for k, v in pa.items() if k != allowed}
    rb = {k: v for k, v in pb.items() if k != allowed}
    if ra != rb or list(ra) != list(rb):
        errs.append(f"{where}: members other than {allowed} differ")


def guard(orig, new):
    """Errors if anything but the permitted members differ between two parsed files."""
    errs = []
    if list(orig) != list(new):
        errs.append("top-level keys differ")
    for k in orig:
        if k in ("attachments", "updated_at"):
            continue
        if orig.get(k) != new.get(k):
            errs.append(f"top-level member {k!r} differs")
    oa, na = orig.get("attachments", []), new.get("attachments", [])
    if len(oa) != len(na):
        errs.append("attachment count differs")
        return errs
    for i, (a, b) in enumerate(zip(oa, na)):
        where = f"attachments[{i}]"
        if list(a) != list(b):
            errs.append(f"{where}: keys differ")
            continue
        for k in a:
            if k != "body" and a[k] != b[k]:
                errs.append(f"{where}: member {k!r} differs")
        if a.get("body") != b.get("body"):
            if a.get("purpose") == "meeting_metadata":
                _body_diff_ok(a, b, "recording_note", errs, where)
            elif is_body_ref(a):
                _body_diff_ok(a, b, "mimetype", errs, where)
            else:
                errs.append(f"{where}: body of a protected attachment differs")
    return errs


def atomic_write(path, text, expect_bytes):
    """Write text to path atomically; refuse if the file changed since it was read."""
    st = os.stat(path)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".postpass-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, st.st_mode & 0o7777)
        with open(path, "rb") as f:
            if f.read() != expect_bytes:
                raise RuntimeError("file changed on disk since it was read")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def process_file(root, relpath, ctx, apply=False, base_map_loader=None, transform=compute_changes):
    """Process one file. Returns dict(file, changes, errors, wrote)."""
    path = os.path.join(root, relpath)
    with open(path, "rb") as f:
        raw = f.read()
    result = {"file": relpath, "changes": [], "errors": [], "wrote": False}
    try:
        text = raw.decode("utf-8")
        obj = json.loads(text)
    except ValueError as e:
        result["errors"].append(f"unreadable: {e}")
        return result
    new, changes = transform(relpath, obj, ctx, base_map_loader)
    result["changes"] = changes
    for c in changes:
        if c["kind"] == "recording_note" and c["detail"].startswith("error:"):
            result["errors"].append(c["detail"])
    if result["errors"]:
        return result
    if not any(c["changed"] for c in changes):
        return result
    new_text = dumps_file(new)
    errs = guard(obj, json.loads(new_text))
    if errs:
        result["errors"].extend(errs)
        return result
    if apply:
        try:
            atomic_write(path, new_text, raw)
            result["wrote"] = True
        except Exception as e:
            result["errors"].append(f"write failed: {e}")
    return result


def list_files(root):
    out = []
    for d in sorted(os.listdir(root)):
        full = os.path.join(root, d)
        if os.path.isdir(full) and (d.startswith("ietf") or d.startswith("interim-")):
            for fn in sorted(os.listdir(full)):
                if fn.endswith(".vcon.json") and not fn.startswith("."):
                    out.append(f"{d}/{fn}")
    return out


def running_blockers():
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    me = os.getpid()
    found = []
    for line in out.splitlines():
        line = line.strip()
        pid, _, cmd = line.partition(" ")
        if pid.isdigit() and int(pid) == me:
            continue
        if any(m in cmd for m in PROCESS_MARKERS):
            found.append(line)
    return found


def git_status_porcelain(root):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    r = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True,
                       text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git status failed: {r.stderr.strip()}")
    return r.stdout


def in_git_repo(path):
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return True
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


def is_production(output_root, dataset_root):
    """The output is the dataset (or a parent or child of it), or lies inside any git working tree."""
    out, rl = os.path.realpath(output_root), os.path.realpath(dataset_root)
    if out == rl or out.startswith(rl + os.sep) or rl.startswith(out + os.sep):
        return True
    return in_git_repo(out)


def lock_problem(lock_file):
    """Why a batch driver's lock forbids applying (None if it does not).

    The lock is a file holding a pid, or a directory with a file ``pid``. It forbids applying if
    that process is alive, or if the lock exists without a readable pid (a driver may be starting).
    A reused pid makes a stale lock look live, which errs on the side of refusing.
    """
    if not lock_file or not os.path.exists(lock_file):
        return None
    path = os.path.join(lock_file, "pid") if os.path.isdir(lock_file) else lock_file
    try:
        with open(path) as handle:
            text = handle.read().strip()
    except OSError:
        text = ""
    if not text.isdigit():
        return f"the lock {lock_file} exists without a readable pid (a driver may be starting)"
    alive = subprocess.run(["ps", "-p", text, "-o", "command="], capture_output=True, text=True).stdout.strip()
    return f"the process named by the lock is running (pid {text}, lock {lock_file})" if alive else None


def check_production(output_root, allow, dataset_root, lock_file=None):
    """Raise SystemExit with a reason if applying to output_root is not permitted."""
    if not is_production(output_root, dataset_root):
        return
    if not allow:
        raise SystemExit(f"refusing: {output_root} is (inside) a git working tree; pass --allow-production")
    blockers = running_blockers()
    if blockers:
        raise SystemExit("refusing: conflicting processes are running:\n  " + "\n  ".join(blockers))
    locked = lock_problem(lock_file)
    if locked:
        raise SystemExit("refusing: " + locked)
    status = git_status_porcelain(output_root)
    if status.strip():
        raise SystemExit("refusing: the working tree has uncommitted changes:\n" + status[:2000])


TSV_COLS = ["file", "att_index", "kind", "rule", "group", "url", "old", "new", "changed", "detail"]


def _tsv(v):
    if v is None:
        return ""
    return str(v).replace("\t", " ").replace("\n", " ")


def write_reports(report_dir, mode, results, summary):
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, f"{mode}.tsv"), "w", encoding="utf-8") as f:
        f.write("\t".join(TSV_COLS) + "\n")
        for r in results:
            for c in r["changes"]:
                f.write("\t".join(_tsv(c.get(k)) for k in TSV_COLS) + "\n")
            for e in r["errors"]:
                f.write("\t".join([r["file"], "", "error", "", "", "", "", "", "", _tsv(e)]) + "\n")
    with open(os.path.join(report_dir, f"{mode}.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "files": [r for r in results if r["changes"] or r["errors"]]},
                  f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(report_dir, f"{mode}-summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")


def spread(items, n=10):
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def summarize(results, ctx, mode, files_total):
    s = {"mode": mode, "files_scanned": files_total}
    rows = [c for r in results for c in r["changes"]]
    mt = [c for c in rows if c["kind"] == "mimetype"]
    s["mimetype_in_scope_attachments"] = len(mt)
    s["mimetype_changed_attachments"] = sum(c["changed"] for c in mt)
    by_rule = collections.defaultdict(collections.Counter)
    by_rule_unchanged = collections.defaultdict(collections.Counter)
    for c in mt:
        (by_rule if c["changed"] else by_rule_unchanged)[c["rule"]][c["new"] or "(removed)"] += 1
    s["changed_by_rule_and_type"] = {k: dict(v) for k, v in sorted(by_rule.items())}
    s["in_scope_unchanged_by_rule_and_type"] = {k: dict(v) for k, v in sorted(by_rule_unchanged.items())}
    s["all_in_scope_by_rule_and_type"] = {
        rule: dict(collections.Counter(c["new"] or "(removed)" for c in mt if c["rule"] == rule))
        for rule in sorted({c["rule"] for c in mt})}
    s["changed_by_rule"] = {k: sum(v.values()) for k, v in sorted(by_rule.items())}
    s["mimetype_now_all_body_refs"] = dict(ctx.mimetype_now.most_common())
    s["mimetype_previous_all_body_refs"] = dict(ctx.mimetype_previous_all.most_common())
    s["in_scope_previous_value_by_url"] = dict(ctx.scope_previous_value.most_common())
    s["unmapped_extensions"] = dict(ctx.unmapped_ext)
    s["extensions_in_scope_filenames"] = dict(collections.Counter(
        ext_of(c["detail"]) for c in mt if c["rule"] == "1").most_common())
    s["rule1_vs_previous_conflicts"] = len(ctx.rule1_vs_previous_conflicts)
    s["rule1_vs_previous_conflict_examples"] = ctx.rule1_vs_previous_conflicts[:10]
    s["rule2_ambiguous_previous"] = ctx.stats["rule2_ambiguous_previous"]
    s["mirror_available"] = bool(ctx.mirror and ctx.mirror.available)
    r3 = [c for c in mt if c["rule"] == "3"]
    s["rule3_resolved"] = len(r3)
    s["rule3_examples"] = [{"file": c["file"], "url": c["url"], "mirror_file": c["detail"], "new": c["new"]}
                           for c in spread(r3, 10)]
    s["rule3_ambiguous"] = len(ctx.rule3_ambiguous)
    s["rule3_ambiguous_examples"] = [list(x) for x in ctx.rule3_ambiguous[:5]]
    s["rule3_unique_but_unmapped_extension"] = [list(x) for x in ctx.rule3_unmapped]
    notes = [c for c in rows if c["kind"] == "recording_note"]
    groups = collections.defaultdict(list)
    for c in notes:
        groups[c["group"]].append(c["file"])
    s["note_groups_matched"] = {g: sorted(v) for g, v in sorted(groups.items())}
    s["note_files_changed"] = {g: sorted(c["file"] for c in notes if c["group"] == g and c["changed"])
                               for g in sorted(groups)}
    s["files_with_changes"] = sum(1 for r in results if any(c["changed"] for c in r["changes"]))
    s["files_written"] = sum(1 for r in results if r["wrote"])
    s["files_with_errors"] = sum(1 for r in results if r["errors"])
    s["errors"] = [{"file": r["file"], "errors": r["errors"]} for r in results if r["errors"]]
    s["file_groups"] = {
        "mimetype_only": sum(1 for r in results if any(c["changed"] and c["kind"] == "mimetype" for c in r["changes"])
                             and not any(c["changed"] and c["kind"] == "recording_note" for c in r["changes"])),
        "note_only": sum(1 for r in results if any(c["changed"] and c["kind"] == "recording_note" for c in r["changes"])
                         and not any(c["changed"] and c["kind"] == "mimetype" for c in r["changes"])),
        "both": sum(1 for r in results if any(c["changed"] and c["kind"] == "recording_note" for c in r["changes"])
                    and any(c["changed"] and c["kind"] == "mimetype" for c in r["changes"])),
    }
    return s


def survey(root, relpath, ctx, previous):
    """Distributions of body-reference mimetypes now and in the previous release (plan mode only)."""
    with open(os.path.join(root, relpath), encoding="utf-8") as f:
        obj = json.load(f)
    base_map = None
    for att in obj.get("attachments", []):
        if is_body_ref(att):
            ctx.mimetype_now[parse_body(att).get("mimetype")] += 1
    if previous is not None:
        text = previous.read(relpath)
        if text is not None:
            for att in json.loads(text).get("attachments", []):
                if is_body_ref(att):
                    ctx.mimetype_previous_all[parse_body(att).get("mimetype")] += 1
        base_map = previous.body_ref_mimetypes(relpath)
    for att in obj.get("attachments", []):
        if is_body_ref(att):
            b = parse_body(att)
            if b.get("mimetype") == DEFAULT_MIMETYPE:
                vals = sorted(v or "(none)" for v in (base_map or {}).get(b["url"], set())) if base_map is not None else []
                ctx.scope_previous_value["|".join(vals) if vals else "(no body reference in the previous release)"] += 1


HELP_DESCRIPTION = """\
Corrects the mimetype member of unhashed material references that claim application/pdf (the
converter's default guess): from the file name, else from the previous release, else from the
proceedings mirror, else the member is removed. Also adds a recording_note to the
meeting_metadata body of files whose recording may belong to another session. Nothing else
changes, and a file is skipped with an error if anything else would differ.

Order of operations: run it after the converter and the chair determination pass. Plan (the
default, reads only); then --apply --output-root DIR; then check with check_reference_types.py
against a copy of the tree from before the run. A second --apply changes nothing.
"""


def load_group_c(path):
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip() and not line.startswith("#")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=HELP_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset-root", default=REPO_ROOT,
                    help="tree to plan over: the directory holding the meeting directories (default: the "
                         "repository root, found relative to this script)")
    ap.add_argument("--apply", action="store_true", help="modify files in --output-root in place")
    ap.add_argument("--output-root",
                    help="tree modified by --apply: a scratch copy, or the dataset itself (needs --allow-production)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit --apply inside a git working tree; it still refuses unless git status is clean, "
                         "no chair_determination.py or `ietf2vcon convert` process is running, and the "
                         "--lock-file process (if given) is not alive")
    ap.add_argument("--lock-file",
                    help="optional: a batch driver's lock (a file holding its pid, or a directory with a file pid); "
                         "applying in a git working tree is refused while that process is alive")
    ap.add_argument("--git-dir",
                    help="git repository to read the previous release from (default: --dataset-root). Needed only "
                         "when the files are not in a git repository, for example a scratch copy used for testing")
    ap.add_argument("--previous-rev",
                    help="commit of the previous release: the earlier published value of each reference (rule 2) "
                         "is read from it. Required")
    ap.add_argument("--previous-pdf", choices=("ignore", "keep"), default="ignore",
                    help="rule 2: 'ignore' (default) treats a previous application/pdf as no information, because it "
                         "was the converter's default guess, and falls through to rules 3 and 4; 'keep' uses any "
                         "previous value, including application/pdf")
    ap.add_argument("--proceedings",
                    help="root of the proceedings mirror (the directory holding <meeting>/); enables rule 3. "
                         "Without it rule 3 is skipped")
    ap.add_argument("--group-c-file",
                    help="file with one relative path per line (lines starting with # ignored) replacing the built-in "
                         "list of files whose recording file name indicates another session (group C)")
    ap.add_argument("--report-dir", default=os.path.join(user_cache_dir(), "reports"),
                    help="where the TSV and JSON reports are written (default: %(default)s)")
    ap.add_argument("--report-name", help="base name of report files (default: plan or apply)")
    args = ap.parse_args(argv)

    if not args.previous_rev:
        ap.error("--previous-rev is required (the commit of the previous release)")
    if args.apply:
        if not args.output_root:
            ap.error("--apply needs --output-root")
        check_production(args.output_root, args.allow_production, args.dataset_root, args.lock_file)
        tree = args.output_root
        mode = "apply"
    else:
        tree = args.dataset_root
        mode = "plan"
    mode = args.report_name or mode

    previous = PreviousRelease(args.git_dir or args.dataset_root, args.previous_rev)
    mirror = MirrorIndex(args.proceedings)
    group_c = load_group_c(args.group_c_file) if args.group_c_file else None
    ctx = Context(previous=previous, mirror=mirror, group_c=group_c, previous_pdf=args.previous_pdf)
    files = list_files(tree)
    results = []
    try:
        for rel in files:
            if not args.apply:
                survey(tree, rel, ctx, previous)
            results.append(process_file(tree, rel, ctx, apply=args.apply,
                                        base_map_loader=previous.body_ref_mimetypes))
    finally:
        previous.close()
    summary = summarize(results, ctx, mode, len(files))
    summary.update({"previous_pdf": args.previous_pdf, "tree": tree, "previous_commit": previous.ref,
                    "mirror": args.proceedings})
    write_reports(args.report_dir, mode, results, summary)
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("rule3_examples", "errors", "note_groups_matched",
                                   "rule1_vs_previous_conflict_examples", "rule3_ambiguous_examples",
                                   "rule3_unique_but_unmapped_extension")},
                     indent=2))
    return 1 if summary["files_with_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
