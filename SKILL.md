---
name: arsenal-builder
description: Set up or fix an isolated AI test platform on Windows using Docker sbx sandboxes, where the sandbox agent runs tests and the host validates a JSON contract to decide Pass/Fail. Use when asked to build, repair, or audit a sandboxed AI testing pipeline (AI 兵工厂, sbx 沙箱测试, automated test gate).
---

# AI Arsenal Builder

A dedicated test platform on one machine: **DeepSeek Harness (host)** drives
**Docker sbx sandboxes (untrusted)**, a Claude Code agent inside the sandbox
runs the test suite, and the **host decides the verdict**.

The rule that makes this work: *the sandbox produces data, the host produces the
verdict.* Never let the sandbox phrase the answer, and never let prose decide it.

## When to use this skill

- "set up an isolated testing platform", "build an AI arsenal", "配置 sbx 沙箱"
- "my test pipeline always reports success", "the gate never fails"
- Repairing an existing arsenal (this skill's v2 replaces the earlier package)

## Architecture

| Side | Trust | Responsibility |
| --- | --- | --- |
| Host (DSH) | trusted | owns the verdict, reads `summary.json`, acts on the exit code |
| Sandbox (sbx) | untrusted | installs deps, runs suite, writes `result.json` + `report.md` |

Data flows **out by pull**: the agent writes to `/tmp/arsenal-out` inside the
sandbox, the host copies the file back with `sbx cp`. The sandbox never writes
into a host directory.

## Phase 1 — Host prerequisites

1. Docker Desktop, plus the standalone `sbx` CLI installed via MSI. Verify with
   `sbx version`.
2. Put the API key in a **User** environment variable:
   `ANTHROPIC_AUTH_TOKEN` = your DeepSeek key.
3. Do **not** run `sbx setup`. It is experimental, it has no network-policy
   step, and it imports host credentials into the global secret store.
4. Allow exactly one endpoint instead of opening the network:

   ```
   sbx policy init balanced
   sbx policy allow network api.deepseek.com
   sbx policy check network api.deepseek.com
   ```

   The last command must print `Allowed`. If it does not, stop and fix the rule
   — never fall back to allow-all.

   Suites that need to install dependencies will report those cases as
   `blocked` (exit code 3) unless the package host is allowed too:

   ```
   sbx policy allow network pypi.org
   sbx policy allow network files.pythonhosted.org
   ```

   `sbx policy` only exists on the host — there is no `sbx` inside the sandbox,
   so an agent hitting "Approval required" cannot approve anything itself.

## Phase 1.5 - One root (project, platform, reports)

Three things, one root. Never three scattered directories:

```
<ARSENAL_HOME>\                    e.g. C:\ai-arsenal
├── arsenal.cmd                     the only entry point
├── config.json                     wiring: which project, where reports go
├── last-run.txt                    written by the runner, points at the newest run
├── workspaces\vecRecall\           the project, with its own .git
├── skills\arsenal-builder\         this package, unzipped here
├── reports\arsenal\<run_id>\       artifacts of one run
└── archive\                        old versions and old reports, read-only
```

Two resolution rules, and that is the whole contract:

| Path | Resolved against | Example |
| --- | --- | --- |
| `prompt_file` (code) | the **skill** dir `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`, `out_dir` (data) | the **root** `%ARSENAL_HOME%` | `workspaces/vecRecall`, `reports/arsenal` |

The root comes from `--root`, then `$ARSENAL_HOME`, then the package directory
(for a quick standalone try). Set it once as a user environment variable and
every invocation resolves correctly from any cwd.

Why they must be separate: reports written inside the package are deleted the
next time you unzip a new version over the old one. Same reason `config.json`
lives in the root - an upgrade must not overwrite your wiring.

**Sandbox binding check**: an `sbx` sandbox is bound to one host directory for
life. Reusing one that was cloned from a different project silently tests the
wrong tree (this happened here: the report covered
`sandbox-workspaces/vecRecall` instead of the requested `workspaces/vecRecall`).
`runner.py` now reads the real binding from `sbx ls --format json`, and exits 2
when it does not match `project_dir` - rebuild with `--recreate`.

## Phase 2 — Install the scripts

The layout is: **documents, config and the launcher in the package root, all
`.py` scripts in `templates/`**. Unzip it, or copy the contents into a workspace
directory such as `C:\ai-arsenal\`:

```
start_arsenal.bat     one-click launcher (run from the root)
PROMPT.md             the prompt handed to the sandbox agent
config.example.json   copy to config.json and edit
result.schema.json    the contract, for reference
templates/
  runner.py           drives the sandbox, pulls the result, sets the exit code
  arsenal_contract.py contract validation (the gate's logic)
  review.py           advisory DeepSeek commentary (never affects the verdict)
  selftest.py         gate self-test (no sandbox needed)
```

Then `copy config.example.json config.json` and set `project_dir`.

The report language is set by `report_language` (Chinese by default):

```json
"report_language": "简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）"
```

It only affects the prose in `report.md` and the `blocked` strings in
`result.json` — **the gate never parses the report**, so changing this cannot
change a verdict. Set it to `"English"` for English reports. `review.py`
commentary is Chinese by default; override with `--language`.

`project_dir` is **the repository you want tested**, not a copy of it:

- It must be the repository root, i.e. the directory that contains `.git`.
  With `workspace_mode: "clone"` (the default) a non-repo path is a hard
  preflight failure, because `sbx create --clone` cannot work without one.
  If your project is not a git repo, use `workspace_mode: "bind"` instead.
- The example ships with the placeholder `C:/path/to/your/repo` on purpose.
  There is no sensible default, and `runner.py` refuses to run until you set a
  real one.

No third-party packages are required. Only the Python standard library.

## Phase 3 — Create the sandbox

`runner.py` creates the sandbox on the first run. It uses `--clone` by default,
which mounts the host repository **read-only** and gives the agent a private
in-container clone, so test side effects cannot touch your working tree.

`workspace_mode: "bind"` mounts the host directory read-write and is gated
behind `"i_accept_bind_mount_risk": true` — think before setting it.

## Phase 4 — Run and read the result

Double-click `start_arsenal.bat`, or `python runner.py --config config.json`.

Exit codes are the contract:

| Code | Meaning |
| --- | --- |
| 0 | verdict PASS — counts added up, nothing failed, nothing blocked |
| 1 | verdict FAIL — any of: tests failed, counts inconsistent, result missing, zero tests, commit mismatch, agent crashed |
| 2 | setup error — sbx missing, key unset, project path wrong, unsafe config, run aborted mid-way |
| 3 | verdict BLOCKED — nothing failed, but part of the suite never ran (missing dependency, no network). Not a pass, not a failure |

`blocked` gets its own code because "the tests broke" and "the tests never
finished" are different facts. Both used to print FAIL, so a 220/220 green run
that skipped 10 assertions for a missing `cryptography` package looked like
broken code. `passed` is still only `true` at exit 0, so any script that checks
for zero keeps the same behaviour.

Every run leaves a trail in `out/<run_id>/` — **a run directory is never empty**:

```
out/<run_id>/
  summary.json                  the machine answer (state, reasons, counts, artifacts)
  agent.log                     everything the agent printed, survives a closed window
  sandbox/arsenal-out/          the whole sandbox output dir, not two hard-coded files
    result.json  report.md  logs/…
out/latest.txt                  path of the most recent run
```

The whole directory is copied back on purpose: extra raw logs, coverage data or
environment dumps the agent left behind come home automatically instead of being
dropped because the host did not know their names in advance.

Read `summary.json` — it is the machine answer:

```json
{ "state": "pass", "passed": true, "exit_code": 0,
  "reasons": [], "counts": {"total": 230, "passed": 230, "failed": 0, "skipped": 0},
  "artifacts": ["sandbox/arsenal-out/result.json", "sandbox/arsenal-out/logs/core.log"] }
```

## When the data did not come back

The sandbox still holds the result but `out/` is empty — usually because the
window was closed or the process was killed before the pull-back ran. Do not
rerun the suite (that burns tokens for a result you already have); collect it:

```
python templates/runner.py --config config.json --collect
```

It skips the agent, pulls the existing sandbox output again, revalidates it and
writes a fresh run directory.

## Why the gate is fail-closed

The validator rejects a run when **any** of these is true. All of them are real
failure modes, not hypotheticals:

- `result.json` missing or not valid JSON
- a required field missing, or a count is a boolean/string instead of an integer
- `passed + failed + skipped != total`
- `total == 0` — an empty suite is not a pass
- `failed > 0` → FAIL; non-empty `blocked` → BLOCKED (3). Neither is a pass.
- `commit` empty, or different from `expect_commit`
- `finished_at` not later than `started_at`
- the agent process exited non-zero

Unknown states never resolve to PASS. That is the whole point: a test platform
that cannot say "fail" is worse than no test platform, because it manufactures
confidence.

## Red lines

- **Never** use `sbx setup` — experimental, and it silently imports host secrets.
- **Never** set the network policy to allow-all / "Open". Add one host instead.
- **Never** pass the API key as `KEY=VALUE` on a command line. It lands in the
  process table. `runner.py` uses `--env-file` with a 0600 temp file.
- **Never** let the agent write the verdict. The prompt forbids it; the host
  validates it; `review.py` is advisory only.
- **Never** parse `report.md` to decide anything. It is prose for humans.

## Verifying an install

`runner.py` ships with a self-test that exercises the gate against seventeen
scenarios without touching a real sandbox — thirteen verdict/evidence cases, one path-contract check, one sandbox-binding check, one that
checks a run leaves `summary.json` / `agent.log` / the sandbox output behind,
plus two that check the preflight refuses an unusable `project_dir`:

```
python templates/selftest.py
```

All seventeen must report `ok`. If any reports `MISMATCH`, the gate is broken — do
not trust its PASS verdicts.

## Uninstall

```
sbx rm test-sandbox
```

Then delete the workspace directory. Nothing was installed system-wide.
