import os

import pytest

import chair_determination as rc


class Procs:
    def __init__(self, commands=None, running=()):
        self.commands = commands or {}
        self.running = list(running)

    def command(self, pid):
        return self.commands.get(pid)

    def all(self):
        return list(self.running)


def make_run(tmp_path, done=(), failed=(), lock_pid=None, lock_empty=False):
    state = tmp_path / "state"
    state.mkdir()
    for n in done:
        (state / f"{n}.done").write_text("")
    for n in failed:
        (state / f"{n}.failed").write_text("reason\n")
    if lock_pid is not None or lock_empty:
        (tmp_path / "lock").mkdir()
        if lock_pid is not None:
            (tmp_path / "lock" / "pid").write_text(f"{lock_pid}\n")
    return tmp_path


def problems(run, requested=None, procs=None, with_lock=True):
    state = run / "state"
    lock = run / "lock" if with_lock else None
    return rc.apply_safety_problems(state, lock, requested, rc.completed_meetings(state), procs or Procs(), own_pid=1)


def inputs(run, lock=True):
    """The required options of every mode, pointing into a run directory made by make_run."""
    args = ["--state-dir", str(run / "state"), "--log-dir", str(run / "logs"),
            "--previous-rev", "0" * 40, "--proceedings", str(run / "proceedings")]
    return args + (["--lock-file", str(run / "lock")] if lock else [])


def test_quiet_tree_is_safe(tmp_path):
    assert problems(make_run(tmp_path, done=[83, 84])) == []


def test_live_driver_blocks(tmp_path):
    run = make_run(tmp_path, done=[83], lock_pid=4242)
    found = problems(run, procs=Procs({4242: "/bin/bash /x/driver.sh"}))
    assert len(found) == 1 and "named by the lock is running (pid 4242" in found[0]


def test_stale_lock_with_a_dead_pid_does_not_block(tmp_path):
    run = make_run(tmp_path, done=[83], lock_pid=4242)
    assert problems(run, procs=Procs({})) == []


def test_without_a_lock_file_option_the_lock_is_not_looked_at(tmp_path):
    run = make_run(tmp_path, done=[83], lock_pid=4242)
    assert problems(run, procs=Procs({4242: "/bin/bash /x/driver.sh"}), with_lock=False) == []


def test_a_lock_file_may_be_a_plain_file_with_a_pid(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "lockfile").write_text("4242\n")
    live = rc.apply_safety_problems(tmp_path / "state", tmp_path / "lockfile", None, [], Procs({4242: "driver"}), own_pid=1)
    assert len(live) == 1 and "pid 4242" in live[0]
    assert rc.apply_safety_problems(tmp_path / "state", tmp_path / "lockfile", None, [], Procs({}), own_pid=1) == []
    assert rc.apply_safety_problems(tmp_path / "state", tmp_path / "missing", None, [], Procs({4242: "driver"}),
                                    own_pid=1) == []


def test_lock_without_a_pid_blocks_because_a_driver_may_be_starting(tmp_path):
    run = make_run(tmp_path, done=[83], lock_empty=True)
    assert "without a readable pid" in problems(run)[0]


def test_running_conversion_blocks_but_not_this_process(tmp_path):
    run = make_run(tmp_path, done=[83])
    procs = Procs(running=[(10, "python .venv/bin/ietf2vcon convert-all --meeting 94"), (11, "ietf2vcon convert one"),
                           (12, "python chair_determination.py --apply"), (1, "ietf2vcon convert (this very pid)")])
    found = problems(run, procs=procs)
    assert len(found) == 2 and all("a conversion is running" in f for f in found)


def test_requested_meeting_not_done_blocks(tmp_path):
    run = make_run(tmp_path, done=[83, 84])
    assert problems(run, requested=[83, 84]) == []
    found = problems(run, requested=[83, 95])
    assert len(found) == 1 and found[0].startswith("meeting 95 is not complete (no 95.done in ")


def test_pending_retry_blocks_a_full_run_but_not_a_restricted_one(tmp_path):
    run = make_run(tmp_path, done=[83, 84], failed=[90, 84])  # 84 failed once, then done
    assert problems(run) == ["meeting 90 failed and has no .done (a retry is pending)"]
    assert problems(run, requested=[83, 84]) == []
    assert any("90" in f for f in problems(run, requested=[83, 90]))


def run_main(tmp_path, run, procs, *extra, root=None):
    root = root or tmp_path / "out"
    root.mkdir(exist_ok=True)
    return rc.main(
        ["--apply", "--allow-production", "--no-validate", "--dataset-root", str(root), "--output-root", str(root),
         *inputs(run), "--report-dir", str(tmp_path / "reports"), "--cache-dir", str(tmp_path / "cache"),
         "--seed-cache", str(tmp_path / "noseed"), "--max-requests", "0", *extra],
        procs=procs,
    )


@pytest.fixture
def production(tmp_path):
    """The output is the dataset root itself, which is what makes it production."""
    root = tmp_path / "out"
    root.mkdir()
    return root


def test_main_refuses_a_live_driver_before_touching_anything(tmp_path, production):
    (tmp_path / "run").mkdir()
    run = make_run(tmp_path / "run", done=[83], lock_pid=4242)
    code = run_main(tmp_path, run, Procs({4242: "bash driver.sh"}))
    assert code == 2
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").iterdir())  # no data was loaded
    assert sorted(p.name for p in production.iterdir()) == []


def test_main_refuses_a_running_conversion_and_a_pending_retry_and_an_undone_meeting(tmp_path, production):
    (tmp_path / "run").mkdir()
    run = make_run(tmp_path / "run", done=[83], failed=[90])
    assert run_main(tmp_path, run, Procs(running=[(10, "ietf2vcon convert-all --meeting 94")]), "--meetings", "83") == 2
    assert run_main(tmp_path, run, Procs()) == 2  # retry pending
    assert run_main(tmp_path, run, Procs(), "--meetings", "83", "90") == 2  # 90 is not done


def test_main_needs_allow_production_and_an_output_root(tmp_path, production):
    (tmp_path / "run").mkdir()
    run = make_run(tmp_path / "run", done=[83])
    assert rc.main(["--apply", "--no-validate", "--dataset-root", str(production), "--output-root", str(production),
                    *inputs(run), "--report-dir", str(tmp_path / "r")], procs=Procs()) == 2
    assert rc.main(["--apply", "--no-validate", *inputs(run), "--report-dir", str(tmp_path / "r")], procs=Procs()) == 2


def test_a_git_working_tree_counts_as_production_even_if_it_is_not_the_dataset_root(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / ".git").mkdir()
    (tmp_path / "plain").mkdir()
    assert rc.is_production(tmp_path / "other", tmp_path / "somewhere")
    assert rc.is_production(tmp_path / "plain", tmp_path / "plain")
    assert not rc.is_production(tmp_path / "plain", tmp_path / "somewhere")


def test_the_required_inputs_are_checked_in_every_mode(tmp_path):
    for mode in ([], ["--verify"], ["--apply", "--output-root", str(tmp_path), "--no-validate"]):
        assert rc.main([*mode, "--report-dir", str(tmp_path / "r")], procs=Procs()) == 2


def test_apply_needs_a_converter_for_validation_unless_told_not_to(tmp_path):
    (tmp_path / "run").mkdir()
    run = make_run(tmp_path / "run", done=[83])
    base = ["--apply", "--output-root", str(tmp_path / "scratch"), *inputs(run), "--report-dir", str(tmp_path / "r")]
    assert rc.main(base, procs=Procs()) == 2
    assert rc.main([*base, "--converter", str(tmp_path / "nowhere")], procs=Procs()) == 2


def test_the_default_cache_and_report_locations_are_outside_the_repository():
    args = rc.parse_args([])
    repo = rc.REPO_ROOT.resolve()
    for location in (args.cache_dir, args.report_dir):
        assert repo not in location.resolve().parents and location.resolve() != repo
    assert args.dataset_root == rc.REPO_ROOT and args.previous_rev is None and args.proceedings is None
    assert args.log_dir is None and args.state_dir is None and args.lock_file is None
    assert args.seed_cache is None and args.converter is None


def test_the_cache_location_follows_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert rc.user_cache_dir() == tmp_path / "xdg" / "ietf-meeting-vcons" / "chair-determination"


def test_the_user_agent_names_the_tool_and_carries_an_optional_contact_only():
    assert "@" not in rc.user_agent() and "read-only" in rc.user_agent()
    assert rc.user_agent("ops@example.org").endswith("contact: ops@example.org")


def test_apply_and_verify_cannot_be_combined(tmp_path):
    assert rc.main(["--apply", "--verify", "--output-root", str(tmp_path), "--report-dir", str(tmp_path)]) == 2


def test_scratch_apply_is_not_subject_to_the_production_checks(tmp_path):
    (tmp_path / "run").mkdir()
    run = make_run(tmp_path / "run", done=[83], lock_pid=4242, failed=[90])
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    code = rc.main(["--apply", "--no-validate", "--output-root", str(scratch), *inputs(run),
                    "--report-dir", str(tmp_path / "r"),
                    "--cache-dir", str(tmp_path / "cache"), "--seed-cache", str(tmp_path / "noseed"),
                    "--max-requests", "0"], procs=Procs({4242: "bash driver.sh"}))
    assert code == 4  # got as far as loading data (and the request cap of 0 stopped it), not refused with 2


def test_a_pending_interim_retry_blocks_a_full_run(tmp_path):
    run = make_run(tmp_path, done=[83, "interim-2026-vcon-01"], failed=["interim-2026-vcon-02"])
    assert problems(run) == ["meeting interim-2026-vcon-02 failed and has no .done (a retry is pending)"]
    assert problems(run, requested=[83, "interim-2026-vcon-01"]) == []
    found = problems(run, requested=["interim-2026-vcon-02"])
    assert len(found) == 1 and found[0].startswith("meeting interim-2026-vcon-02 is not complete (no interim-2026-vcon-02.done")


def make_run_clean(tmp_path):
    (tmp_path / "run2").mkdir()
    return make_run(tmp_path / "run2", done=[83])


def test_update_records_and_dry_run_need_apply(tmp_path):
    assert rc.main(["--update-records", "--output-root", str(tmp_path), "--report-dir", str(tmp_path)]) == 2
    assert rc.main(["--dry-run", "--output-root", str(tmp_path), "--report-dir", str(tmp_path)]) == 2


def test_the_update_flag_does_not_lift_the_refusals_on_the_real_output(tmp_path, production):
    (tmp_path / "run").mkdir()
    run = make_run(tmp_path / "run", done=[83], lock_pid=4242)
    assert run_main(tmp_path, run, Procs({4242: "bash driver.sh"}), "--update-records") == 2
    assert run_main(tmp_path, make_run_clean(tmp_path), Procs(), "--update-records", "--meetings", "83", "90") == 2
