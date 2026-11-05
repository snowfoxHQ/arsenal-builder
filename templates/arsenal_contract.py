"""Deterministic contract validation for the arsenal test pipeline.

Division of responsibility:
  sandbox agent -> produces data (result.json + report.md)
  host          -> owns the verdict (this module)

Nothing here reads free-form prose. If a field is missing, malformed, or the
counts do not add up, the verdict is FAIL. The default is always to reject.
"""

from __future__ import annotations

import json
import os

REQUIRED_FIELDS = {
    "project": str,
    "commit": str,
    "started_at": str,
    "finished_at": str,
    "total": int,
    "passed": int,
    "failed": int,
    "skipped": int,
    "blocked": list,
}

COUNT_FIELDS = ("total", "passed", "failed", "skipped")


class Verdict:
    """A validation outcome.

    Three states, not two. "the suite failed" and "we could not finish the
    suite" are different facts and must not share a label: a green run with one
    blocked dependency used to print FAIL, which reads like the code is broken.
    `ok` stays True only for a clean pass, so anything that checks `ok` or the
    exit code still refuses to call a blocked run a pass.
    """

    def __init__(self, ok, reasons, data=None, blocked_reasons=None):
        self.ok = bool(ok)
        self.reasons = list(reasons)
        self.blocked_reasons = list(blocked_reasons or [])
        self.data = data

    @property
    def state(self):
        if self.ok:
            return "pass"
        if self.reasons == [] and self.blocked_reasons:
            return "blocked"
        return "fail"

    @property
    def code(self):
        return {"pass": 0, "blocked": 3, "fail": 1}[self.state]

    def summary(self):
        if self.data:
            counts = "total=%s passed=%s failed=%s skipped=%s" % (
                self.data.get("total"),
                self.data.get("passed"),
                self.data.get("failed"),
                self.data.get("skipped"),
            )
        else:
            counts = "no result data"
        head = {"pass": "PASS", "blocked": "BLOCKED", "fail": "FAIL"}[self.state]
        return "%s (%s)" % (head, counts)


def _type_ok(value, expected):
    if expected is int:
        # bool is a subclass of int; a boolean count is a contract violation.
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def same_commit(got, expected):
    """Compare two revision strings, tolerating abbreviations on either side.

    The agent reports `git rev-parse HEAD` (40 chars). A human writing the
    config will type 7. Exact string comparison would call that a mismatch and
    fail a perfectly good run, so compare by prefix once both look like hex.
    """
    a, b = str(got).strip().lower(), str(expected).strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 7 and longer.startswith(shorter)


def validate(result_path, expect_commit=None, expect_run_id=None):
    """Validate result.json and return a Verdict. Never raises on bad input.

    expect_run_id turns the gate into a freshness check: the agent is told the
    run id in the prompt and must copy it verbatim into result.json. A result
    without the matching id is output left over from an earlier run - schema,
    counts and commit may all look perfect, and it still proves nothing about
    this run.
    """
    if not result_path:
        return Verdict(False, ["no result path configured"])

    if not os.path.isfile(result_path):
        return Verdict(False, ["result file not found: %s" % result_path])

    try:
        with open(result_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:  # noqa: BLE001 - any parse failure means no verdict
        return Verdict(False, ["result file is not valid JSON: %s" % exc])

    if not isinstance(data, dict):
        return Verdict(False, ["result root must be a JSON object"])

    reasons = []
    soft = []
    for name, expected in REQUIRED_FIELDS.items():
        if name not in data:
            reasons.append("missing field: %s" % name)
        elif not _type_ok(data[name], expected):
            reasons.append(
                "field %s has wrong type (expected %s)" % (name, expected.__name__)
            )
    if reasons:
        return Verdict(False, reasons, data)

    for name in COUNT_FIELDS:
        if data[name] < 0:
            reasons.append("field %s is negative" % name)
    if reasons:
        return Verdict(False, reasons, data)

    total = data["total"]
    passed = data["passed"]
    failed = data["failed"]
    skipped = data["skipped"]

    if passed + failed + skipped != total:
        reasons.append(
            "counts do not add up: %d + %d + %d != %d" % (passed, failed, skipped, total)
        )

    if total == 0:
        reasons.append("zero tests reported - an empty suite is not a pass")

    if failed > 0:
        reasons.append("%d test(s) failed" % failed)

    # A blocked item is not a failing test - it is a fact that part of the suite
    # never ran (missing dependency, no network). Kept apart from `reasons` so
    # the run can be reported as inconclusive instead of broken.
    if data["blocked"]:
        for item in data["blocked"]:
            soft.append("not verified: %s" % item)

    if not str(data["commit"]).strip():
        reasons.append("commit is empty - cannot tell which revision was tested")

    if expect_commit and not same_commit(data["commit"], expect_commit):
        reasons.append(
            "commit mismatch: got %r, expected %r" % (data["commit"], expect_commit)
        )

    if expect_run_id is not None:
        got_run_id = data.get("run_id")
        if not isinstance(got_run_id, str) or not got_run_id.strip():
            reasons.append(
                "run_id missing - cannot prove this result was written by this run"
            )
        elif got_run_id != expect_run_id:
            reasons.append(
                "run_id mismatch: result belongs to %r, this run is %r - "
                "leftover output is not evidence" % (got_run_id, expect_run_id)
            )

    if str(data["finished_at"]) <= str(data["started_at"]):
        reasons.append("finished_at is not later than started_at")

    return Verdict(not reasons and not soft, reasons, data, soft)


def load_json(path):
    """Load a JSON file, returning None on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001
        return None
