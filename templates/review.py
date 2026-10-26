#!/usr/bin/env python3
"""Advisory review of a completed run. Never changes the verdict.

The gate lives in runner.py and is deterministic. This script only adds
human-readable commentary from DeepSeek on top of an already-decided result.

It always exits 0 when it runs at all: a review that fails to be generated
must not turn a passing run into a failing one, and must not mask a failure
either. If you want to gate on this output, you are using the wrong tool.

Stdlib only - no third-party packages, no requirements.txt.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from arsenal_contract import load_json, validate
from runner import find_artifact

ENDPOINT = "https://api.deepseek.com/anthropic/v1/messages"
MODEL = "deepseek-flash"
KEY_ENV = "ANTHROPIC_AUTH_TOKEN"


def post_json(url, headers, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Advisory review of an arsenal run")
    parser.add_argument("run_dir", help="directory produced by runner.py")
    parser.add_argument("--expect-commit", default=None)
    parser.add_argument(
        "--language",
        default="简体中文",
        help="language for the commentary (default: 简体中文)",
    )
    args = parser.parse_args(argv)

    # The artifacts live under sandbox/arsenal-out/ after the directory copy,
    # so resolve them instead of assuming they sit at the run root.
    result_path = find_artifact(args.run_dir, "result.json")
    report_path = find_artifact(args.run_dir, "report.md")

    verdict = validate(result_path, args.expect_commit)

    print("=" * 62)
    print("确定性判定（宿主）：%s" % verdict.summary())
    for reason in verdict.reasons + verdict.blocked_reasons:
        print("  - %s" % reason)
    print("=" * 62)

    api_key = os.environ.get(KEY_ENV)
    if not api_key:
        print("顾问点评已跳过：环境变量 %s 未设置。" % KEY_ENV)
        return 0

    data = verdict.data or {}
    report = ""
    if os.path.isfile(report_path):
        with open(report_path, "r", encoding="utf-8", errors="replace") as handle:
            report = handle.read()[:12000]

    prompt = (
        "你在点评一次测试运行，这次运行已经由确定性检查器判过。不要复述、也"
        "不要推翻这个判定。\n\n"
        "机器判定：%s\n\n"
        "请用%s回答，不超过 200 字：\n"
        "1. 如果是失败或阻塞，指出最可能的根因。\n"
        "2. 指出证据里可疑或缺失的地方。\n"
        "3. 给一个具体可执行的下一步。\n\n"
        "报告原文：\n%s"
        % (verdict.summary(), args.language, report or "（没有 report.md）")
    )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }

    print("\n顾问点评（不影响判定）：")
    try:
        response = post_json(ENDPOINT, headers, payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        print("  不可用：HTTP %s %s" % (exc.code, detail))
        return 0
    except Exception as exc:  # noqa: BLE001 - advisory only
        print("  不可用：%s" % exc)
        return 0

    answer = "".join(
        block.get("text", "")
        for block in response.get("content", [])
        if block.get("type") == "text"
    )
    print(answer.strip() or "  （空响应）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
