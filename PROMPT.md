# Prompt handed to the sandbox agent

You are running inside an isolated sandbox on a dedicated test machine.
Your workspace is {{PROJECT_PATH}}.

## Language

Write **both** `{{REPORT_PATH}}` and every string inside `{{RESULT_PATH}}` in
{{REPORT_LANGUAGE}}. Keep code, file paths, commands, test names and versions in
their original form - do not translate them. A mixed Chinese/English report is
expected and fine; a report written entirely in English is not.

## What to do

1. Inspect the project at `{{PROJECT_PATH}}`: language, package manager, test runner.
2. Install the dependencies needed to run the existing test suite.
3. Run the full test suite and capture the real numbers.
4. Write two files into `{{OUT_DIR}}` (create the directory if needed):
   - `{{RESULT_PATH}}` — machine-readable, see the contract below
   - `{{REPORT_PATH}}` — human-readable summary: stack, commands run, what failed
5. Leave the raw evidence there too, because the host keeps everything in
   `{{OUT_DIR}}` and will not accept a summary with nothing behind it:
   - `{{OUT_DIR}}/logs/<suite>.log` — the verbatim stdout+stderr of each test
     command, unedited and untruncated
   - `{{OUT_DIR}}/logs/env.txt` — interpreter version, package manager version,
     and the installed versions of the dependencies you used
   Do not clean up or overwrite these files between runs.

## Hard rules

- **Do not decide whether the build passes.** Never write "PASS" or "FAIL" as a
  verdict, and do not try to make the result look good. Report counts only.
- Report the numbers your test runner actually printed. If you could not run the
  suite, say so by setting `total: 0` and describing the reason in `blocked`.
  Do not estimate, infer, or invent counts.
- Do not read, write, or inspect anything outside `{{PROJECT_PATH}}` and
  `{{OUT_DIR}}`. There is nothing else you need.
- `commit` must be the current `git rev-parse HEAD` of the project, or `"unknown"`
  when the project is not a git repository.
- Timestamps are ISO 8601 with timezone.

## Result contract

`{{RESULT_PATH}}` must be a single JSON object with exactly these fields:

```json
{
  "project": "vecRecall",
  "commit": "7f3a2c9",
  "started_at": "2026-10-26T18:00:00+08:00",
  "finished_at": "2026-10-26T18:12:31+08:00",
  "total": 230,
  "passed": 230,
  "failed": 0,
  "skipped": 0,
  "blocked": []
}
```

- `total`, `passed`, `failed`, `skipped` are integers and must satisfy
  `passed + failed + skipped == total`.
- `blocked` is an array of short strings, written in the report language; empty
  means nothing was blocked.
- Extra keys are ignored. Missing or malformed keys make the run fail.

The host validates this file and owns the verdict. If the JSON is missing or
does not add up, the run fails regardless of what your report says.
