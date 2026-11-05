#!/usr/bin/env bash
# Arsenal pipeline launcher (POSIX twin of start_arsenal.bat).
# The exit code of this script IS the verdict:
#   0 pass / 1 fail / 2 setup error / 3 blocked (partial suite, nothing failed)
#
# Layout it expects:
#   $ARSENAL_HOME/config.json            machine wiring (project, report dir)
#   $ARSENAL_HOME/workspaces/<repo>/     project under test
#   $ARSENAL_HOME/reports/arsenal/       one directory per run
#   .../skills/arsenal-builder/templates/*.py   this code
# Or run the skill standalone: ARSENAL_HOME then falls back to this
# script's own directory.
set -u

PYDIR="$(cd "$(dirname "$0")/templates" && pwd)"
PKGDIR="$(dirname "$0")"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

AHOME="${ARSENAL_HOME:-}"
if [ -z "$AHOME" ]; then
    AHOME="$("$PY" "$PYDIR/runner.py" --print-home 2>/dev/null || true)"
fi
[ -n "$AHOME" ] || AHOME="$PKGDIR"
AHOME="${AHOME%/}"

CONFIG="$AHOME/config.json"
[ -f "$CONFIG" ] || CONFIG="$PKGDIR/config.json"

echo "=================================================="
echo " AI Arsenal - sandbox test pipeline"
echo " home: $AHOME"
echo " code: $PKGDIR"
echo "=================================================="
echo
echo "[1/2] driving the sandbox and validating the result..."
"$PY" "$PYDIR/runner.py" --config "$CONFIG" --root "$AHOME" "$@"
RC=$?

case "$RC" in
    0)
        # A dry run produced no run directory: reviewing the previous run
        # would be both misleading and slow. Stop after preflight.
        case "$*" in
            *--dry-run*)
                echo
                echo "=================================================="
                echo " RESULT: PASS (preflight only - nothing was executed)"
                echo "=================================================="
                exit 0
                ;;
        esac
        echo
        echo "[2/2] advisory review (does not affect the verdict)..."
        if [ -f "$AHOME/last-run.txt" ]; then
            RUNDIR="$(head -n 1 "$AHOME/last-run.txt")"
            "$PY" "$PYDIR/review.py" "$RUNDIR"
        else
            echo "review skipped: no run directory recorded"
        fi
        echo
        echo "=================================================="
        echo " RESULT: PASS"
        echo "=================================================="
        exit 0
        ;;
    3)
        echo
        echo "=================================================="
        echo " RESULT: BLOCKED - part of the suite never ran"
        echo " Nothing failed, so this is not a pass either."
        echo " See the reasons above, then check $AHOME/last-run.txt"
        echo "=================================================="
        exit 3
        ;;
    *)
        echo
        echo "=================================================="
        echo " RESULT: FAIL  (exit code $RC)"
        echo " Agent log and artifacts are in the run directory"
        echo " printed above; $AHOME/last-run.txt points at it."
        echo "=================================================="
        exit "$RC"
        ;;
esac
