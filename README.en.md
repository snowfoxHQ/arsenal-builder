# AI Arsenal Builder 3.4.0 — Full Manual

[![selftest](https://github.com/snowfoxHQ/arsenal-builder/actions/workflows/selftest.yml/badge.svg)](https://github.com/snowfoxHQ/arsenal-builder/actions/workflows/selftest.yml)


A **self-contained, distributable Agent Skill package**: an LLM agent runs your
tests inside an isolated sandbox, and the **host decides the verdict**. The
sandbox backend is swappable (`sbx` microVM / Docker / no isolation); the
verdict semantics are not.

One-sentence rule: **the sandbox produces data, the host produces the verdict.**

It works without any host: `python templates\runner.py` runs as-is, and dropping
this folder into a host's skills directory makes `SKILL.md` discoverable.

> This repository *is* the package: architecture principles (section 1),
> step-by-step operation (section 2), troubleshooting (section 3), and
> `CHANGELOG.md` for the why behind each version.
> The machine workbench (`arsenal.cmd`, `config.json`, `reports\`) is a
> separate repo: [ai-arsenal](https://github.com/snowfoxHQ/ai-arsenal).

---

## 1. Architecture

### 1.1 What it defends against

Three real incidents, each of which actually happened:

| Incident | Symptom | Countermeasure |
| --- | --- | --- |
| **False success** | 7 tests failed, pipeline still printed "complete" with exit code 0 | Verdict moved to the host: the sandbox hands over structured data, the host computes the exit code from a contract |
| **A gate that was decoration** | The validation script contained zero `assert`, just `print` | Contract validation is a pure function, `arsenal_contract.py` — never raises, and anything unconfirmed is a FAIL |
| **Report shrank silently** | The report went from 174 lines to 51; headings survived, content did not; tests stayed green | Reports follow a nine-section outline and are audited after every run (sections / table rows / bytes) |

### 1.2 Trust boundary

| Side | Trust | Job | Holds |
| --- | --- | --- | --- |
| **Host** (DeepSeek Harness + `runner.py`) | trusted | preflight, create sandbox, pull result, run contract check, set exit code, write `summary.json` | API key, `config.json`, all artifacts |
| **Sandbox** (Docker `sbx` + Claude Code agent) | **untrusted** | install deps, run suite, write `result.json` + `report.md` | read-only clone of the project, network allow-list, its own `/tmp` |

Three rules follow directly:

1. **Data is pulled out, never pushed in.** The agent writes to `/tmp/arsenal-out`
   inside the sandbox; the host pulls the **whole directory** back with `sbx cp`.
   The sandbox process never gets a host write path — the pull direction *is* the
   permission direction.
2. **Prose never decides anything.** `report.md` is for humans; the gate **never
   parses it**. `review.py` commentary is advisory — a hundred compliments cannot
   move the exit code. The verdict comes from the numbers in `result.json` and the
   host's arithmetic.
3. **Fail-closed.** Any hit on the checklist is a FAIL, and a state that cannot be
   confirmed is never a PASS. A test platform that cannot say "fail" is worse than
   none, because it manufactures confidence.

### 1.3 Life cycle of one run

`runner.py` runs six steps; every step that can fail has an explicit exit code:

```
  HOST                                    SANDBOX (untrusted)
  ────                                    ───────────────────
  1 preflight   sbx present? key present? config sane?
                project a git repo? sandbox bound to the right dir?
  2 ensure      sandbox missing → sbx create --clone ─→ host repo mounted read-only
                (clone mode: read-only mount + private       │
                 in-container clone; test side effects       ▼
                 never touch your working tree)
  3 execute     writes a 0600 temp env-file ────────→ Claude Code runs the suite
                (key never in the process table)       writes /tmp/arsenal-out/
                                                       result.json + report.md + logs/
  4 fetch       sbx cp the whole output dir ←────────── pulled, sandbox never writes host
                lands in <out_dir>\<run_id>\sandbox\
  5 validate    contract check (pure function, fail-closed)
                + report audit (sections / table rows / bytes)
  6 exit code   writes summary.json + both latest pointers
                0 PASS / 1 FAIL / 2 SETUP / 3 BLOCKED
                (then review.py advisory — never affects the verdict)
```

**Sandbox binding check** (inside step 2): an `sbx` sandbox is bound to one host
directory for life. Reusing one cloned from a different project silently tests the
**wrong tree** — the report looks fine, but it measured another copy. That is the
hardest failure to notice. `runner.py` reads the real binding from
`sbx ls --format json` and exits 2 when it does not match `project_dir`, requiring
`--recreate`. `--dry-run` catches it too.

### 1.4 The gate checklist (fail-closed)

`arsenal_contract.py` checks the pulled `result.json` item by item; any hit is a fail:

- `result.json` missing, or not valid JSON
- a required field missing, or a count is a boolean/string instead of an integer
- `passed + failed + skipped != total`
- `total == 0` — an empty suite is not a pass
- `failed > 0` → FAIL; non-empty `blocked` → BLOCKED
- `commit` empty, or different from `expect_commit` (abbreviated-hash prefix ≥7 chars tolerated)
- `finished_at` not later than `started_at`
- the agent process exited non-zero

### 1.5 Exit codes

**The exit code is the verdict; downstream code must not parse prose.**

| Code | Meaning | Typical causes |
| --- | --- | --- |
| 0 | PASS | counts add up, nothing failed, nothing blocked |
| 1 | FAIL | tests failed / counts inconsistent / result missing / zero tests / commit mismatch / agent crashed |
| 2 | SETUP | sbx missing, key unset, wrong project path, broken config, `bind` without risk confirmation, sandbox bound elsewhere, run aborted mid-way |
| 3 | BLOCKED | nothing failed, but part of the suite **never ran** (missing dependency / no network) |

`BLOCKED` gets its own code because "the tests broke" and "the tests never finished"
are different facts. Both used to print FAIL: a 220/230 green run that skipped 10
assertions for a missing `cryptography` package looked like broken code. The
`passed` field is still `true` only at exit 0, so scripts that check for zero behave
as before.

### 1.6 One root: project, platform, reports

Three things scattered across three directories is how "the report looks fine but it
measured another copy" happens. So there is one root:

```
<ARSENAL_HOME>\                     e.g. C:\ai-arsenal
├── arsenal.cmd                     the only entry point (sets ARSENAL_HOME, forwards flags)
├── install.bat                     idempotent install + self-check
├── config.json                     wiring: which project, where reports go (upgrade keeps it)
├── last-run.txt                    written by the runner, points at the newest run
├── workspaces\<repo>\              the project under test, must contain .git
├── skills\
│   └── arsenal-builder\            ← unzip this package here
├── reports\arsenal\<run_id>\       artifacts of one run (incl. sandbox\arsenal-out\)
├── dist\                           distributable zips
└── archive\                        old versions and reports, read-only
```

**The path contract is two rules:**

| Config key | Resolved against | Example |
| --- | --- | --- |
| `prompt_file` (**code**, ships with the package) | the skill dir `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`, `out_dir` (**data**, belongs to the root) | the root `ARSENAL_HOME` | `workspaces/vecRecall`, `reports/arsenal` |

The root comes from, in order: `--root <dir>` → `$ARSENAL_HOME` → **the directory
layout** (this package sits at `<root>\skills\arsenal-builder\`, so the root is two
levels up) → the package directory itself.

Rule 3 is **geometric**, not a string constant, so **moving the whole folder requires
editing nothing**: move `C:\ai-arsenal` to `D:\arena` and the root becomes
`D:\arena`. The environment variable is therefore optional. `runner.py --print-home`
prints the root it actually resolved; the launcher uses that instead of counting
levels up from `%~dp0`.

Reports live outside the package and `config.json` lives in the root for the same
reason: **an upgrade must not destroy data or wiring** — unzipping a new version over
the old package would otherwise take the reports with it.

### 1.7 The report: nine sections + a separate audit

The report once shrank from 174 lines / 8194 bytes to 51 lines / 2001 bytes: headings
survived, content did not, and nothing anywhere complained. So `PROMPT.md` does not
ask for "a summary" — it demands **nine sections**:

| # | Section | What has to be in it |
| --- | --- | --- |
| 1 | Overview | what the project is, what it solves, its core principles — **read from the source**, not the README |
| 2 | Stack | table: language / declared requirement / **observed** version / packaging / test runner |
| 3 | Module layout | tree with real `wc -l` line counts and the job of each file |
| 4 | Dependencies | one row per dependency: kind / status / version. **This table is what makes two runs comparable** |
| 5 | Test results | per suite: command, raw numbers like `158/158`, exit code, **what it covers** (bullets, from real test names) |
| 6 | Counts | total / passed / failed / skipped, integers |
| 7 | Benchmarks | tabulate and compare against the README's claim; say plainly if there is nothing to run |
| 8 | Not covered / risks | what this run did not verify, and why a reader should care |
| 9 | Conclusion | facts: which dimensions are verified, which are not. **No PASS/FAIL wording** — the host owns the verdict |

Two switches control the prose and cannot move a verdict (the gate never parses it):

```json
"report_language": "简体中文（…）",
"report_depth": "full"
```

`report_depth`: `full` = all nine sections (default) | `brief` = sections 1/5/6/8
only, for CI. Override per run with `--report-depth brief`; unknown values fall back
to `full`.

**A separate report audit**: after every run `runner.py` counts sections, table rows,
bullets and bytes, and prints anything that looks thin:

```
  report: 5 section(s), outline asks for 6
  report: 1863 B of prose, too little to explain anything (4000 B expected)
```

Warning only by default — green tests and a badly written report are different
problems, and letting prose quality flip an exit code would make the verdict
unpredictable. Add `--strict-report` to count it as a failure. The result is recorded
in `summary.json` as `report_ok` / `report_problems`.

### 1.8 Security red lines

- **Never** use `sbx setup`: experimental, has no network-policy step, and silently
  imports host credentials into the global secret store (this actually happened)
- **Never** set the network policy to allow-all / Open; allow one host instead
- **Never** pass the key as `KEY=VALUE` on a command line — it lands in the host
  process table. `runner.py` uses `--env-file` with a 0600 temp file, deleted after use
- **Never** let the sandbox phrase the verdict: the prompt forbids it, the host
  validates it, `review.py` is advisory only
- **Never** parse `report.md` to decide anything; it is prose for humans
- `bind` mode exposes the host directory **read-write** to the agent and requires an
  explicit `"i_accept_bind_mount_risk": true` — think before setting it

## 2. Step-by-step operation

### Step 0 — host prerequisites

1. Install Docker Desktop and the standalone `sbx` CLI (MSI). `sbx version` must work.
2. Put the key in a **User** environment variable (not a session variable — the
   sandbox inherits it):

   ```bat
   setx ANTHROPIC_AUTH_TOKEN "sk-your-deepseek-key"
   ```

3. **Do not** run `sbx setup` (see red lines).

### Step 1 — network policy (allow only what is needed)

```bat
sbx policy init balanced
sbx policy allow network api.deepseek.com
sbx policy check network api.deepseek.com
```

The last command must print `Allowed`; if not, stop and fix the rule — **never fall
back to allow-all**.

Suites that install dependencies will not fail outright, but the affected cases come
back BLOCKED (exit 3). Allow the package hosts individually when needed:

```bat
sbx policy allow network pypi.org
sbx policy allow network files.pythonhosted.org
```

Note: `sbx policy` exists on the host only. The agent cannot approve anything itself
when it sees "Approval required" — it just records it and does not retry in a loop.

### Step 2 — install

Unzip into `<ARSENAL_HOME>\skills\arsenal-builder\`, then at the **root**:

```bat
arsenal.cmd install
```

It creates the `workspaces\` / `reports\` directories, seeds `config.json` (only when
missing — your wiring is never overwritten) and runs the 21-scenario self-test.
Idempotent; running it twice changes nothing.

Then check the key fields of `config.json`:

```json
{
  "sandbox": "arsenal-test",
  "project_dir": "workspaces/vecRecall",
  "workspace_mode": "clone",
  "out_dir": "reports/arsenal",
  "prompt_file": "PROMPT.md",
  "report_language": "…",
  "report_depth": "full"
}
```

`project_dir` must be **the repository you want tested**, at the repository root (the
directory containing `.git`): `clone` mode (default) relies on `sbx create --clone`,
which cannot work without one, and preflight stops you. Only switch to `bind` (and
accept the read-write mount) if the project is not a git repo.

### Step 3 — preflight first, then run

```bat
arsenal.cmd --dry-run     :: preflight only: sbx / key / config / git repo / sandbox binding
arsenal.cmd               :: full pipeline. First run creates the sandbox with --clone
```

The last three lines on screen are the verdict: `RESULT: PASS / FAIL / BLOCKED` with
the meaning of the exit code. Double-clicking `arsenal.cmd` is equivalent; the window
stays open afterwards (`ARSENAL_NO_PAUSE=1` disables that).

### Step 4 — read the result

One run leaves, under `<out_dir>\<run_id>\` (default `reports\arsenal\<run_id>\`):

```
summary.json                     the machine answer — read this first
agent.log                        everything the agent printed (survives a closed window)
sandbox/arsenal-out/             the whole sandbox output dir, pulled back
  result.json  report.md  logs/…
```

`summary.json` field guide:

```json
{ "state": "pass", "passed": true, "exit_code": 0,
  "paths": { "home": "…", "skill": "…", "project": "…",
             "reports": "…", "run": "…" },
  "reasons": [], "counts": {"total": 230, "passed": 230, "failed": 0, "skipped": 0},
  "report_ok": true, "report_depth": "full", "report_problems": [],
  "artifacts": ["sandbox/arsenal-out/result.json", "…"] }
```

- `paths`: the five absolute paths this run touched — "which copy was tested, where
  did the report land" requires no guessing
- `reasons`: on anything other than PASS, the concrete causes (short English lines) —
  faster than reading the report
- `counts`: all-integer counters; their consistency is guaranteed by the gate
- `report_ok` / `report_problems`: the report audit; does not change the exit code
  unless `--strict-report`

`report.md` follows the nine-section outline (see 1.7) and is for humans. The newest
run's path is recorded in both `last-run.txt` at the root and `<out_dir>\latest.txt`.

### Day-to-day commands

```bat
arsenal.cmd --collect              the sandbox has data but it was never pulled back
                                   (window closed / process killed): skip the agent,
                                   pull + revalidate + write, saves a whole run's tokens
arsenal.cmd --recreate             rebuild the sandbox. Required after changing project_dir
arsenal.cmd --strict-report        a thin report counts as a failure, not a warning
arsenal.cmd --report-depth brief   sections 1/5/6/8 only for this run (CI)
runner.py --print-home             print the root that was actually resolved
```

`arsenal.cmd` passes every flag through to `runner.py` untouched.

### Self-test

```bat
python skills\arsenal-builder\templates\selftest.py
```

21 scenarios, no real sandbox: 11 verdict cases (pass / tests failed / deps blocked /
missing result / inconsistent counts / zero tests / crashed agent / garbage JSON /
non-UTF8 / commit mismatch / abbreviated-commit tolerance) + 5 contract checks (a run
must leave all three artifacts behind, the data/code path split, root inferred from
layout, prompt fully rendered, run directories never reused) + 2 report audits +
1 sandbox-binding check + 2 preflight refusals. All must print `ok`; any `MISMATCH`
means the gate is broken — do not trust its PASS verdicts.

### Uninstall

```bat
sbx rm <sandbox> --force
```

`--force` is required — without it `sbx rm` sits at a confirmation prompt. Then delete
the workspace directory; nothing was installed system-wide.

## 3. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Exit 2, "sandbox bound to another directory" | reused a sandbox cloned from another project | `arsenal.cmd --recreate` |
| Suite clearly ran, run directory is empty | the pull-back never ran (window closed / process killed) | `arsenal.cmd --collect`; do not rerun |
| 220/230 green but exit 3 | BLOCKED: part of the suite skipped for missing deps | read `reasons` in `summary.json`; allow pypi if needed (step 1) |
| Exit 0 but the report is thin | the report audit is warning | read `report_problems`; add `--strict-report` to enforce |
| Exit 2, "needs a git repository" | `clone` mode but no `.git` | make the project a repo, or switch to `bind` and accept the risk |
| The .bat window flashes and closes | window closes when done | the run is in the directory `last-run.txt` points at; remove `ARSENAL_NO_PAUSE` to keep it open |
| Chinese in a `.bat` turns into garbage commands | cmd reads batch files in the OEM code page | all batch files in this package are ASCII+CRLF on purpose; Chinese lives only in `.md` |
| Dependency rows full of 403 in the report | package hosts not allowed by the sandbox policy | step 1; add package hosts only, never allow-all |

## 4. What is in this package

| File | Job |
| --- | --- |
| `start_arsenal.bat` | launcher: resolve root → run `runner.py` → act on the exit code |
| `PROMPT.md` | the prompt for the sandbox agent: nine-section outline + language/depth placeholders |
| `SKILL.md` / `SKILL.zh-CN.md` | skill docs (the AI-facing copy: four phases + red lines) |
| `README.md` | this manual in Chinese; `README.en.md` is the mirror |
| `config.example.json` | sample config — copy to the root and edit |
| `result.schema.json` | the `result.json` contract, for reference |
| `CHANGELOG.md` | every version: what changed, why, measured numbers |
| `templates/runner.py` | the six-step pipeline: preflight → ensure → execute → fetch → validate → exit code |
| `templates/arsenal_contract.py` | contract validation (pure functions, never raise) |
| `templates/review.py` | DeepSeek advisory commentary — **never affects the verdict** |
| `templates/selftest.py` | 21-scenario gate self-test |

No third-party packages; Python standard library only (3.9+).
