# AI Arsenal Builder 3.0.1

A dedicated AI test platform for Windows: **DeepSeek Harness on the host** drives
**Docker `sbx` sandboxes (untrusted)**, a Claude Code agent inside the sandbox runs
the suite, and the **host decides the verdict**.

**The sandbox produces data, the host produces the verdict.** The sandbox never
phrases the answer, and prose never decides it.

> This file describes the **platform package** (the directory you just unzipped).
> For a complete deployment, how the root is resolved, and what the nine report
> sections demand, see `SKILL.md` / `SKILL.zh-CN.md` in this package and
> `README.md` at the arsenal root.

---

## What is in this package

| File | Job |
| --- | --- |
| `start_arsenal.bat` | Launcher: resolve the root → run `runner.py` → act on the exit code. **The only thing to double-click** |
| `PROMPT.md` | The prompt handed to the sandbox agent: nine-section report outline plus language/depth placeholders |
| `SKILL.md` / `SKILL.zh-CN.md` | Platform docs (architecture, four phases, red lines, why the gate is fail-closed) |
| `config.example.json` | Sample config — copy it to the **root** and set `project_dir` |
| `result.schema.json` | The `result.json` contract the gate validates against |
| `CHANGELOG.md` | What changed in each version, why, and the measured numbers |
| `templates/runner.py` | Drives the sandbox, pulls the result, runs the gate, writes `summary.json`, sets the exit code |
| `templates/arsenal_contract.py` | Contract validation itself (pure functions, never raises) |
| `templates/review.py` | Advisory DeepSeek commentary — **never affects the verdict** |
| `templates/selftest.py` | 21-scenario gate self-test, no real sandbox needed |

No third-party packages. Python standard library only (3.9+).

## How to use it

### Option 1 — install into a root (recommended)

```
<ARSENAL_HOME>\                     e.g. C:\ai-arsenal
├── arsenal.cmd                     the only entry point
├── install.bat                     idempotent install + self-check
├── config.json                     wiring: which project, where reports go
├── last-run.txt                    written by the runner, points at the newest run
├── workspaces\<repo>\              the project under test, must contain .git
├── skills\
│   └── arsenal-builder\            ← unzip this package here
├── reports\arsenal\<run_id>\       artifacts of one run
├── dist\                           distributable zips
└── archive\                        old versions and reports, read-only
```

Unzip into `<ARSENAL_HOME>\skills\arsenal-builder\`, then run `arsenal.cmd install`
at the root: it creates directories, seeds `config.json` and runs the self-test.
Running it twice changes nothing.

### Option 2 — standalone try-out

Unzip anywhere and run `start_arsenal.bat` directly. When no root can be found it
falls back to the package's own directory (`out/` lands inside the package), which
is fine for checking that the pipeline starts at all.

## Path contract

Two rules, and that is the whole contract:

| Config key | Resolved against | Example |
| --- | --- | --- |
| `prompt_file` (**code**, ships with the package) | the skill dir `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`, `out_dir` (**data**, belongs to the root) | the root `ARSENAL_HOME` | `workspaces/vecRecall`, `reports/arsenal` |

The root comes from, in order: `--root <dir>` → `$ARSENAL_HOME` → **the directory
layout** (this package sits at `<root>\skills\arsenal-builder\`, so the root is two
levels up) → the package directory.

Rule 3 is geometric rather than a string constant, so **moving the whole folder
requires editing nothing**.

## Commands

```bat
arsenal.cmd                 run the full pipeline
arsenal.cmd --dry-run       preflight only: is sbx there, is the key set, is the project a git repo
arsenal.cmd --collect       do not test; pull back what the sandbox already produced
arsenal.cmd --recreate      rebuild the sandbox (required after changing project_dir)
arsenal.cmd install         install / self-check
```

`runner.py` also takes `--report-depth brief` (sections 1/5/6/8 only),
`--strict-report` (a thin report counts as a failure) and `--print-home`
(print the resolved root). The launcher passes every argument through.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | PASS — counts added up, nothing failed, nothing blocked |
| 1 | FAIL — tests failed / counts inconsistent / result missing / zero tests / commit mismatch / agent crashed |
| 2 | setup error — sbx missing, key unset, wrong project path, sandbox bound to another directory |
| 3 | BLOCKED — nothing failed, but part of the suite never ran (missing dependency, no network). Not a pass, not a failure |

**The exit code is the verdict; never parse the prose.** The machine answer lives in
`<OUT_DIR>\<run_id>\summary.json`.

## The report

`PROMPT.md` demands nine sections: overview / stack (observed versions) / module
layout (`wc -l`) / one row per dependency / per-suite results plus what they cover /
counts / benchmark comparison / not covered and risks / conclusion (no PASS/FAIL
wording).

`report_language` and `report_depth` control the prose and **cannot move a verdict**
(the gate never parses the report). Every run also audits the report — section count,
table rows, bytes — and prints anything that looks thin; it is a warning by default,
a failure only with `--strict-report`.

## Self-test

```
python templates\selftest.py
```

All 21 scenarios must report `ok`. Any `MISMATCH` means the gate is broken — do not
trust its PASS verdicts.

## Red lines

- **Never** use `sbx setup`: experimental, and it silently imports host credentials
- **Never** set the network policy to allow-all; allow one host instead
- **Never** pass the key as `KEY=VALUE` on a command line — it lands in the process table (use `--env-file` with a 0600 temp file)
- **Never** let the sandbox phrase the verdict, and **never** parse `report.md` to decide anything

## Uninstall

```
sbx rm <sandbox> --force
```

Then delete the workspace directory. Nothing was installed system-wide.
