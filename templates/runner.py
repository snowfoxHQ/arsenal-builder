#!/usr/bin/env python3
"""Arsenal runner: drive the sandbox, pull the result back, own the verdict.

Flow
  1. preflight    - sbx present, API key present, config sane
  2. ensure       - sandbox exists (create with --clone when missing)
  3. execute      - run Claude inside the sandbox on the project
  4. fetch        - sbx cp the structured result OUT of the sandbox
  5. validate     - deterministic contract check (fail-closed)
  6. exit code    - 0 pass / 1 fail / 2 setup error

The sandbox never writes to a host directory. It writes to its own /tmp, and
the host pulls the file back. That keeps the untrusted side on its own side of
the boundary.

Exit codes are the only signal that matters downstream. Prose is not parsed.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

from arsenal_contract import Verdict, load_json, validate
from backends import BackendError, BackendUnavailable, get_backend
from logparse import cross_check, parse_test_logs

DEFAULT_CONFIG = {
    # Which sandbox backend drives the run. "sbx" is the reference
    # implementation (see backends/); the runner itself only knows the
    # five-capability contract, never a vendor name.
    "backend": "sbx",
    "agent_bin": "claude",
    "sandbox": "test-sandbox",
    # Deliberately empty: there is no sensible default for "the project to test".
    # preflight() refuses to run until this is set to a real git repository root.
    "project_dir": None,
    "workspace_mode": "clone",
    "i_accept_bind_mount_risk": False,
    "sandbox_out_dir": "/tmp/arsenal-out",
    "result_name": "result.json",
    "report_name": "report.md",
    "expect_commit": None,
    "sbx_bin": "sbx",
    "prompt_file": "PROMPT.md",
    # The agent writes the report in this language. Kept in config because a
    # report nobody can read is the same as no report; the gate never parses it,
    # so switching it cannot change a verdict.
    # basic    - the classic gate: counts self-consistent, no failures, commit
    #            matches, result fresh. Nothing else is inspected.
    # standard - additionally cross-checks the claim against the raw suite
    #            logs the run pulled back (host-side parsing, not agent prose).
    #            Contradictions become suspects: the run is BLOCKED, not PASS.
    # strict   - the claim must survive the evidence: suspects FAIL the run,
    #            and a positive claim without parseable suite logs fails too.
    #            For projects whose harness writes an unparseable format this
    #            is by design unusable - that is what the tier means.
    "precision": "basic",
    # Vendor endpoint for the agent (and review.py). Switching providers is a
    # config edit on purpose - see BASE_URL above. agent_model, when set, is
    # passed to the agent CLI as --model; None keeps the CLI's own default.
    "anthropic_base_url": "https://api.deepseek.com/anthropic",
    "agent_model": None,
    "report_language": "简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）",
    # full  - all nine sections of the outline in PROMPT.md (the default, because
    #         a report is written for a person deciding whether to adopt a project)
    # brief - counts and blockers only, for CI runs where nobody reads it
    "report_depth": "full",
    "out_dir": "out",
    "recreate": False,
}

# How much of the report outline to demand, per depth setting. Rendered into
# {{REPORT_DEPTH}} so PROMPT.md stays one file for both modes.
REPORT_DEPTH_TEXT = {
    "full": (
        "Report depth: **full**. Write all nine sections. Do not compress two "
        "sections into one."
    ),
    "brief": (
        "Report depth: **brief**. This run only needs sections 1 (Overview), "
        "5 (Test results), 6 (Counts) and 8 (Not covered). Omit 2, 3, 4, 7 and 9 "
        "- but do not drop 8: an unexamined gap is the one thing a reader cannot "
        "recover from a count."
    ),
}

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SETUP = 2
EXIT_BLOCKED = 3

# Kept in step with the root VERSION.txt; run_manifest.json records it so two
# runs can be told apart by tooling, not by humans reading dates.
RUNNER_VERSION = "3.5.0"

# Config keys that describe one run rather than the environment it ran in.
# Excluded from the fingerprint - they differ between runs on purpose.
VOLATILE_CONFIG_KEYS = ("run_id", "recreate", "home", "config_path")

KEY_ENV = "ANTHROPIC_AUTH_TOKEN"
# Default vendor endpoint. Configurable via "anthropic_base_url" - switching
# providers must be a config edit, not a code edit; two copies of this value
# (runner + review) is exactly the duplicated-family trap the README warns about.
BASE_URL = "https://api.deepseek.com/anthropic"


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Layout: the .py files live in templates/, while PROMPT.md sits in the package
# root one level up. That root is the SKILL directory - code and the prompt, and
# nothing else.
if os.path.basename(SCRIPT_DIR).lower() == "templates":
    BASE_DIR = os.path.dirname(SCRIPT_DIR)
else:
    BASE_DIR = SCRIPT_DIR

HOME_ENV = "ARSENAL_HOME"


def log(message):
    print("[arsenal] %s" % message, flush=True)


def home_from_layout(base=None):
    """Infer the arsenal home from where this skill physically sits.

    A deployed skill lives at <home>\\skills\\arsenal-builder\\, so the home is
    two levels up. Reading the root off the directory layout means the pipeline
    works on a fresh machine with no environment variable set and no absolute
    path baked into any file: move the whole folder, and it still resolves.

    Returns None when the skill is not inside a skills\\ directory (someone
    unzipped it into a random folder to try it out).
    """
    here = os.path.normpath(base or BASE_DIR)
    parent = os.path.dirname(here)
    if os.path.basename(parent).lower() == "skills":
        return os.path.dirname(parent)
    return None


def resolve_home(cli_root=None):
    """The one root that holds the project, the skill and the reports.

    Two directories matter and they are not the same directory:
      home  - data: the project under test and the report archive
      base  - code: this skill, its PROMPT.md, its config

    Keeping them separate is what stops a reinstall (unzip a new version over
    the old one) from deleting a month of test reports. Precedence is
    --root, then $ARSENAL_HOME, then the layout (<home>\\skills\\<skill>\\),
    then the skill directory itself for people who just unzipped it somewhere.
    """
    for candidate in (cli_root, os.environ.get(HOME_ENV)):
        if candidate:
            return os.path.abspath(candidate)
    return home_from_layout() or BASE_DIR


def resolve_from_base(path):
    """Code-side paths (the prompt) are relative to the skill directory."""
    if not path:
        return path
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(BASE_DIR, path))


def resolve_from_home(path, home):
    """Data-side paths (project, reports) are relative to the arsenal root."""
    if not path:
        return path
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(home, path))


def save_config(config, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def preflight(config):
    """Return a list of fatal problems. Empty list means go."""
    problems = []

    try:
        backend = get_backend(config)
    except BackendError as exc:
        problems.append(str(exc))
        return problems

    try:
        backend.probe()
    except BackendError as exc:
        problems.append(str(exc))

    if not os.environ.get(KEY_ENV):
        problems.append(
            "%s is not set on the host. Set it as a User environment variable." % KEY_ENV
        )

    mode = str(config["workspace_mode"]).lower()
    if mode not in ("clone", "bind"):
        problems.append("workspace_mode must be 'clone' or 'bind', got %r" % mode)
    if backend.isolation and mode == "bind" and not config.get("i_accept_bind_mount_risk"):
        problems.append(
            "workspace_mode=bind exposes the host directory read-write to the agent. "
            "Set \"i_accept_bind_mount_risk\": true in the config to confirm."
        )
    if not backend.isolation and not config.get("i_accept_no_isolation"):
        problems.append(
            'backend "%s" runs the agent directly on this machine - no sandbox, no '
            "isolation: the agent can do anything this user can. Set "
            '"i_accept_no_isolation": true in the config to accept that explicitly.'
            % backend.name
        )

    project_dir = config.get("project_dir")
    if not project_dir:
        problems.append(
            "project_dir is not set. Copy config.example.json to config.json and point "
            "project_dir at the root of the repository you want tested."
        )
    elif not os.path.isdir(project_dir):
        problems.append("project_dir does not exist: %s" % project_dir)
    elif (
        backend.isolation
        and mode == "clone"
        and not os.path.exists(os.path.join(project_dir, ".git"))
    ):
        problems.append(
            "workspace_mode=clone needs a git repository, but %s has no .git. "
            "Set workspace_mode to bind (and accept the read-write mount), or make "
            "that directory a git repo first." % project_dir
        )

    if not os.path.isfile(config["prompt_file"]):
        problems.append("prompt file not found: %s" % config["prompt_file"])

    # An existing sandbox is bound to one host directory for life, so a sandbox
    # cloned from a different project would test the wrong tree - and every
    # number in the report would be real, just not about this project. Check it
    # here, which means --dry-run catches it too, instead of finding out from a
    # report nobody questions. When the backend cannot report a binding, the
    # check self-certifies (commit comparison for clone, nonce file for bind).
    if backend.isolation and project_dir and backend.exists():
        try:
            status, detail = check_binding(backend, config)
        except BackendError as exc:
            status, detail = "unknown", str(exc)
        if status == "mismatch":
            problems.append(
                "sandbox %r belongs to a different tree (binding check got %r, "
                "expected project_dir %s). A sandbox tests one directory for "
                "life - run with --recreate to rebuild it."
                % (config["sandbox"], detail, project_dir)
            )
        elif status == "unknown":
            log(
                "warning: could not verify which directory sandbox %r is bound to "
                "(no metadata, self-check inconclusive)" % config["sandbox"]
            )

    return problems


def same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def _git_head(project):
    try:
        result = subprocess.run(
            ["git", "-C", project, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def binding_by_commit(backend, project):
    """Self-certify a clone-style instance when it reports no binding.

    Metadata is not the only evidence: if the instance were cloned from this
    project, its HEAD and the host's HEAD agree. Two different hashes mean the
    instance holds a different tree - which is the wrong-tree accident this
    whole check exists to prevent.
    """
    from arsenal_contract import same_commit

    host_head = _git_head(project)
    if not host_head:
        return "unknown", None
    rc, out = backend.exec_capture(
        ["git", "-C", backend.in_sandbox_path(project), "rev-parse", "HEAD"]
    )
    sandbox_head = (out or "").strip() if rc == 0 else ""
    if not sandbox_head or " " in sandbox_head or len(sandbox_head) > 100:
        return "unknown", None
    if same_commit(host_head, sandbox_head):
        return "match", host_head
    return "mismatch", sandbox_head


def binding_by_nonce(backend, project):
    """Self-certify a bind-style instance with a one-off file.

    Drop a nonce into the project and ask the instance to read it back. This
    verifies the file system the instance actually sees, not what it claims -
    a stronger check than any metadata, and it works on every backend that can
    execute a command.
    """
    nonce = "arsenal-nonce-%d-%s" % (os.getpid(), time.strftime("%H%M%S"))
    marker = os.path.join(project, ".arsenal-nonce")
    try:
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(nonce)
    except OSError:
        return "unknown", None
    try:
        rc, out = backend.exec_capture(["cat", backend.in_sandbox_path(marker)])
    finally:
        try:
            os.unlink(marker)
        except OSError:
            pass
    if rc != 0:
        return "unknown", None
    got = (out or "").strip()
    if got == nonce:
        return "match", nonce
    return "mismatch", got


def check_binding(backend, config):
    """(status, detail) with status in 'match' | 'mismatch' | 'unknown'.

    Metadata first (the backend's own binding report); when a backend cannot
    report one, fall back to self-certification: commit comparison for clone
    mode, nonce file for bind mode. 'unknown' never blocks a run - it warns,
    because silence would read as "checked and fine".
    """
    project = config.get("project_dir")
    attached = backend.binding()
    if attached:
        if same_path(attached, project):
            return "match", attached
        return "mismatch", attached
    mode = str(config["workspace_mode"]).lower()
    if mode == "clone":
        return binding_by_commit(backend, project)
    return binding_by_nonce(backend, project)


def agent_stage_done(run_dir, config, manifest_sha, run_id):
    """True when the agent stage already completed against this environment.

    All three must hold, or the agent runs again: a stage record written only
    after a clean agent exit, with the same manifest hash; the artifacts
    already pulled back to the host; and a result carrying this run's nonce.
    """
    record = load_json(os.path.join(run_dir, "stages", "agent.json"))
    if not isinstance(record, dict):
        return False
    if record.get("manifest_hash") != manifest_sha:
        return False
    if record.get("exit_code") != 0:
        return False
    result_path = find_artifact(run_dir, config["result_name"])
    data = load_json(result_path) if os.path.isfile(result_path) else None
    return isinstance(data, dict) and data.get("run_id") == run_id


def write_env_file(config):
    """Write the key to a 0600 temp file so it never reaches argv."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", prefix="arsenal-env-", suffix=".env", delete=False, encoding="utf-8"
    )
    try:
        handle.write("%s=%s\n" % (KEY_ENV, os.environ[KEY_ENV]))
        handle.write("ANTHROPIC_BASE_URL=%s\n" % (config.get("anthropic_base_url") or BASE_URL))
    finally:
        handle.close()
    try:
        os.chmod(handle.name, 0o600)
    except OSError:
        pass
    return handle.name


def build_prompt(config, backend):
    with open(config["prompt_file"], "r", encoding="utf-8") as handle:
        template = handle.read()

    if not getattr(backend, "isolation", True):
        # The prompt's first line claims an isolated sandbox. On a backend with
        # no isolation that claim is false, and an agent that believes it might
        # roam. Say so, before the hard rules.
        template += (
            "\n\n## Isolation notice\n\n"
            "This run has **no sandbox isolation**: you are on the operator's "
            "machine. The hard rules above apply doubly - do not read, write or "
            "inspect anything outside `{{PROJECT_PATH}}` and `{{OUT_DIR}}`.\n"
        )

    project_in_sandbox = backend.in_sandbox_path(config["project_dir"])
    out_dir = backend.artifact_dir

    return (
        template.replace("{{PROJECT_PATH}}", project_in_sandbox)
        .replace("{{OUT_DIR}}", out_dir)
        .replace("{{RUN_ID}}", str(config.get("run_id") or ""))
        .replace("{{RESULT_NAME}}", config["result_name"])
        .replace("{{REPORT_NAME}}", config["report_name"])
        .replace("{{RESULT_PATH}}", "%s/%s" % (out_dir, config["result_name"]))
        .replace("{{REPORT_PATH}}", "%s/%s" % (out_dir, config["report_name"]))
        .replace("{{REPORT_LANGUAGE}}", config.get("report_language") or "英文")
        .replace("{{REPORT_DEPTH}}", depth_text(config.get("report_depth")))
    )


def depth_text(depth):
    """Render {{REPORT_DEPTH}}. An unknown value falls back to full, never to brief:
    a report that is too long wastes a minute, one that is too thin wastes a run."""
    return REPORT_DEPTH_TEXT.get(str(depth or "").strip().lower(), REPORT_DEPTH_TEXT["full"])


def run_agent_command(config, prompt):
    """The agent invocation, identical for every backend: the policy lives
    here, the mechanics of executing it live in the backend. agent_bin exists
    so tests (and unusual installs) can point at a different CLI."""
    agent = config.get("agent_bin") or "claude"
    prefix = list(agent) if isinstance(agent, (list, tuple)) else [agent]
    command = prefix + ["-p", prompt, "--dangerously-skip-permissions"]
    model = str(config.get("agent_model") or "").strip()
    if model:
        command += ["--model", model]
    return command


def find_artifact(run_dir, name):
    """Locate a file the agent wrote, wherever the directory copy landed it."""
    direct = os.path.join(run_dir, name)
    if os.path.isfile(direct):
        return direct
    for root, _dirs, files in os.walk(run_dir):
        if name in files:
            return os.path.join(root, name)
    return direct


def fetch_outputs(config, run_dir, backend):
    """Pull the whole sandbox output directory back to the host.

    Copying the directory (instead of two hard-coded filenames) is the point:
    whatever the agent left behind - raw test logs, coverage, environment
    dumps - comes home automatically, and nothing is silently dropped because
    the host was not told its name in advance.
    """
    out_dir = backend.artifact_dir
    local_dir = os.path.join(run_dir, "sandbox")
    os.makedirs(local_dir, exist_ok=True)

    if not backend.fetch_dir(out_dir, local_dir):
        log("directory copy failed, falling back to single-file fetch")
        for name in (config["result_name"], config["report_name"]):
            backend.fetch_file(out_dir, name, os.path.join(run_dir, name))

    got_result = os.path.isfile(find_artifact(run_dir, config["result_name"]))
    got_report = os.path.isfile(find_artifact(run_dir, config["report_name"]))
    return got_result, got_report


def describe_artifacts(run_dir, limit=40):
    """One line per file kept on the host, so the run never ends in silence.

    Must not raise. It runs after the verdict is already printed, so an
    exception here turns a PASS into exit code 1 and skips writing summary.json
    entirely - the exact "it finished but left nothing behind" failure this
    whole function exists to prevent. A copied-back venv carrying a dangling
    `lib64` symlink was enough to do it on Windows.
    """
    rows = []
    skipped = 0
    for root, dirs, files in os.walk(run_dir, onerror=lambda _e: None):
        # Symlinked directories can point anywhere, including at targets that do
        # not exist on this host; walking them is how getsize() started raising.
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                rel = os.path.relpath(full, run_dir).replace("\\", "/")
                size = os.path.getsize(full)
            except OSError:
                skipped += 1
                continue
            rows.append((rel, size))
    if len(rows) > limit:
        skipped += len(rows) - limit
        rows = rows[:limit]
    if skipped:
        rows.append(("... %d more not listed" % skipped, 0))
    return rows


# What a usable report looks like, per depth. Crude on purpose: counting
# sections, table rows and bullets catches the one regression this exists for -
# a report that kept its headings but lost everything under them.
REPORT_SHAPE = {
    "full": {"sections": 6, "bytes": 4000, "table_rows": 12, "bullets": 15},
    "brief": {"sections": 3, "bytes": 600, "table_rows": 0, "bullets": 0},
}


def audit_report(report_path, depth):
    """Check the report is something a person could act on.

    This never touches the verdict by default: the gate is about test truth, and
    letting prose quality flip an exit code would make pass/fail unpredictable.
    It exists because the report once quietly shrank from 174 lines to 51 - the
    tests still passed, so nothing anywhere complained, and the only signal was
    a human opening the file and finding counts and nothing else.

    Measured against the two real reports this pipeline produced: the good one
    scores 8194 B / 27 table rows / 13 headings, the thin one 2001 B / 6 / 5.
    The thresholds sit between them, closer to the thin side, because a false
    warning costs one line of output while a missed one costs a whole run.
    """
    if not os.path.isfile(report_path):
        return ["no report was produced"]

    shape = REPORT_SHAPE.get(str(depth or "").strip().lower(), REPORT_SHAPE["full"])
    try:
        with open(report_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return ["report could not be read: %s" % exc]

    lines = text.splitlines()
    problems = []
    sections = sum(1 for line in lines if line.lstrip().startswith("## "))
    table_rows = sum(1 for line in lines if line.lstrip().startswith("|"))
    bullets = sum(
        1 for line in lines if line.lstrip().startswith(("- ", "* "))
    )
    size = len(text.encode("utf-8"))

    if sections < shape["sections"]:
        problems.append(
            "%d section(s), outline asks for %d" % (sections, shape["sections"])
        )
    if size < shape["bytes"]:
        problems.append(
            "%d B of prose, too little to explain anything (%d B expected)"
            % (size, shape["bytes"])
        )
    if table_rows < shape["table_rows"]:
        problems.append(
            "%d table row(s), expected at least %d - stack, dependencies and "
            "counts all belong in tables" % (table_rows, shape["table_rows"])
        )
    if bullets < shape["bullets"]:
        problems.append(
            "%d bullet(s) of coverage and risk, expected at least %d"
            % (bullets, shape["bullets"])
        )
    return problems


def make_run_dir(out_dir, when=None):
    """Allocate a run directory that cannot collide with an earlier run.

    Timestamp to the second is not unique: two runs started in the same second
    shared a directory, and the later one silently inherited the earlier one's
    result.json - a verdict about a test that was never run. Suffix instead.
    """
    stamp = (when or time.strftime("%Y%m%d-%H%M%S")).replace(":", "-")
    run_id = stamp
    run_dir = os.path.join(out_dir, run_id)
    suffix = 2
    while os.path.exists(run_dir):
        run_id = "%s-%d" % (stamp, suffix)
        run_dir = os.path.join(out_dir, run_id)
        suffix += 1
    os.makedirs(run_dir, exist_ok=False)
    return run_id, run_dir


def build_manifest(config, backend):
    """The environment facts that decide whether two runs are comparable.

    README says the dependency table is what makes two runs comparable - but a
    table written by the agent is prose this host never parsed. The manifest is
    the machine answer: platform, tooling versions, the tested commit, the
    config fingerprint and the prompt that was sent. Two runs whose manifests
    differ are two different experiments, whatever their counts claim.
    """
    import hashlib
    import platform

    fingerprint = {
        key: str(value)
        for key, value in sorted(config.items())
        if key not in VOLATILE_CONFIG_KEYS and not isinstance(value, (dict, list))
    }
    try:
        with open(config["prompt_file"], "rb") as handle:
            prompt_sha = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        prompt_sha = "unreadable"

    return {
        "arsenal_version": RUNNER_VERSION,
        "platform": platform.platform(),
        "host_python": sys.version.split()[0],
        "backend": backend.name,
        "backend_version": backend.version(),
        "project_commit": _git_head(config.get("project_dir") or ""),
        "prompt_sha256": prompt_sha,
        "config_fingerprint": hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    }


def manifest_hash(manifest):
    import hashlib

    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def compare_manifests(dir_a, dir_b):
    """Print whether two run directories are comparable, and why not.

    Exit 0 = comparable, 1 = differences found, 2 = a manifest is missing or
    unreadable. The diff is the answer, not prose around it.
    """
    manifests = []
    for run_dir in (dir_a, dir_b):
        path = os.path.join(run_dir, "run_manifest.json")
        data = load_json(path)
        if not isinstance(data, dict):
            log("no readable run_manifest.json in %s" % run_dir)
            return EXIT_SETUP
        manifests.append(data)

    differences = {
        key: (manifests[0].get(key), manifests[1].get(key))
        for key in sorted(set(manifests[0]) | set(manifests[1]))
        if manifests[0].get(key) != manifests[1].get(key)
    }
    if differences:
        print("comparable: false", flush=True)
        for key, (old, new) in differences.items():
            print("  %-18s %s -> %s" % (key, old, new), flush=True)
        return EXIT_FAIL
    print("comparable: true (%s vs %s)" % (dir_a, dir_b), flush=True)
    return EXIT_PASS


def deep_merge(base, overlay):
    """Overlay wins; dicts merge recursively so a profile only names diffs."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_profile(home, name):
    """Load <home>/configs/<name>.json. Returns (profile dict, error string)."""
    filename = name if name.endswith(".json") else name + ".json"
    path = os.path.join(home, "configs", filename)
    data = load_json(path)
    if data is None:
        return None, "profile not found or not valid JSON: %s" % path
    if not isinstance(data, dict):
        return None, "profile must be a JSON object: %s" % path
    return data, ""


def main(argv=None):
    # A GBK console cannot encode the "✓" that sbx prints during create; on
    # 2026-11-05 that UnicodeEncodeError aborted a run the CLI had completed.
    # Degrade the glyph, never the run.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 - exotic streams: keep going regardless
        pass

    parser = argparse.ArgumentParser(description="Arsenal test pipeline runner")
    parser.add_argument("--config", default="config.json")
    parser.add_argument(
        "--root",
        default=None,
        help="arsenal home: the directory that holds the project and the reports "
        "(default: $%s, else the skill directory)" % HOME_ENV,
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="deep-merge <home>/configs/<name>.json over config.json - a profile "
        "names only the fields that differ (report_depth, backend, ...)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RUN_DIR",
        help="continue an earlier run directory: reuse it, skip the agent when "
        "its stage already completed against the same manifest, rerun it "
        "otherwise. Refuses when the environment manifest has changed.",
    )
    parser.add_argument("--recreate", action="store_true", help="rebuild the sandbox first")
    parser.add_argument("--dry-run", action="store_true", help="preflight only")
    parser.add_argument(
        "--collect",
        action="store_true",
        help="skip the agent and just pull back what the sandbox already produced",
    )
    parser.add_argument(
        "--print-home",
        action="store_true",
        help="print the resolved arsenal home and exit (used by the launcher)",
    )
    parser.add_argument(
        "--report-depth",
        choices=sorted(REPORT_DEPTH_TEXT),
        default=None,
        help="how much of the report outline to demand (overrides config)",
    )
    parser.add_argument(
        "--strict-report",
        action="store_true",
        help="treat a thin report as a failure instead of a warning",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("RUN_A", "RUN_B"),
        help="compare two run directories' manifests; exit 0 comparable / 1 not",
    )
    args = parser.parse_args(argv)

    if args.compare:
        return compare_manifests(args.compare[0], args.compare[1])

    home = resolve_home(args.root)

    if args.print_home:
        print(home)
        return EXIT_PASS

    config_path = args.config
    if not os.path.isabs(config_path) and not os.path.isfile(config_path):
        # A bare `--config config.json` from another directory should find the
        # root's wiring, not silently fall back to defaults.
        candidate = os.path.join(home, config_path)
        if os.path.isfile(candidate):
            config_path = candidate

    config = dict(DEFAULT_CONFIG)
    if os.path.isfile(config_path):
        loaded = load_json(config_path)
        if loaded is None:
            log("config file is not valid JSON: %s" % config_path)
            return EXIT_SETUP
        config.update(loaded)
        log("loaded config from %s" % config_path)
    else:
        log("no config file at %s, using defaults" % config_path)

    config["home"] = home
    if args.profile:
        profile, error = load_profile(home, args.profile)
        if profile is None:
            log(error)
            return EXIT_SETUP
        config = deep_merge(config, profile)
        log("profile %r merged (%d override(s))" % (args.profile, len(profile)))
    # The prompt is code (it ships with the skill); the project and the report
    # archive are data (they belong to the home). Mixing the two is how a
    # reinstall ends up wiping the reports.
    config["prompt_file"] = resolve_from_base(config["prompt_file"])
    config["out_dir"] = resolve_from_home(config["out_dir"], home)
    if config.get("project_dir"):
        config["project_dir"] = resolve_from_home(config["project_dir"], home)
    log("arsenal home: %s" % home)

    if args.recreate:
        config["recreate"] = True
    if args.report_depth:
        config["report_depth"] = args.report_depth

    problems = preflight(config)
    if problems:
        log("preflight failed:")
        for item in problems:
            print("  - %s" % item, flush=True)
        return EXIT_SETUP

    if args.dry_run:
        log("preflight passed; dry run stops here")
        return EXIT_PASS

    # Preflight already validated the backend; building it again here cannot
    # fail, and both the agent path and artifact recovery go through it.
    backend = get_backend(config)
    # The environment facts of this run, written before anything can fail:
    # even a crashed run leaves behind what it ran ON, which is what makes two
    # run directories comparable (or honestly incomparable).
    manifest = build_manifest(config, backend)
    manifest_sha = manifest_hash(manifest)

    resumed = None
    if args.resume:
        resumed = os.path.abspath(args.resume)
        if not os.path.isdir(resumed):
            log("resume target is not a directory: %s" % resumed)
            return EXIT_SETUP
        old_manifest = load_json(os.path.join(resumed, "run_manifest.json"))
        if not isinstance(old_manifest, dict):
            log("resume target has no readable run_manifest.json: %s" % resumed)
            return EXIT_SETUP
        differences = [
            key
            for key in sorted(set(old_manifest) | set(manifest))
            if old_manifest.get(key) != manifest.get(key)
        ]
        if differences:
            log(
                "refusing to resume %s - the environment changed since it ran: %s"
                % (resumed, ", ".join(differences))
            )
            log("start a fresh run instead; resuming a different experiment "
                "would forge its provenance")
            return EXIT_SETUP
        run_id = os.path.basename(resumed)
        run_dir = resumed
        log("resuming run %s" % run_dir)
    else:
        # A run id must be unique, not just timestamped. Two runs in the same second
        # used to share a directory, so the second one inherited the first one's
        # result.json and reported a verdict about a test it never ran.
        run_id, run_dir = make_run_dir(config["out_dir"])
        log("run directory: %s" % run_dir)
    # The run id doubles as the freshness nonce: it is rendered into the prompt,
    # the agent must copy it into result.json, and the gate rejects a result
    # without the matching id - so output left by an earlier run can never be
    # mistaken for this run's verdict.
    config["run_id"] = run_id

    with open(os.path.join(run_dir, "run_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "run_id": run_id,
                "state": "running",
                "resumed": bool(resumed),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "paths": {
                    "home": config.get("home"),
                    "skill": BASE_DIR,
                    "project": config.get("project_dir"),
                    "reports": config.get("out_dir"),
                    "run": run_dir,
                },
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
        handle.write("\n")

    agent_log = os.path.join(run_dir, "agent.log")
    agent_code = None
    agent_skipped = False
    error = None
    verdict = None
    got_result = got_report = False
    binding_status = "unchecked"

    try:
        if args.collect:
            # Data recovery, not a test run: the agent already finished, the
            # output is still sitting in the sandbox, and re-running it would
            # cost tokens for a result we can simply pick up.
            if not backend.exists():
                raise RuntimeError(
                    "--collect needs an existing sandbox named %r" % config["sandbox"]
                )
            log("collect mode: pulling existing output from %s" % config["sandbox"])
            binding_status, detail = check_binding(backend, config)
            if binding_status == "mismatch":
                log(
                    "warning: this sandbox does not belong to project_dir %s "
                    "(binding check got %r)" % (config["project_dir"], detail)
                )
        else:
            if resumed and agent_stage_done(run_dir, config, manifest_sha, run_id):
                # The whole point of resume: an agent stage that already
                # completed against the same environment must not burn tokens
                # again. The gate below still validates the artifacts fresh.
                log(
                    "agent stage already completed for this manifest - skipping "
                    "the agent, validating the artifacts already on the host"
                )
                agent_code = 0
                agent_skipped = True
            else:
                if config.get("recreate") and backend.exists():
                    backend.remove()

                if not backend.exists():
                    log(
                        "creating sandbox %s (%s mode)"
                        % (config["sandbox"], config["workspace_mode"])
                    )
                    if not backend.create():
                        raise RuntimeError(
                            "sandbox create failed - see the output above"
                        )
                    binding_status = "fresh-instance"
                else:
                    log("reusing existing sandbox %s" % config["sandbox"])
                    # A sandbox is bound to one host directory for its whole life.
                    # Reusing one that was cloned from a different project silently
                    # tests the wrong tree - which is exactly how a run once reported
                    # on sandbox-workspaces/vecRecall instead of the project asked for.
                    binding_status, detail = check_binding(backend, config)
                    if binding_status == "mismatch":
                        raise RuntimeError(
                            "sandbox %r belongs to a different tree (binding check got "
                            "%r), not project_dir %s - rerun with --recreate to rebuild it"
                            % (config["sandbox"], detail, config["project_dir"])
                        )
                    if binding_status == "unknown":
                        log(
                            "warning: could not verify the sandbox binding - continuing, "
                            "the run_id nonce still guarantees freshness"
                        )

                # Cheap half of the freshness guarantee: no leftover output can be
                # fetched back if the folder is empty before the agent starts. The
                # strong half is the run_id nonce, checked by the gate below.
                out_dir = backend.artifact_dir
                if not backend.clear_dir(out_dir):
                    log(
                        "warning: could not clear %s inside the sandbox; the run_id "
                        "nonce still guards against stale output" % out_dir
                    )

                prompt = build_prompt(config, backend)
                env_file = write_env_file(config)
                try:
                    log("executing tests inside %s" % config["sandbox"])
                    agent_code = backend.exec_agent(
                        run_agent_command(config, prompt), env_file, agent_log
                    )
                finally:
                    try:
                        os.unlink(env_file)
                    except OSError:
                        pass

                # The stage record is what makes a later --resume cheap: it is
                # written only after a clean agent exit, and carries the
                # manifest hash so a changed environment can never inherit it.
                stage_dir = os.path.join(run_dir, "stages")
                os.makedirs(stage_dir, exist_ok=True)
                with open(
                    os.path.join(stage_dir, "agent.json"), "w", encoding="utf-8"
                ) as handle:
                    json.dump(
                        {
                            "stage": "agent",
                            "manifest_hash": manifest_sha,
                            "exit_code": agent_code,
                            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        },
                        handle,
                        indent=2,
                    )
                    handle.write("\n")

        got_result, got_report = fetch_outputs(config, run_dir, backend)
        log("result.json: %s" % ("fetched" if got_result else "MISSING"))
        log("report.md:   %s" % ("fetched" if got_report else "MISSING"))

        verdict = validate(
            find_artifact(run_dir, config["result_name"]) if got_result else None,
            config.get("expect_commit")
            # A result whose commit differs from the tree the manifest records
            # is the "tested another copy" incident: 2026-11-05, demo run - a
            # branch switch left the clone-mode sandbox on the old HEAD, the
            # agent honestly tested stale code and the basic tier passed it.
            # result.commit vs manifest.project_commit is a zero-parse check
            # the gate must not skip, so the manifest backstops expect_commit
            # whenever the config does not pin one. --collect is explicit data
            # recovery, not a freshness claim: same exemption as the run_id.
            or (None if args.collect else manifest.get("project_commit")),
            expect_run_id=None if args.collect else run_id,
        )
    except Exception as exc:  # noqa: BLE001 - any crash still owes the host a record
        error = "%s: %s" % (type(exc).__name__, exc)
        log("run aborted - %s" % error)

    if verdict is None:
        verdict = Verdict(
            False,
            ["run aborted before validation: %s" % error]
            if error
            else ["run produced no verdict"],
        )

    if error:
        exit_code = EXIT_SETUP
    elif agent_code not in (0, None):
        exit_code = EXIT_FAIL
    else:
        exit_code = verdict.code

    report_path = find_artifact(run_dir, config["report_name"])
    # When the run itself broke, the reason is already printed above; auditing a
    # report that was never going to exist just adds noise to a loud failure.
    report_problems = (
        [] if error else audit_report(report_path, config.get("report_depth"))
    )
    if report_problems and args.strict_report and exit_code == EXIT_PASS:
        exit_code = EXIT_FAIL

    # precision=standard/strict: cross-check the claim against the raw suite
    # logs the run pulled back. The host parses the logs itself - agent prose
    # is not evidence. standard: contradictions are suspicions, a PASS is
    # demoted to BLOCKED (a parser that misreads an exotic format must not
    # brand an honest run broken). strict: the claim must survive the
    # evidence - a suspect is a failure, and a positive claim without any
    # parseable evidence is one too.
    precision = str(config.get("precision") or "basic").strip().lower()
    suspects = []
    if (
        not error
        and precision in ("standard", "strict")
        and isinstance(verdict.data, dict)
    ):
        suites = parse_test_logs(run_dir)
        suspects = cross_check(suites, verdict.data)
        if (
            precision == "strict"
            and not suspects
            and (verdict.data.get("passed") or 0) > 0
            and not suites
        ):
            suspects.append(
                "strict requires parseable evidence under logs/test*.log, but "
                "no suite log could be parsed - an unevidenced claim fails, "
                "it does not pass"
            )
        if suspects:
            target = verdict.blocked_reasons if precision == "standard" else verdict.reasons
            target.extend(suspects)
            verdict.ok = False
            if exit_code == EXIT_PASS:
                exit_code = verdict.code

    print("", flush=True)
    print("=" * 62, flush=True)
    if error:
        print("VERDICT: ERROR - %s" % error, flush=True)
    elif agent_code not in (0, None):
        print(
            "VERDICT: FAIL - the agent did not finish cleanly (exit %d)" % agent_code,
            flush=True,
        )
    else:
        print("VERDICT: %s" % verdict.summary(), flush=True)

    for reason in verdict.reasons + verdict.blocked_reasons:
        print("  - %s" % reason, flush=True)
    if agent_code not in (0, None):
        print("  - agent exited with code %d" % agent_code, flush=True)
    if agent_code == 0 and not got_report:
        print(
            "  note: no report.md came back; the verdict above is from result.json only",
            flush=True,
        )
    for problem in report_problems:
        print("  report: %s" % problem, flush=True)
    if report_problems and args.strict_report:
        print("  report: --strict-report counted this as a failure", flush=True)
    print("=" * 62, flush=True)

    artifacts = describe_artifacts(run_dir)
    print("", flush=True)
    print("artifacts kept on the host (%s):" % run_dir, flush=True)
    if artifacts:
        for rel, size in artifacts:
            print("  %-28s %7d B" % (rel, size), flush=True)
    else:
        print("  (none - see agent.log for what the agent said)", flush=True)
    print("", flush=True)

    reasons = verdict.reasons + verdict.blocked_reasons
    if agent_code not in (0, None):
        reasons.append("agent exited with code %d" % agent_code)

    summary = {
        "run_id": run_id,
        "sandbox": config["sandbox"],
        "project": config["project_dir"],
        "workspace_mode": config["workspace_mode"],
        # Absolute paths of the three things a run touches, so nobody has to
        # guess which copy of the project or which report directory was used.
        "paths": {
            "home": config.get("home"),
            "skill": BASE_DIR,
            "project": config["project_dir"],
            "reports": config["out_dir"],
            "run": run_dir,
        },
        "agent_exit_code": agent_code,
        "resumed": bool(resumed),
        "agent_skipped": agent_skipped,
        "state": "error" if error else verdict.state,
        "passed": exit_code == EXIT_PASS,
        "exit_code": exit_code,
        "error": error,
        "reasons": reasons,
        "counts": (
            {key: verdict.data.get(key) for key in ("total", "passed", "failed", "skipped")}
            if verdict.data
            else None
        ),
        "result_fetched": got_result,
        "report_fetched": got_report,
        "run_id": run_id,
        # False when --collect skipped the agent: freshness was not proven.
        "run_id_verified": bool(
            not args.collect and verdict.data and verdict.data.get("run_id") == run_id
        ),
        "binding_status": binding_status,
        "binding_verified": binding_status in ("match", "fresh-instance"),
        "backend": config.get("backend") or "sbx",
        "manifest": manifest,
        "manifest_hash": manifest_hash(manifest),
        "precision": config.get("precision") or "basic",
        "suspects": suspects,
        "report_ok": not report_problems,
        "report_depth": config.get("report_depth"),
        "report_problems": report_problems,
        "artifacts": [rel for rel, _size in artifacts],
    }
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    with open(os.path.join(config["out_dir"], "latest.txt"), "w", encoding="utf-8") as handle:
        handle.write(run_dir)
    # A pointer at the home root, so the launcher can find the newest run
    # without knowing where out_dir points or parsing this program's prose.
    home = config.get("home")
    if home:
        with open(os.path.join(home, "last-run.txt"), "w", encoding="utf-8") as handle:
            handle.write(run_dir)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
