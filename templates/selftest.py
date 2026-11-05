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

# Populated in main(): paths of the per-backend fake CLIs and their states.
DOCKER = {}
NONE = {}

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
    # Behave like a compliant agent: copy the run id out of the rendered
    # prompt into result.json, unless the scenario staged a deviation
    # (nonce_mode = "wrong" | "omit").
    import re

    prompt = args[args.index("-p") + 1] if "-p" in args else ""
    match = re.search(r'"run_id":\s*"([^"]*)"', prompt)
    rpath = os.path.join(state, "files", "result.json")
    mode = read("nonce_mode").strip()
    if os.path.exists(rpath):
        try:
            with open(rpath, "r", encoding="utf-8") as fh:
                data = json.loads(fh.read())
            if isinstance(data, dict):
                if mode == "omit":
                    data.pop("run_id", None)
                elif mode == "wrong":
                    data["run_id"] = "19700101-000000-stale"
                elif match:
                    data["run_id"] = match.group(1)
                with open(rpath, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(data, indent=2))
        except Exception:
            pass
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

FAKE_DOCKER = r'''
import json, os, shutil, sys

state = os.environ["FAKE_DOCKER_STATE"]
args = sys.argv[1:]
cmd = args[0] if args else ""

def read(name, default=""):
    path = os.path.join(state, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

if cmd == "info":
    print("Docker is fine")
    sys.exit(0)

if cmd == "ps":
    print("test-sandbox" if os.path.exists(os.path.join(state, "exists")) else "", end="")
    sys.exit(0)

if cmd == "inspect":
    print(read("workspace").strip())
    sys.exit(0)

if cmd == "run":
    open(os.path.join(state, "exists"), "w").close()
    for i, a in enumerate(args):
        if a.startswith("arsenal.project="):
            with open(os.path.join(state, "workspace"), "w", encoding="utf-8") as fh:
                fh.write(a.split("=", 1)[1])
    print("container id")
    sys.exit(0)

if cmd == "rm":
    try:
        os.unlink(os.path.join(state, "exists"))
    except OSError:
        pass
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
        shutil.copytree(files, os.path.join(dst, "arsenal-out"), dirs_exist_ok=True)
        sys.exit(0)
    path = os.path.join(files, base)
    if os.path.exists(path):
        shutil.copyfile(path, dst)
        sys.exit(0)
    print("cp: no such file: " + src)
    sys.exit(1)

print("fake docker: unsupported command " + cmd)
sys.exit(1)
'''

# A stand-in for the claude CLI: parses the rendered prompt, writes a
# contract-valid result.json and a report that passes the shape audit.
FAKE_AGENT = r'''
import json, os, re, sys

args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""

run_id = ""
m = re.search(r'"run_id":\s*"([^"]+)"', prompt)
if m:
    run_id = m.group(1)
result_path = ""
m = re.search(r"`([^`\n]*result\.json)`", prompt)
if m:
    result_path = m.group(1)
report_path = result_path.replace("result.json", "report.md") if result_path else ""

os.makedirs(os.path.dirname(result_path), exist_ok=True)
with open(result_path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "project": "fixture",
        "run_id": run_id,
        "commit": "7f3a2c9",
        "started_at": "2026-01-01T09:00:00+08:00",
        "finished_at": "2026-01-01T09:01:00+08:00",
        "total": 10, "passed": 10, "failed": 0, "skipped": 0,
        "blocked": [],
    }, indent=2))
rows = "\n".join("| k%d | v%d |" % (i, i) for i in range(14))
bullets = "\n".join("- covered behaviour %d" % i for i in range(16))
with open(report_path, "w", encoding="utf-8") as fh:
    fh.write("# report\n\n## 1. Section\n\nsome prose. " * 40 + "\n\n" + rows + "\n" + bullets + "\n")
print("fake agent done")
sys.exit(0)
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


HOST_HEAD = ""


def _init_fixture_repo(project):
    """A real commit, so binding self-certification has two HEADs to compare."""
    global HOST_HEAD
    try:
        subprocess.run(
            ["git", "init", "-q"], cwd=project, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        subprocess.run(
            ["git", "-C", project, "config", "user.email", "selftest@arsenal"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        subprocess.run(
            ["git", "-C", project, "config", "user.name", "selftest"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        subprocess.run(
            ["git", "-C", project, "commit", "-q", "--allow-empty", "-m", "fixture"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        result = subprocess.run(
            ["git", "-C", project, "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        HOST_HEAD = result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        HOST_HEAD = ""


def build_workspace(root):
    fake = os.path.join(root, "fake_sbx.py")
    with open(fake, "w", encoding="utf-8") as handle:
        handle.write(FAKE_SBX)

    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "files"), exist_ok=True)
    os.environ["FAKE_SBX_STATE"] = state

    project = os.path.join(root, "project")
    os.makedirs(os.path.join(project, ".git"), exist_ok=True)
    _init_fixture_repo(project)

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
    for name in ("exists", "exec.rc", "exec.bin", "workspace", "nonce_mode"):
        path = os.path.join(state, name)
        if os.path.exists(path):
            os.unlink(path)
    files = os.path.join(state, "files")
    if os.path.isdir(files):
        for name in os.listdir(files):
            path = os.path.join(files, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
    # a sandbox is created on first run, so pre-mark it as existing
    open(os.path.join(state, "exists"), "w").close()


def stage(state, result=None, exec_rc=0, exec_output=b"", workspace=None, report=None):
    files = os.path.join(state, "files")
    if result is not None:
        payload = result if isinstance(result, str) else json.dumps(result)
        with open(os.path.join(files, "result.json"), "w", encoding="utf-8") as handle:
            handle.write(payload)
    if report is not None:
        with open(os.path.join(files, "report.md"), "w", encoding="utf-8") as handle:
            handle.write(report)
    with open(os.path.join(state, "exec.rc"), "w", encoding="utf-8") as handle:
        handle.write(str(exec_rc))
    with open(os.path.join(state, "exec.bin"), "wb") as handle:
        handle.write(exec_output)
    if workspace is not None:
        with open(os.path.join(state, "workspace"), "w", encoding="utf-8") as handle:
            handle.write(workspace)


def run_runner(cfg_path, extra=None):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = runner.main(["--config", cfg_path] + list(extra or []))

    with open(cfg_path, "r", encoding="utf-8") as handle:
        out_dir = json.load(handle)["out_dir"]
    latest = os.path.join(out_dir, "latest.txt")
    run_dir = None
    if os.path.isfile(latest):
        with open(latest, "r", encoding="utf-8") as handle:
            run_dir = handle.read().strip()
    return code, buffer.getvalue(), run_dir


def load_summary(run_dir):
    path = os.path.join(run_dir, "summary.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_endpoint_checks():
    """The vendor endpoint must be config, not code - and reach the env file."""
    from runner import DEFAULT_CONFIG, write_env_file

    problems = []
    if DEFAULT_CONFIG.get("anthropic_base_url") != "https://api.deepseek.com/anthropic":
        problems.append("default base url drifted from the documented default")
    os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "selftest-dummy-key")
    custom = dict(DEFAULT_CONFIG, anthropic_base_url="https://relay.example.com/anthropic")
    path = write_env_file(custom)
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    finally:
        os.unlink(path)
    if "ANTHROPIC_BASE_URL=https://relay.example.com/anthropic" not in content:
        problems.append("configured base url did not reach the agent env file")
    if "ANTHROPIC_AUTH_TOKEN=" not in content:
        problems.append("key missing from the agent env file")
    return (not problems, "; ".join(problems))


def run_lifecycle_checks(base_cfg, cfg_path, state):
    """Resume and profile, end to end.

    Resume must skip a completed agent stage (even with a sabotaged fake CLI,
    which would fail the run if the agent were actually invoked again), rerun
    a crashed one, and refuse a changed environment manifest. A profile must
    reach the runner as a deep overlay.
    """
    problems = []

    def write_cfg(overrides=None):
        cfg = dict(base_cfg)
        cfg.update(overrides or {})
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)

    reset_state(state)
    stage(state, make_result(), 0, b"")
    write_cfg()
    code_a, _, run_dir = run_runner(cfg_path)
    if code_a != 0 or not run_dir:
        return False, "baseline run for lifecycle checks failed (code %s)" % code_a

    # 1. completed agent stage + sabotaged exec -> the agent must be skipped
    with open(os.path.join(state, "exec.rc"), "w", encoding="utf-8") as fh:
        fh.write("137")
    write_cfg()
    code, _, _ = run_runner(cfg_path, extra=["--resume", run_dir])
    if code != 0:
        problems.append("resume did not skip the completed agent stage (code %d)" % code)
    elif not load_summary(run_dir).get("agent_skipped"):
        problems.append("resume passed without recording agent_skipped")

    # 2. crashed agent stage -> resume reruns the agent
    stages_path = os.path.join(run_dir, "stages", "agent.json")
    with open(stages_path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["exit_code"] = 137
    with open(stages_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    reset_state(state)
    stage(state, make_result(), 0, b"")
    write_cfg()
    code, _, _ = run_runner(cfg_path, extra=["--resume", run_dir])
    if code != 0:
        problems.append("resume did not rerun a crashed agent stage (code %d)" % code)

    # 3. changed environment manifest -> refuse
    manifest_path = os.path.join(run_dir, "run_manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["host_python"] = "0.0.0"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    write_cfg()
    code, _, _ = run_runner(cfg_path, extra=["--resume", run_dir])
    if code != 2:
        problems.append("resume accepted a changed environment (code %d)" % code)

    # 4. profile overlay must reach the runner: point project_dir at a
    # non-repo, preflight must flip to exit 2.
    configs_dir = os.path.join(os.path.dirname(cfg_path), "configs")
    os.makedirs(configs_dir, exist_ok=True)
    with open(os.path.join(configs_dir, "badproj.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"project_dir": os.path.join(os.path.dirname(cfg_path), "notrepo")}, fh
        )
    reset_state(state)
    stage(state, make_result(), 0, b"")
    write_cfg()
    code, _, _ = run_runner(cfg_path, extra=["--profile", "badproj"])
    if code != 2:
        problems.append("profile override did not reach the runner (code %d)" % code)

    return (not problems, "; ".join(problems))


def check_run_dir(run_dir):
    """The host must be left with evidence, not an empty folder."""
    if not run_dir or not os.path.isdir(run_dir):
        return False, "no run directory was recorded"
    missing = [
        name
        for name in ("summary.json", "agent.log", "run_manifest.json")
        if not os.path.isfile(os.path.join(run_dir, name))
    ]
    if missing:
        return False, "run directory is missing " + ", ".join(missing)
    if not os.path.isfile(os.path.join(run_dir, "sandbox", "arsenal-out", "result.json")):
        return False, "result.json was not pulled back under sandbox/arsenal-out/"
    with open(os.path.join(run_dir, "run_manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    if HOST_HEAD and manifest.get("project_commit") != HOST_HEAD:
        return False, "manifest project_commit %r != fixture HEAD" % manifest.get("project_commit")
    return True, ""


def check_manifest_comparable(run_dir):
    """The compare tool must tell 'same environment' from 'different'."""
    with contextlib.redirect_stdout(io.StringIO()):
        code_same = runner.main(["--compare", run_dir, run_dir])
    if code_same != 0:
        return False, "a run vs itself compared as different (code %d)" % code_same

    tmp = tempfile.mkdtemp(prefix="arsenal-cmp-")
    try:
        copy = os.path.join(tmp, "copy")
        shutil.copytree(run_dir, copy)
        path = os.path.join(copy, "run_manifest.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["project_commit"] = "0" * 40
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with contextlib.redirect_stdout(io.StringIO()):
            code_diff = runner.main(["--compare", run_dir, copy])
        if code_diff != 1:
            return False, "a changed commit still compared comparable (code %d)" % code_diff
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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

        previous = os.environ.pop(runner.HOME_ENV, None)
        try:
            # With nothing configured, the layout (or the skill dir standalone)
            # decides - never a path written into the source.
            expected = runner.home_from_layout() or runner.BASE_DIR
            if os.path.normcase(runner.resolve_home()) != os.path.normcase(expected):
                problems.append(
                    "with no root set, home must fall back to the layout or the skill dir"
                )

            os.environ[runner.HOME_ENV] = home
            if os.path.normcase(runner.resolve_home()) != os.path.normcase(home):
                problems.append("$%s was ignored" % runner.HOME_ENV)
            cli = os.path.join(home, "from-cli")
            if os.path.normcase(runner.resolve_home(cli)) != os.path.normcase(cli):
                problems.append("--root does not win over $%s" % runner.HOME_ENV)
        finally:
            os.environ.pop(runner.HOME_ENV, None)
            if previous is not None:
                os.environ[runner.HOME_ENV] = previous
    finally:
        shutil.rmtree(home, ignore_errors=True)

    return (not problems, "; ".join(problems))


def check_run_dir_unique(_run_dir=None):
    """Two runs started in the same second must not share a directory.

    If they do, the second run inherits the first run's result.json and reports a
    verdict about a test it never executed - the worst failure mode there is,
    because every number in the report is real, just not this run's.
    """
    tmp = tempfile.mkdtemp(prefix="arsenal-rundir-")
    problems = []
    try:
        ids = []
        for _ in range(3):
            run_id, path = runner.make_run_dir(tmp, when="20260101-120000")
            ids.append(run_id)
            if not os.path.isdir(path):
                problems.append("run directory was not created: %s" % path)
        if len(set(ids)) != len(ids):
            problems.append("same-second run ids collided: %s" % ids)
        if not ids[0].startswith("20260101-120000"):
            problems.append("run id lost its timestamp: %s" % ids[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (not problems, "; ".join(problems))


def check_layout_home(_run_dir=None):
    """The root must be readable from the directory tree, not from a constant.

    Someone moves C:\\ai-arsenal to D:\\arsenal and nothing changes: <home>\\skills\\
    <skill>\\ is still two levels below the root. That only holds if no file
    hard-codes the old path.
    """
    tmp = tempfile.mkdtemp(prefix="arsenal-layout-")
    problems = []
    try:
        home = os.path.join(tmp, "arena")
        skill = os.path.join(home, "skills", "arsenal-builder")
        os.makedirs(skill)
        got = runner.home_from_layout(skill)
        if got is None or os.path.normcase(got) != os.path.normcase(home):
            problems.append("layout <home>/skills/<skill> resolved to %r" % got)

        loose = os.path.join(tmp, "loose")
        os.makedirs(loose)
        if runner.home_from_layout(loose) is not None:
            problems.append("a folder that is not inside skills/ claimed a home")

        nested = os.path.join(home, "skills", "arsenal-builder", "templates")
        if os.path.normcase(runner.home_from_layout(nested) or "") == os.path.normcase(home):
            problems.append("templates/ was treated as the skill directory")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (not problems, "; ".join(problems))


def _report_problems(run_dir):
    path = os.path.join(run_dir, "summary.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle).get("report_problems")


def check_thin_report(run_dir):
    """A report that only says how many tests passed must be flagged.

    Not failed - the tests may be perfectly green - but flagged, because the
    report once collapsed from 174 lines of substance to 51 lines of counts and
    nothing in the pipeline noticed for several versions.
    """
    problems = _report_problems(run_dir)
    if problems is None:
        return False, "no summary.json to read report_problems from"
    if not problems:
        return False, "a one-screen report was accepted without comment"
    return True, ""


def check_rich_report(run_dir):
    problems = _report_problems(run_dir)
    if problems is None:
        return False, "no summary.json to read report_problems from"
    if problems:
        return False, "a full report was rejected: " + "; ".join(problems)
    return True, ""


def check_prompt_rendered(_run_dir=None):
    """Every {{PLACEHOLDER}} must be substituted before the agent sees it.

    A leftover placeholder means the agent is told to write to a path literally
    called {{RESULT_PATH}}, and the host then declares the run failed for a
    result that was written exactly where it was told to write it.
    """
    from backends import get_backend

    cfg = dict(runner.DEFAULT_CONFIG)
    cfg["project_dir"] = "C:/demo/repo"
    cfg["prompt_file"] = os.path.join(runner.BASE_DIR, "PROMPT.md")
    cfg["run_id"] = "20260101-090000"
    backend = get_backend(cfg)
    text = runner.build_prompt(cfg, backend)
    if "20260101-090000" not in text:
        return False, "the run id did not reach the prompt - the freshness nonce is dead"
    if "{{" in text or "}}" in text:
        left = sorted({part.strip() for part in text.split("{{")[1:]})
        return False, "unresolved placeholder(s): %s" % ", ".join(
            "{{%s" % part.split("}}")[0] for part in left
        )
    if not cfg["report_language"].strip():
        return False, "report_language is empty"
    if "Report depth:" not in text:
        return False, "the rendered prompt does not tell the agent how much to write"
    brief = runner.build_prompt(dict(cfg, report_depth="brief"), backend)
    if "Report depth: **brief**" not in brief:
        return False, "report_depth=brief does not reach the prompt"
    return True, ""


def run_backend_contracts(drivers):
    """Every backend must satisfy the same capability contract.

    `drivers` maps backend name -> (bin override key, bin path, state env var).
    The checks are written against the contract, not against any vendor: a new
    backend joins by adding a fake CLI driver here and must pass unchanged.
    This is what keeps "sandbox-agnostic" from decaying into docs-only.
    """
    from backends import BackendError, get_backend

    problems = []
    try:
        get_backend({"backend": "teleport"})
        problems.append("an unknown backend name was accepted")
    except BackendError:
        pass

    for backend_name, (bin_key, fake_cli, state_env) in drivers.items():
        root = tempfile.mkdtemp(prefix="arsenal-backend-%s-" % backend_name)
        previous = os.environ.get(state_env)
        try:
            state = os.path.join(root, "state")
            os.makedirs(os.path.join(state, "files"), exist_ok=True)
            os.environ[state_env] = state
            cfg = {
                "backend": backend_name,
                "sandbox": "test-sandbox",
                "project_dir": os.path.join(root, "proj"),
                "workspace_mode": "clone",
                "sandbox_out_dir": "/tmp/arsenal-out",
                bin_key: [sys.executable, fake_cli],
                "docker_image": "test/agent-image",
            }
            backend = get_backend(cfg)

            try:
                backend.probe()
            except BackendError as exc:
                problems.append("[%s] probe rejected a present binary: %s" % (backend_name, exc))

            missing = get_backend(
                dict(cfg, **{bin_key: os.path.join(root, "no-such-backend.exe")})
            )
            try:
                missing.probe()
                problems.append("[%s] probe accepted a missing binary" % backend_name)
            except BackendError:
                pass

            if backend.exists():
                problems.append("[%s] exists() is true for an empty listing" % backend_name)
            open(os.path.join(state, "exists"), "w").close()
            with open(os.path.join(state, "workspace"), "w", encoding="utf-8") as fh:
                fh.write("C:/some/host/dir")
            if not backend.exists():
                problems.append("[%s] exists() missed a marked instance" % backend_name)
            if backend.binding() != "C:/some/host/dir":
                problems.append(
                    "[%s] binding() returned %r" % (backend_name, backend.binding())
                )

            got = backend.in_sandbox_path("C:\\x\\y")
            expected_path = "/c/x/y" if backend_name == "sbx" else got
            if got != expected_path:
                problems.append("[%s] in_sandbox_path returned %r" % (backend_name, got))

            if backend.fetch_file(
                "/tmp/arsenal-out", "absent.json", os.path.join(root, "out.bin")
            ):
                problems.append(
                    "[%s] fetch_file claimed success for a missing file" % backend_name
                )
        finally:
            if previous is None:
                os.environ.pop(state_env, None)
            else:
                os.environ[state_env] = previous
            shutil.rmtree(root, ignore_errors=True)

    # The none backend has no CLI to fake - drive it directly against the
    # real file system.
    root = tempfile.mkdtemp(prefix="arsenal-backend-none-")
    try:
        project = os.path.join(root, "proj")
        os.makedirs(project)
        cfg = {
            "backend": "none",
            "sandbox": "none",
            "project_dir": project,
            "workspace_mode": "bind",
        }
        none_backend = get_backend(cfg)
        if none_backend.isolation:
            problems.append("[none] claims isolation")
        if os.path.normcase(none_backend.binding()) != os.path.normcase(project):
            problems.append("[none] binding() is not the project dir")
        if os.path.normcase(none_backend.in_sandbox_path(project)) != os.path.normcase(
            os.path.abspath(project)
        ):
            problems.append("[none] in_sandbox_path is not identity")
        out = os.path.join(root, "artifacts")
        os.makedirs(out)
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        if not none_backend.fetch_file(out, "result.json", os.path.join(root, "r.json")):
            problems.append("[none] fetch_file failed on a real file")
        none_backend.clear_dir(out)
        if os.path.exists(out):
            problems.append("[none] clear_dir left the directory behind")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    return (not problems, "; ".join(problems))


# Two report shapes. The thin one is what the pipeline actually produced for a
# while: a screenful of counts. The rich one follows the outline in PROMPT.md.
THIN_REPORT = """# vecRecall - Test Run Report

## Stack

- Python 3.14.4, pip 25.1.1, no pytest.

## Aggregated counts

| Metric | Value |
| --- | --- |
| Total | 230 |
| Passed | 230 |
| Failed | 0 |

## What failed

Nothing.
"""

RICH_REPORT = """# vecRecall - Test Run Report

Date: 2026-10-26
Project: `C:/ai-arsenal/workspaces/vecRecall`
Commit: `7f3a2c9`

## 1. Overview

VecRecall is a cognitive memory system rebuilt from MemPalace around one rule:
retrieval is pure vector search and never filters by structure. The core
principles, as the source states them:

- retrieval goes through the vector path only; `wing`/`topic` never narrow a query
- the Wing/Topic/Diary organisation layer exists for UI browsing and nothing else
- the AAAK compressed summary is written to the SQLite UI layer, never indexed
- four memory tiers are kept: L0 identity, L1 key moments, L2 semantic trigger,
  L3 deep retrieval, with L2 firing at a 0.55 similarity threshold

v2.0 adds a cognitive layer (entity/relation/summary extraction on write,
semantic de-duplication), a temporal knowledge graph, forgetting-curve
distillation, hybrid vector+graph retrieval, at-rest encryption, subword
lexical embedding, and an optional hash chain.

## 2. Stack

| Item | Value |
| --- | --- |
| Language | Python |
| Declared | `requires-python >= 3.10` |
| Observed | 3.14.4 (`/usr/bin/python3`) |
| Packaging | setuptools + wheel (PEP 517), package `vecrecall` |
| Test runner | project-bundled scripts, not pytest |

## 3. Module layout

```
core/engine.py          1349 lines  main engine + vector/embedding backends
core/extractor.py        260 lines  cognitive extractor (heuristic / LLM)
core/temporal_graph.py   344 lines  temporal knowledge graph over SQLite
blockchain/chain.py      456 lines  hash chain storage
api/server.py            373 lines  REST service with auth and CORS
```

## 4. Dependencies

| Package | Kind | Status | Version |
| --- | --- | --- | --- |
| (core) | required | none by design | - |
| cryptography | extra `crypto` | installed | 50.0.2 |
| chromadb | extra `chroma` | not installed | - |
| sentence-transformers | extra `sentence` | not installed | - |

## 5. Test results

### `tests/test_core.py`

Command: `python3 tests/test_core.py` - result 158/158, exit 0.

Covers:

- write and retrieval on the vector path
- no structural filtering on retrieval (`wing`/`topic` never narrow a query)
- AAAK `ui_summary` staying out of the vector index
- the four memory tiers and the L2 similarity trigger at 0.55
- graph round-trip over SQLite, batched writes, per-agent diary isolation
- subword embedding variants (Chinese unigram/bigram/trigram, English 3/4-gram)
- lifecycle: update, delete, prune, reindex, hybrid scoring
- hash-chain integration and the embedding-backend factory
- REST auth and MCP path isolation
- cognitive extraction, including the LLM failure fallback

### `tests/test_blockchain.py`

Command: `python3 tests/test_blockchain.py` - result 72/72, exit 0.

Covers:

- block hashing, serialisation, tamper detection
- chain append and verification
- Chinese/English keyword extraction and lookup by keyword or date
- automatic group promotion (4 small blocks to 1 L1)
- the three platform hooks (OpenClaw / Hermes / Claude Code)
- multi-wing isolation, stats and date indexing

## 6. Aggregated counts

| Metric | Value |
| --- | --- |
| Total | 230 |
| Passed | 230 |
| Failed | 0 |
| Skipped | 0 |

## 7. Benchmarks

`python3 benchmarks/benchmark_recall.py` on the bundled 21-query / 15-document
set, zero-dependency lexical embedding:

| Backend | R@5 | MRR | README claims |
| --- | --- | --- | --- |
| `bow` (lexical, no dependencies) | 76.2% | 0.635 | 76.2% |
| `hash` | not run | not run | not quoted |

The measured recall matches the README exactly. The semantic backends were not
benchmarked because their dependencies are absent.

## 8. Not covered / risks

- ChromaDB and sentence-transformers backends were never exercised; only the
  zero-dependency backends ran.
- The REST and MCP servers were covered by unit-level assertions, not by a live
  client hitting a socket.
- No concurrency test exists, so behaviour under parallel writers is unknown.

## 9. Conclusion

- **Verified**: the zero-dependency core and the blockchain layer behave as
  documented, on Python 3.14.4, across 230 assertions with no failures. The
  benchmark figure quoted in the README reproduces exactly.
- **Not verified**: the ChromaDB and sentence-transformers backends, the REST and
  MCP servers under a live client, and concurrent writers.
- **Before adopting**: run the ChromaDB path against your own corpus, and decide
  whether the absence of concurrency tests is acceptable for your write pattern.
"""

SCENARIOS = [
    {
        "name": "stale result rejected",
        "setup": lambda s: (
            open(os.path.join(s, "nonce_mode"), "w").write("wrong"),
            stage(s, make_result(), 0, b""),
        ),
        "expect": 1,
        "why": "a result.json left by an earlier run must never count as this run's verdict",
    },
    {
        "name": "run_id omitted rejected",
        "setup": lambda s: (
            open(os.path.join(s, "nonce_mode"), "w").write("omit"),
            stage(s, make_result(), 0, b""),
        ),
        "expect": 1,
        "why": "a result that cannot prove which run wrote it proves nothing",
    },
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
        "name": "manifest comparable",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "every run owes a manifest, and --compare must tell same from different",
        "check": check_manifest_comparable,
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
        "name": "home from layout",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "the root must be read off <home>\\skills\\<skill>\\, not hard-coded",
        "check": check_layout_home,
    },
    {
        "name": "run dirs unique",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "a reused run directory means the verdict comes from a previous run",
        "check": check_run_dir_unique,
    },
    {
        "name": "prompt fully rendered",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "an unsubstituted {{PLACEHOLDER}} sends the agent to a literal path",
        "check": check_prompt_rendered,
    },
    {
        "name": "thin report flagged",
        "setup": lambda s: stage(s, make_result(), 0, b"", report=THIN_REPORT),
        "expect": 0,
        "why": "a counts-only report must be called out, but not fail the gate",
        "check": check_thin_report,
    },
    {
        "name": "full report accepted",
        "setup": lambda s: stage(s, make_result(), 0, b"", report=RICH_REPORT),
        "expect": 0,
        "why": "a report following the outline must pass the shape check",
        "check": check_rich_report,
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
    {
        "name": "self-cert: commit mismatch",
        "setup": lambda s: stage(
            s, make_result(), 0, b"0" * 40 + b"\n"
        ),
        "expect": lambda: 2 if HOST_HEAD else 0,
        "why": "no metadata, and the instance's HEAD differs from the host's - "
        "a different tree",
    },
    {
        "name": "self-cert: commit match",
        "setup": lambda s: stage(
            s, make_result(), 0, (HOST_HEAD + "\n").encode()
        ),
        "expect": 0,
        "why": "no metadata, but the HEADs agree - the instance holds this tree",
    },
    {
        "name": "docker binding mismatch",
        "setup": lambda s: _stage_docker(DOCKER["state"], workspace="C:/somewhere/else"),
        "expect": 2,
        "why": "a container labelled for another project must not test this one",
        "config": {
            "backend": "docker",
            "docker_bin": lambda cfg: [sys.executable, DOCKER["fake"]],
            "docker_image": "test/agent-image",
            "workspace_mode": "bind",
            "i_accept_bind_mount_risk": True,
        },
    },
    {
        "name": "none backend refused",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 2,
        "why": "no isolation is a trust-model change - it demands explicit acceptance",
        "config": {"backend": "none"},
    },
    {
        "name": "none backend accepted",
        "setup": lambda s: stage(s, make_result(), 0, b""),
        "expect": 0,
        "why": "with explicit acceptance, the run proceeds on the host itself",
        "config": {
            "backend": "none",
            "i_accept_no_isolation": True,
            "workspace_mode": "bind",
            "agent_bin": lambda cfg: [sys.executable, NONE["fake_agent"]],
        },
    },
    {
        "name": "standard: false green caught",
        "setup": lambda s: (
            stage(s, make_result(), 0, b""),
            _stage_log(s, "test_core.log", "Ran 230 tests in 1.2s\nFAILED (failures=7)\n"),
        ),
        "expect": 3,
        "why": "raw logs show failures the result claims do not exist",
        "config": {"precision": "standard"},
    },
    {
        "name": "standard: skips without reasons",
        "setup": lambda s: (
            stage(
                s,
                make_result(passed=220, skipped=10),
                0,
                b"",
            ),
            _stage_log(s, "test_core.log", "Ran 220 tests in 1.2s\nOK\n"),
        ),
        "expect": 3,
        "why": "skips with no blocked reasons are unverified, not passed",
        "config": {"precision": "standard"},
    },
    {
        "name": "standard: consistent logs",
        "setup": lambda s: (
            stage(s, make_result(), 0, b""),
            _stage_log(s, "test_core.log", "Ran 230 tests in 1.2s\nOK\n"),
        ),
        "expect": 0,
        "why": "logs that agree with the claim add no suspects",
        "config": {"precision": "standard"},
    },
    {
        "name": "basic ignores logs",
        "setup": lambda s: (
            stage(s, make_result(), 0, b""),
            _stage_log(s, "test_core.log", "Ran 230 tests in 1.2s\nFAILED (failures=7)\n"),
        ),
        "expect": 0,
        "why": "precision=basic keeps the classic gate - no log forensics",
        "config": {"precision": "basic"},
    },
]


def _stage_docker(state, workspace=None, exec_rc=0, exec_output=b""):
    if not os.path.exists(os.path.join(state, "exists")):
        open(os.path.join(state, "exists"), "w").close()
    if workspace is not None:
        with open(os.path.join(state, "workspace"), "w", encoding="utf-8") as fh:
            fh.write(workspace)
    with open(os.path.join(state, "exec.rc"), "w", encoding="utf-8") as fh:
        fh.write(str(exec_rc))
    with open(os.path.join(state, "exec.bin"), "wb") as fh:
        fh.write(exec_output)


def _stage_log(state, name, text):
    """Stage a raw suite log the way the agent would have left it."""
    logs = os.path.join(state, "files", "logs")
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def main():
    root = tempfile.mkdtemp(prefix="arsenal-selftest-")
    previous_key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    previous_home = os.environ.get(runner.HOME_ENV)
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "sk-selftest-not-a-real-key"
    # Pin the root at the scratch directory: otherwise last-run.txt and any
    # stray output land inside the installed skill, which is how the package
    # ended up shipping a last-run.txt pointing at a deleted temp folder.
    os.environ[runner.HOME_ENV] = root
    failures = 0

    try:
        cfg, cfg_path, state = build_workspace(root)
        default_project = cfg["project_dir"]
        base_cfg = dict(cfg)

        with open(os.path.join(root, "fake_docker.py"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_DOCKER)
        DOCKER["fake"] = os.path.join(root, "fake_docker.py")
        DOCKER["state"] = os.path.join(root, "docker-state")
        os.makedirs(os.path.join(DOCKER["state"], "files"), exist_ok=True)
        os.environ["FAKE_DOCKER_STATE"] = DOCKER["state"]

        with open(os.path.join(root, "fake_agent.py"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_AGENT)
        NONE["fake_agent"] = os.path.join(root, "fake_agent.py")

        print("arsenal self-test")
        print("workspace: %s" % root)
        print("")
        print("%-24s %-8s %-8s %s" % ("scenario", "expect", "actual", "result"))
        print("-" * 72)

        for scenario in SCENARIOS:
            reset_state(state)
            scenario["setup"](state)

            cfg = dict(base_cfg)
            cfg["expect_commit"] = scenario.get("expect_commit")
            override = scenario.get("project_dir")
            cfg["project_dir"] = override(cfg) if callable(override) else default_project
            for key, value in (scenario.get("config") or {}).items():
                cfg[key] = value(cfg) if callable(value) else value

            with open(cfg_path, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2)

            code, output, run_dir = run_runner(cfg_path)
            expect = scenario["expect"]() if callable(scenario["expect"]) else scenario["expect"]
            ok = code == expect
            detail = ""
            if ok and scenario.get("check"):
                ok, detail = scenario["check"](run_dir)
            if not ok:
                failures += 1
            print(
                "%-24s %-8s %-8s %s"
                % (scenario["name"], expect, code, "ok" if ok else "MISMATCH")
            )
            if not ok:
                if detail:
                    print("    %s" % detail)
                print("    expected %d because %s" % (scenario["expect"], scenario["why"]))
                for line in output.strip().splitlines()[-8:]:
                    print("    | %s" % line)

        print("-" * 72)
        contract_ok, contract_detail = run_backend_contracts(
            {
                "sbx": ("sbx_bin", os.path.join(root, "fake_sbx.py"), "FAKE_SBX_STATE"),
                "docker": ("docker_bin", DOCKER["fake"], "FAKE_DOCKER_STATE"),
            }
        )
        if not contract_ok:
            failures += 1
            print("%-24s %-8s %-8s %s" % ("backend contracts", "pass", "fail", "MISMATCH"))
            for line in contract_detail.split("; "):
                print("    %s" % line)
        else:
            print("%-24s %-8s %-8s %s" % ("backend contracts", "pass", "pass", "ok"))

        life_ok, life_detail = run_lifecycle_checks(base_cfg, cfg_path, state)
        if not life_ok:
            failures += 1
            print("%-24s %-8s %-8s %s" % ("resume/profile", "pass", "fail", "MISMATCH"))
            for line in life_detail.split("; "):
                print("    %s" % line)
        else:
            print("%-24s %-8s %-8s %s" % ("resume/profile", "pass", "pass", "ok"))

        ep_ok, ep_detail = run_endpoint_checks()
        if not ep_ok:
            failures += 1
            print("%-24s %-8s %-8s %s" % ("endpoint config", "pass", "fail", "MISMATCH"))
            for line in ep_detail.split("; "):
                print("    %s" % line)
        else:
            print("%-24s %-8s %-8s %s" % ("endpoint config", "pass", "pass", "ok"))
        print("-" * 72)
        if failures:
            print("%d scenario(s) MISMATCH - the gate is not trustworthy." % failures)
            return 1
        print(
            "all %d scenarios + backend contracts ok - the gate fails closed."
            % len(SCENARIOS)
        )
        return 0
    finally:
        if previous_key is None:
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        else:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = previous_key
        if previous_home is None:
            os.environ.pop(runner.HOME_ENV, None)
        else:
            os.environ[runner.HOME_ENV] = previous_home
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
