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

Current version: **3.0.3** — what changed and why is in `CHANGELOG.md`.

## When to use this skill

- "set up an isolated testing platform", "build an AI arsenal", "配置 sbx 沙箱"
- "my test pipeline always reports success", "the gate never fails"
- Repairing an existing arsenal (this package is **3.0.3** and replaces the v1/v2 packages)

## Architecture

| Side | Trust | Responsibility |
| --- | --- | --- |
| Host (DSH) | trusted | owns the verdict, reads `summary.json`, acts on the exit code |
| Sandbox (sbx) | untrusted | installs deps, runs suite, writes `result.json` + `report.md` |

Data flows **out by pull**: the agent writes to `/tmp/arsenal-out` inside the
sandbox, the host copies the whole output **directory** back with `sbx cp` —
not two hard-coded file names. The sandbox never writes into a host directory.

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
├── install.bat                     idempotent install + self-check
├── config.json                     wiring: which project, where reports go
├── last-run.txt                    written by the runner, points at the newest run
├── workspaces\vecRecall\           the project, with its own .git
├── skills\arsenal-builder\         this package, unzipped here
├── reports\arsenal\<run_id>\       artifacts of one run
├── dist\                           the distributable zip
└── archive\                        old versions and old reports, read-only
```

Two resolution rules, and that is the whole contract:

| Path | Resolved against | Example |
| --- | --- | --- |
| `prompt_file` (code) | the **skill** dir `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`, `out_dir` (data) | the **root** `%ARSENAL_HOME%` | `workspaces/vecRecall`, `reports/arsenal` |

The root comes from, in order:

1. `--root <dir>`
2. `$ARSENAL_HOME`
3. **the directory layout**: this package sits at `<root>\skills\arsenal-builder\`,
   so the root is two levels up
4. the package directory itself (a quick standalone try)

Rule 3 is the important one: **moving the whole folder requires editing nothing**.
Move `C:\ai-arsenal` to `D:\arena` and the root becomes `D:\arena`, because the
constraint is geometric (the parent of `skills`), not a string constant. The
environment variable is therefore optional, not required:

```bat
setx ARSENAL_HOME "C:\ai-arsenal"    :: optional, just a shortcut
```

`runner.py --print-home` prints the root it actually resolved, and
`start_arsenal.bat` uses that instead of guessing how many levels up `%~dp0` is.

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
start_arsenal.bat     one-click launcher (also called by the root's arsenal.cmd)
PROMPT.md             the prompt handed to the sandbox agent
config.example.json   copy to <ARSENAL_HOME>\config.json and edit
result.schema.json    the contract, for reference
README.md             the full manual (Chinese): architecture + operation - README.en.md is the mirror
templates/
  runner.py           drives the sandbox, pulls the result, sets the exit code
  arsenal_contract.py contract validation (the gate's logic)
  review.py           advisory DeepSeek commentary (never affects the verdict)
  selftest.py         gate self-test, 21 scenarios, no sandbox needed
```

Two more entry points live in the root, not in the package: `arsenal.cmd` (the only
one a human double-clicks - it sets `ARSENAL_HOME` and forwards every flag) and
`install.bat` (idempotent install plus self-check).

Then `copy config.example.json config.json` and set `project_dir`.

### What the report must contain

`PROMPT.md` carries a **nine-section outline**, not a one-line request. By
default the agent writes all of it:

| # | Section | What has to be in it |
| --- | --- | --- |
| 1 | Overview | what the project is, what it solves, its core principles — read from the source, not the README |
| 2 | Stack | table: language / declared requirement / **observed** version / packaging / test runner |
| 3 | Module layout | tree with the real `wc -l` line count and the job of each file |
| 4 | Dependencies | one row per dependency: kind / status / version. **This table is what makes two runs comparable** |
| 5 | Test results | per suite: command, the raw `158/158`, exit code, and what it covers (as bullets) |
| 6 | Counts | total / passed / failed / skipped, integers |
| 7 | Benchmarks | tabulate and compare against the README's claim; say plainly if there is nothing to run |
| 8 | Not covered / risks | what this run did not verify, and why a reader should care |
| 9 | Conclusion | facts: which dimensions are verified, which are not. **No PASS/FAIL wording** — the host owns the verdict |

Two settings control the prose, and neither can move a verdict because the gate
never parses the report:

```json
"report_language": "简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）",
"report_depth": "full"
```

`report_depth` is `full` (all nine sections) or `brief` (sections 1, 5, 6 and 8
only, for CI runs nobody reads). Override per run with `--report-depth brief`.
`review.py` commentary is Chinese by default; override with `--language`.

### The report is audited separately

After every run the runner counts sections, table rows and bytes, and prints
anything that looks thin:

```
  report: 5 section(s), outline asks for 6
  report: 1863 B of prose, too little to explain anything (4000 B expected)
```

By default this is a warning only: green tests and a badly written report are
different problems, and letting prose quality flip an exit code would make the
verdict unpredictable. Add `--strict-report` to count it as a failure.

> Why this exists: the report once shrank from 174 lines / 8194 bytes to 51
> lines / 2001 bytes. The headings survived, the content did not, the tests were
> still green, and **nothing anywhere complained** — until a human opened the
> file and found counts and nothing else.

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

Double-click `arsenal.cmd` (it sets `ARSENAL_HOME` and forwards flags), or run
`python runner.py --config config.json` from the skill directory.

Exit codes are the contract:

| Code | Meaning |
| --- | --- |
| 0 | verdict PASS — counts added up, nothing failed, nothing blocked |
| 1 | verdict FAIL — any of: tests failed, counts inconsistent, result missing, zero tests, commit mismatch, agent crashed |
| 2 | setup error — sbx missing, key unset, project path wrong, config broken, `bind` without risk confirmation, sandbox bound to another directory, run aborted mid-way |
| 3 | verdict BLOCKED — nothing failed, but part of the suite never ran (missing dependency, no network). Not a pass, not a failure |

`blocked` gets its own code because "the tests broke" and "the tests never
finished" are different facts. Both used to print FAIL, so a 220/230 green run
that skipped 10 assertions for a missing `cryptography` package looked like
broken code. `passed` is still only `true` at exit 0, so any script that checks
for zero keeps the same behaviour.

Every run leaves a trail under `out_dir` — in a proper root that is
`reports\arsenal\<run_id>\` — and **a run directory is never empty**:

```
<ARSENAL_HOME>\reports\arsenal\<run_id>\
  summary.json                  the machine answer (state, reasons, counts, artifacts, paths)
  agent.log                     everything the agent printed, survives a closed window
  sandbox/arsenal-out/          the whole sandbox output dir, not two hard-coded files
    result.json  report.md  logs/…
<ARSENAL_HOME>\reports\arsenal\latest.txt   path of the most recent run
<ARSENAL_HOME>\last-run.txt                 same path, at the root, for the launcher
```

Two pointers on purpose: `latest.txt` sits next to the run directories, `last-run.txt`
sits at the root so the launcher can find the newest run without knowing where
`out_dir` points or parsing anyone's prose.

The whole directory is copied back on purpose: extra raw logs, coverage data or
environment dumps the agent left behind come home automatically instead of being
dropped because the host did not know their names in advance.

Read `summary.json` — it is the machine answer:

```json
{ "run_id": "2026-10-26T22-41-08", "state": "pass", "passed": true, "exit_code": 0,
  "paths": { "home": "C:\\ai-arsenal", "skill": "C:\\ai-arsenal\\skills\\arsenal-builder",
             "project": "C:\\ai-arsenal\\workspaces\\vecRecall",
             "reports": "C:\\ai-arsenal\\reports\\arsenal",
             "run": "C:\\ai-arsenal\\reports\\arsenal\\2026-10-26T22-41-08" },
  "reasons": [], "counts": {"total": 230, "passed": 230, "failed": 0, "skipped": 0},
  "report_ok": true, "report_depth": "full", "report_problems": [],
  "artifacts": ["sandbox/arsenal-out/result.json", "sandbox/arsenal-out/logs/core.log"] }
```

`paths` is the five absolute paths a run touches (home / skill / project / reports / run),
so nobody has to guess which copy of the project or which report directory was used -
that question is exactly how this platform once tested the wrong tree. `report_ok`
and `report_problems` record the report audit; they never change the exit code
unless `--strict-report` is set.

## When the data did not come back

The sandbox still holds the result but the run directory is empty — usually because the
window was closed or the process was killed before the pull-back ran. Do not
rerun the suite (that burns tokens for a result you already have); collect it:

```
arsenal.cmd --collect
:: or, without the root launcher:
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
- `commit` empty, or different from `expect_commit` (an abbreviated hash is
  tolerated when a ≥7-char prefix matches on either side)
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
- **Never** expose the host directory read-write (`workspace_mode: "bind"`) without
  an explicit `"i_accept_bind_mount_risk": true`.

## Verifying an install

`runner.py` ships with a self-test that exercises the gate against twenty-one
scenarios without touching a real sandbox — eleven verdict cases (pass, tests
failed, deps blocked, missing result, inconsistent counts, zero tests, crashed
agent, garbage JSON, non-UTF8 output, wrong commit, abbreviated commit), five
contract checks (a run must leave `summary.json` / `agent.log` / the sandbox
output behind, the data/code path split, the root inferred from the layout, a
fully rendered prompt that carries the report depth, and unique run
directories), two report audits (a counts-only report must be flagged, a full
one must pass), one sandbox-binding check, and two that check the preflight
refuses an unusable `project_dir`:

```
python templates/selftest.py
```

All twenty-one must report `ok`. If any reports `MISMATCH`, the gate is broken —
do not trust its PASS verdicts.

## Uninstall

```
sbx rm test-sandbox --force
```

`--force` matters: without it `sbx rm` asks for confirmation and sits there. Then
delete the workspace directory. Nothing was installed system-wide.
