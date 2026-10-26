# AI Arsenal Builder v2

> A dedicated AI test platform for Windows: DeepSeek Harness on the host,
> Claude Code inside a Docker `sbx` sandbox, and the **host owns the verdict**.

![Platform](https://img.shields.io/badge/Platform-Windows-blue)
![Runtime](https://img.shields.io/badge/Python-3.9%2B%20stdlib-brightgreen)
![Sandbox](https://img.shields.io/badge/Sandbox-Docker%20sbx-blue)

## Design

One sentence: **the sandbox produces data, the host produces the verdict.**

- The agent in the sandbox runs the suite and writes `result.json` (structured)
  plus `report.md` (for humans)
- The host pulls the result **out** with `sbx cp`, validates it against a
  contract, and decides Pass/Fail itself
- The exit code is the only signal: `0` pass / `1` fail / `2` setup error /
  `3` blocked (part of the suite never ran - not a pass, not a failure)

Report prose is never parsed. The sandbox never phrases the verdict. Anything
that cannot be confirmed is a FAIL.

## What v2 fixes

| v1 problem | v2 handling |
| --- | --- |
| Failing tests still printed "pipeline complete", exit code 0 | Exit codes propagate; `runner.py` returns the real verdict, the `.bat` checks each stage |
| `assert_report.py` contained zero `assert`; the gate was a `print` | Gate replaced with deterministic contract validation |
| Missing result file fell through to `return`, exit code 0 | Missing file is a FAIL |
| Sandbox wrote straight into the host mount; host trusted it | Sandbox writes only to its own `/tmp`; host pulls with `sbx cp` |
| API key placed on the command line (`sbx exec -e KEY=VALUE`) | `--env-file` with a 0600 temp file, deleted after use |
| Network policy forced to `Open` (allow everything) | One host allowed: `sbx policy allow network api.deepseek.com` |
| Told users to run `sbx setup` (experimental, imports global secrets) | Explicitly forbidden, with the reason documented |
| `templates/` did not exist; en and zh copies diverged | One layout for both languages, both READMEs point at it |
| Runner wrote `/c/...`, assertion script read `C:\...` | One `project_dir` config value, paths converted internally |
| `requests` dependency undeclared | Standard library only, zero third-party packages |
| Both READMEs truncated mid code block | Complete, including self-test and uninstall |
| Frontmatter not at byte 0; name did not match the folder | `name: arsenal-builder`, frontmatter at byte 0 |

## Quick start

1. Install Docker Desktop and the standalone `sbx` CLI (MSI). `sbx version` must work.
2. Set a User environment variable: `ANTHROPIC_AUTH_TOKEN` = your DeepSeek key.
3. Allow exactly one endpoint:

   ```powershell
   sbx policy init balanced
   sbx policy allow network api.deepseek.com
   sbx policy check network api.deepseek.com   # must print Allowed
   ```

4. Create your config (docs, config and the launcher live in the package root;
   the scripts live in `templates\`):

   ```powershell
   copy config.example.json config.json
   notepad config.json    # set project_dir (repo root, must contain .git)
   ```
   Check `report_language` while you are there: it defaults to Chinese, and the
   agent writes `report.md` in that language.

5. Self-test the gate (no sandbox, a few seconds):

   ```powershell
   python templates\selftest.py
   ```

6. Run the pipeline — double-click `start_arsenal.bat`, or:

   ```powershell
   .\start_arsenal.bat
   ```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | PASS - counts consistent, nothing failed, nothing blocked |
| 1 | FAIL - tests failed, counts inconsistent, result missing, zero tests, commit mismatch, or the agent crashed |
| 2 | Setup error - sbx missing, key unset, bad project path, unsafe config, run aborted |
| 3 | BLOCKED - nothing failed, but part of the suite never ran (missing dependency, no network) |

Each run produces `out/<run_id>/` with `summary.json`, `agent.log`, and the whole
sandbox output directory under `sandbox/arsenal-out/` - not just two hard-coded
filenames, so extra logs and coverage come home too. A run directory is never
empty: even a run that dies mid-way leaves `summary.json` saying so. Downstream
consumers (DSH, CI) read only `summary.json` and the exit code.

Lost the host copy? `python templates/runner.py --config config.json --collect`
pulls the existing sandbox output again without rerunning the tests.

## Layout

```
arsenal-builder/
├── SKILL.md               skill description (English)
├── SKILL.zh-CN.md         skill description (Chinese)
├── README.md              this file
├── README_zh.md           中文说明
├── CHANGELOG.md           change log
├── LICENSE
├── PROMPT.md              prompt handed to the sandbox agent
├── start_arsenal.bat      one-click launcher
├── config.example.json    config template
├── config.json            your config (created from the example)
├── result.schema.json     contract JSON Schema
└── templates/
    ├── runner.py             drives the sandbox, pulls results, sets the exit code
    ├── arsenal_contract.py   contract validation
    ├── review.py             advisory commentary (never affects the verdict)
    └── selftest.py           14-scenario gate self-test
```

## Security boundary

- The agent runs with `--dangerously-skip-permissions`, so it executes whatever
  it decides to execute without asking
- The project under test must be yours, with trusted dependency sources -
  "install dependencies" runs third-party install scripts by design
- Default `--clone` mode mounts the host repo read-only and gives the agent a
  private in-container clone, so side effects cannot reach your working tree
- Only the DeepSeek endpoint is allowed outbound; everything else is denied
- The host never trusts prose from the sandbox, only the counts in
  `result.json` and `summary.json`

## Uninstall

```powershell
sbx rm test-sandbox
```

Delete the workspace directory. Nothing was installed system-wide.

## License

MIT
