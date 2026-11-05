"""Host-side parsing of the raw test logs the pipeline pulled back.

The agent's result.json is self-reported. This module is the counterweight:
it reads the verbatim logs (which the prompt requires the agent to leave
behind, unedited) and derives counts the host can compare against the claim.
Only standard formats are understood - a self-written harness whose output
nothing can parse simply produces no evidence, and precision=standard then
cross-checks only what exists. Absence of evidence is not evidence of fraud.

Conservative by design: mismatches become *suspects*. Under
precision=standard a suspect demotes PASS to BLOCKED - a parser that
misreads an exotic log format must not brand an honest run broken. Under
precision=strict the buyer knows the price: the claim must survive the
evidence, so a suspect fails the run outright.
"""

import os
import re

# Suite logs are named <something>.log; only files starting with "test" are
# parsed - benchmark or env logs carry numbers that mean something else.
LOG_PREFIX = "test"

_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests?", re.M)
_UNITTEST_TAIL = re.compile(
    r"^FAILED \((.*)\)", re.M
)
_PYTEST_SUMMARY = re.compile(
    # pytest prints the final line decorated (`==== 3 failed in 0.4s ====`)
    # on a tty but bare (`3 failed in 0.4s`) when redirected to a file - and
    # redirected is exactly how the agent captures it (2026-11-05: the bare
    # form parsed as None and the whole pytest branch went untested).
    r"^(?:=+\s*)?(.*?)\s+in\s+[\d.]+s\s*(?:=+\s*)?$",
    re.M,
)
_PYTEST_ITEM = re.compile(r"(\d+) (passed|failed|skipped|errors?|error)")
_COUNTER = re.compile(r"(\d+)\s*/\s*(\d+)")


def _from_unittest(text):
    match = _UNITTEST_RAN.search(text)
    if not match:
        return None
    total = int(match.group(1))
    failed = skipped = 0
    tail = _UNITTEST_TAIL.search(text)
    if tail:
        for part in tail.group(1).split(","):
            piece = part.strip()
            m = re.match(r"(\w+)=(\d+)$", piece)
            if not m:
                continue
            kind, count = m.group(1), int(m.group(2))
            if kind in ("failures", "errors"):
                failed += count
            elif kind == "skipped":
                skipped += count
    if re.search(r"^OK\b", text, re.M):
        failed = skipped = 0
    return {
        "format": "unittest",
        "total": total,
        "passed": max(total - failed - skipped, 0),
        "failed": failed,
        "skipped": skipped,
    }


def _from_pytest(text):
    match = _PYTEST_SUMMARY.search(text)
    if not match:
        return None
    summary = match.group(1)
    if not re.search(r"\b(passed|failed|skipped|error)", summary):
        return None
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for count, kind in _PYTEST_ITEM.findall(summary):
        # errors are failures with a traceback: counting them as neither
        # passed nor failed would understate total and hide the bad news
        if kind.startswith("skip"):
            counts["skipped"] += int(count)
        elif kind.startswith("error"):
            counts["failed"] += int(count)
        elif kind in counts:
            counts[kind] += int(count)
    total = sum(counts.values())
    if not total:
        return None
    return dict(format="pytest", total=total, **counts)


def _from_counter(text):
    """Self-written harnesses print `148/148`. Compare the passed count only -
    skips live outside the counter, so deriving `failed` would be a lie."""
    best = None
    for passed, total in _COUNTER.findall(text):
        passed, total = int(passed), int(total)
        if 0 < passed <= total:
            best = (passed, total)
    if not best:
        return None
    passed, total = best
    return {
        "format": "counter",
        "total": total,
        "passed": passed,
        "failed": 0,
        "skipped": 0,
    }


def parse_log_text(text):
    for parser in (_from_unittest, _from_pytest, _from_counter):
        parsed = parser(text)
        if parsed:
            return parsed
    return None


def parse_test_logs(run_dir):
    """Parse every <run_dir>/**/logs/test*.log the run pulled back."""
    suites = []
    for root, dirs, files in os.walk(run_dir):
        if os.path.basename(root) != "logs":
            continue
        for name in sorted(files):
            if not name.startswith(LOG_PREFIX) or not name.endswith(".log"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            parsed = parse_log_text(text)
            if parsed:
                parsed["suite"] = name[: -len(".log")]
                suites.append(parsed)
    return suites


def cross_check(suites, claimed):
    """Suspects where the raw logs disagree with the agent's counts.

    Every check compares only what the logs actually establish; a suite log
    nothing could parse contributes nothing and triggers nothing.
    """
    suspects = []
    if not suites or not isinstance(claimed, dict):
        return suspects

    parsed_failed = sum(s.get("failed", 0) for s in suites)
    parsed_passed = sum(s.get("passed", 0) for s in suites)
    claimed_failed = claimed.get("failed", 0)
    claimed_passed = claimed.get("passed", 0)
    claimed_skipped = claimed.get("skipped", 0)
    claimed_total = claimed.get("total", 0)

    if parsed_failed > 0 and claimed_failed == 0:
        suspects.append(
            "raw logs show %d failing test(s) but result.json claims 0 failed "
            "- possible false green" % parsed_failed
        )
    if claimed_skipped > 0 and not claimed.get("blocked"):
        suspects.append(
            "%d test(s) skipped but `blocked` carries no reasons - skips "
            "without reasons are unverified, not passed" % claimed_skipped
        )
    if claimed_total and claimed_skipped > claimed_total // 2:
        suspects.append(
            "skip rate %.0f%% (%d of %d) - a mostly-skipped suite is mostly "
            "unverified" % (100.0 * claimed_skipped / claimed_total,
                            claimed_skipped, claimed_total)
        )
    if parsed_passed and claimed_passed and parsed_passed != claimed_passed:
        suspects.append(
            "raw logs account for %d passed but result.json claims %d"
            % (parsed_passed, claimed_passed)
        )
    return suspects
