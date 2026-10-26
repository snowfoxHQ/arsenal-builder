#!/usr/bin/env python3
"""Self-test for the arsenal gate. No real sandbox, no network, no API key use.

It ships a fake `sbx` executable and drives runner.py end to end through the
scenarios that matter. Run it after installing, and after any edit to the
contract.

    python selftest.py

Exit code 0 means every scenario behaved as expected.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner  # noqa: E402
from arsenal_contract import validate  # noqa: E402

FAKE_SBX = r'''
import json, os, shutil, sys

state = os.environ["FAKE_SBX_STATE"]
args = sys.argv[1:]
cmd = args[0] if args else ""

def read(name, default=""):
    path = os.path.join(state, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

if cmd == "ls":
    if "--format" in args or "--json" in args:
        entries = []
        if os.path.exists(os.path.join(state, "exists")):
            ws = read("workspace").strip()
            entries.append({"name": "test-sandbox", "workspaces": [ws] if ws else []})
        print(json.dumps(entries))
        sys.exit(0)
    print("SANDBOX       AGENT    STATUS")
    if os.path.exists(os.path.join(state, "exists")):
        print("test-sandbox  claude   running")
    sys.exit(0)

if cmd == "create":
    open(os.path.join(state, "exists"), "w").close()
    print("sandbox created")
    sys.exit(0)

if cmd == "rm":
    try:
        os.unlink(os.path.join(state, "exists"))
    except OSError:
        pass
    print("sandbox removed")
    sys.exit(0)

if cmd == "exec":
    blob = os.path.join(state, "exec.bin")
    if os.path.exists(blob):
        with open(blob, "rb") as fh:
            sys.stdout.buffer.write(fh.read())
        sys.stdout.buffer.flush()
    sys.exit(int(read("exec.rc", "0").strip() or "0"))

if cmd == "cp":
    src, dst = args[1], args[2]
    base = src.replace("\\", "/").rstrip("/").split("/")[-1]
    files = os.path.join(state, "files")
    if base == "arsenal-out" and os.path.isdir(dst):
        # directory copy: mirror whatever the agent staged
        shutil.copytree(files, os.path.join(dst, "arsenal-out"), dirs_exist_ok=True)
        sys.exit(0)
    path = os.path.join(files, base)
    if os.path.exists(path):
        shutil.copyfile(path, dst)
        sys.exit(0)
    print("cp: no such file: " + src)
    sys.exit(1)

print("fake sbx: unsupported command " + cmd)
sys.exit(1)
'''


def make_result(**overrides):
    data = {
        "project": "vecRecall",
        "commit": "7f3a2c9",
        "started_at": "2026-10-26T18:00:00+08:00",
        "finished_at": "2026-10-26T18:12:31+08:00",
        "total": 230,
        "passed": 230,
        "failed": 0,
        "skipped": 0,
        "blocked": [],
    }
    data.update(overrides)
    return data


def build_workspace(root):
    fake = os.path.join(root, "fake_sbx.py")
    with open(fake, "w", encoding="utf-8") as handle:
        handle.write(FAKE_SBX)

    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "files"), exist_ok=True)
    os.environ["FAKE_SBX_STATE"] = state

    project = os.path.join(root, "project")
    os.makedirs(os.path.join(project, ".git"), exist_ok=True)

    # Fixture for the preflight scenario below: a directory that is not a repo.
    os.makedirs(os.path.join(root, "notrepo"), exist_ok=True)

    cfg = {
        "sandbox": "test-sandbox",
        "project_dir": project,
        "workspace_mode": "clone",
        "sandbox_out_dir": "/tmp/arsenal-out",
        "result_name": "result.json",
        "report_name": "report.md",
        "expect_commit": None,
        "sbx_bin": [sys.executable, fake],
        "prompt_file": os.path.join(runner.BASE_DIR, "PROMPT.md"),
        "out_dir": os.path.join(root, "out"),
        "recreate": False,
    }
    cfg_path = os.path.join(root, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2)

    return cfg, cfg_path, state


def reset_state(state):
    for name in ("exists", "exec.rc", "exec.bin", "workspace"):
        path = os.path.join(state, name)
        if os.path.exists(path):
            os.unlink(path)
    files = os.path.join(state, "files")
    if os.path.isdir(files):
        for name in os.listdir(files):
            os.unlink(os.path.join(files, name))
    # a sandbox is created on first run, so pre-mark it as existing
    open(os.path.join(state, "exists"), "w").close()


def stage(state, result=None, exec_rc=0, exec_output=b"", workspace=None):
    files = os.path.join(state, "files")
    if result is not None:
        payload = result if isinstance(result, str) else json.dumps(result)
        with open(os.path.join(files, "result.json"), "w", encoding="utf-8") as handle:
            handle.write(payload)
    with open(os.path.join(state, "exec.rc"), "w", encoding="utf-8") as handle:
        handle.write(str(exec_rc))
    with open(os.path.join(state, "exec.bin"), "wb") as handle:
        handle.write(exec_output)
    if workspace is not None:
        with open(os.path.join(state, "workspace"), "w", encoding="utf-8") as handle:
            handle.write(workspace)


def run_runner(cfg_path):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = runner.main(["--config", cfg_path])

    with open(cfg_path, "r", encoding="utf-8") as handle:
        out_dir = json.load(handle)["out_dir"]
    latest = os.path.join(out_dir, "latest.txt")
    run_dir = None
    if os.path.isfile(latest):
        with open(latest, "r", encoding="utf-8") as handle:
            run_dir = handle.read().strip()
    return code, buffer.getvalue(), run_dir


def check_run_dir(run_dir):
    """The host must be left with evidence, not an empty folder."""
    if not run_dir or not os.path.isdir(run_dir):
        return False, "no run directory was recorded"
    missing = [
        name
        for name in ("summary.json", "agent.log")
        if not os.path.isfile(os.path.join(run_dir, name))
    ]
    if missing:
        return False, "run directory is missing " + ", ".join(missing)
    if not os.path.isfile(os.path.join(run_dir, "sandbox", "arsenal-out", "result.json")):
        return False, "result.json was not pulled back under sandbox/arsenal-out/"
    return True, ""


def check_home_resolution(_run_dir=None):
    """Data paths hang off the arena root; code paths hang off the skill.

    If a relative out_dir resolved against the skill directory instead, then
    unzipping the next version over the old one would delete the reports.
    """
    home = tempfile.mkdtemp(prefix="arsenal-home-")
    problems = []
    try:
        got = runner.resolve_from_home("reports/arsenal", home)
        if os.path.normcase(got) != os.path.normcase(os.path.join(home, "reports", "arsenal")):
            problems.append("relative path not joined to home: %r" % got)

        absolute = os.path.join(home, "absolute-out")
        if runner.resolve_from_home(absolute, "X:/elsewhere") != absolute:
            problems.append("an absolute out_dir was rewritten")

        previous = os.environ.get(runner.HOME_ENV)
        try:
            os.environ[runner.HOME_ENV] = home
            if os.path.normcase(runner.resolve_home()) != os.path.normcase(home):
                problems.append("$%s was ignored" % runner.HOME_ENV)
            cli = os.path.join(home, "from-cli")
            if os.path.normcase(runner.resolve_home(cli)) != os.path.normcase(cli):
                problems.append("--root does not win over $%s" % runner.HOME_ENV)
        finally:
            if previous is None:
                os.environ.pop(runner.HOME_ENV, None)
            else:
                os.environ[runner.HOME_ENV] = previous

        if os.path.normcase(runner.resolve_home()) != os.path.normcase(runner.BASE_DIR):
            problems.append("with no root set, home must fall back to the skill dir")
    finally:
        shutil.rmtree(home, ignore_errors=True)

    return (not problems, "; ".join(problems))


def check_prompt_rendered(_run_dir=None):
    """Every {{PLACEHOLDER}} must be substituted before the agent sees it.

    A leftover placeholder means the agent is told to write to a path literally
    called {{RESULT_PATH}}, and the host then declares the run failed for a
    result that was written exactly where it was told to write it.
    """
    cfg = dict(runner.DEFAULT_CONFIG)
    cfg["project_dir"] = "C:/demo/repo"
    cfg["prompt_file"] = os.path.join(runner.BASE_DIR, "PROMPT.md")
    text = runner.build_prompt(cfg)
    if "{{" in text or "}}" in text:
        left = sorted({part.strip() for part in text.split("{{")[1:]})
        return False, "unresolved placeholder(s): %s" % ", ".join(
            "{{%s" % part.split("}}")[0] for part in left
        )
    if not cfg["report_language"].strip():
        return False, "report_language is empty"
    return True, ""


SCENARIOS = [
    {
        "name": "clean pass",
        "setup": lambda s: stage(s, make_result(), 0, b"running 230 tests\n"),
        "expect": 0,
        "why": "all counts consistent, nothing failed",
    },
    {
        "name": "audit trail on host",
        "setup": lambda s: stage(s, make_result(), 0, b"running 230 tests\n"),
        "expect": 0,
        "why": "a run must leave summary.json, agent.log and the sandbox output behind",
        "check": check_run_dir,
    },
    {
        "name": "sandbox for other repo",
        "setup": lambda s: stage(
            s, make_result(), 0, b"", workspace="C:/somewhere/else"
        ),
        "expect": 2,
        "why": "reusing a sandbox cloned from another project tests the wrong tree",
    },
    {
        "name": "home path contract",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "reports must resolve under ARSENAL_HOME, never inside the skill dir",
        "check": check_home_resolution,
    },
    {
        "name": "prompt fully rendered",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "an unsubstituted {{PLACEHOLDER}} sends the agent to a literal path",
        "check": check_prompt_rendered,
    },
    {
        "name": "tests failed",
        "setup": lambda s: stage(
            s, make_result(passed=223, failed=7), 0, b"7 failed\n"
        ),
        "expect": 1,
        "why": "failed > 0",
    },
    {
        "name": "deps blocked",
        "setup": lambda s: stage(
            s,
            make_result(
                passed=220,
                skipped=10,
                blocked=["cryptography>=42 missing, no network"],
            ),
            0,
            b"10 skipped\n",
        ),
        "expect": 3,
        "why": "nothing failed, but part of the suite never ran - not a pass, not a fail",
    },
    {
        "name": "result missing",
        "setup": lambda s: stage(s, None, 0, b"agent wrote nothing\n"),
        "expect": 1,
        "why": "sandbox produced no result.json",
    },
    {
        "name": "counts do not add up",
        "setup": lambda s: stage(s, make_result(passed=200, total=230), 0, b""),
        "expect": 1,
        "why": "200 + 0 + 0 != 230",
    },
    {
        "name": "zero tests",
        "setup": lambda s: stage(
            s, make_result(total=0, passed=0, skipped=0), 0, b"no tests collected\n"
        ),
        "expect": 1,
        "why": "an empty suite is not a pass",
    },
    {
        "name": "agent crashed",
        "setup": lambda s: stage(s, make_result(), 137, b"killed\n"),
        "expect": 1,
        "why": "agent exit code non-zero",
    },
    {
        "name": "garbage json",
        "setup": lambda s: stage(s, "{not json", 0, b""),
        "expect": 1,
        "why": "unparseable result",
    },
    {
        "name": "non-utf8 output",
        "setup": lambda s: stage(
            s, make_result(), 0, b"warning: \xd6\xd0\xce\xc4 \xff\xfe\n"
        ),
        "expect": 0,
        "why": "a stray GBK byte must not break a good run",
    },
    {
        "name": "wrong commit",
        "setup": lambda s: stage(s, make_result(commit="deadbee"), 0, b""),
        "expect": 1,
        "why": "tested revision is not the expected one",
        "expect_commit": "7f3a2c9",
    },
    {
        "name": "abbrev commit ok",
        "setup": lambda s: stage(
            s,
            make_result(commit="7f3a2c9a1b2c3d4e5f60718293a4b5c6d7e8f901"),
            0,
            b"",
        ),
        "expect": 0,
        "why": "agent reports the full hash, config holds the 7-char abbreviation",
        "expect_commit": "7f3a2c9",
    },
    {
        "name": "no project_dir",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 2,
        "why": "preflight must refuse to guess which project to test",
        "project_dir": lambda cfg: None,
    },
    {
        "name": "clone, not a git repo",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 2,
        "why": "sbx create --clone cannot work without .git",
        "project_dir": lambda cfg: os.path.join(
            os.path.dirname(cfg["out_dir"]), "notrepo"
        ),
    },
]


def main():
    root = tempfile.mkdtemp(prefix="arsenal-selftest-")
    previous_key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "sk-selftest-not-a-real-key"
    failures = 0

    try:
        cfg, cfg_path, state = build_workspace(root)
        default_project = cfg["project_dir"]
        print("arsenal self-test")
        print("workspace: %s" % root)
        print("")
        print("%-22s %-8s %-8s %s" % ("scenario", "expect", "actual", "result"))
        print("-" * 72)

        for scenario in SCENARIOS:
            reset_state(state)
            scenario["setup"](state)

            if "expect_commit" in scenario:
                cfg["expect_commit"] = scenario["expect_commit"]
            else:
                cfg["expect_commit"] = None

            override = scenario.get("project_dir")
            cfg["project_dir"] = override(cfg) if callable(override) else default_project

            with open(cfg_path, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2)

            code, output, run_dir = run_runner(cfg_path)
            ok = code == scenario["expect"]
            detail = ""
            if ok and scenario.get("check"):
                ok, detail = scenario["check"](run_dir)
            if not ok:
                failures += 1
            print(
                "%-22s %-8d %-8s %s"
                % (scenario["name"], scenario["expect"], code, "ok" if ok else "MISMATCH")
            )
            if not ok:
                if detail:
                    print("    %s" % detail)
                print("    expected %d because %s" % (scenario["expect"], scenario["why"]))
                for line in output.strip().splitlines()[-8:]:
                    print("    | %s" % line)

        print("-" * 72)
        if failures:
            print("%d scenario(s) MISMATCH - the gate is not trustworthy." % failures)
            return 1
        print("all %d scenarios ok - the gate fails closed." % len(SCENARIOS))
        return 0
    finally:
        if previous_key is None:
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        else:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = previous_key
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
