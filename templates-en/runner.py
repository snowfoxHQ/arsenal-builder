import subprocess
import os
import sys

deepseek_key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
if not deepseek_key:
    print("❌ Missing ANTHROPIC_AUTH_TOKEN environment variable.")
    sys.exit(1)

PROJECT_NAME = "vecRecall" 
PROJECT_PATH = f"/C:/ai-arsenal/sandbox-workspaces/{PROJECT_NAME}"
REPORT_PATH = f"/C:/ai-arsenal/sandbox-workspaces/{PROJECT_NAME}_test_report.md"

print(f"🏭 Arsenal Foreman: Directing sandbox to test {PROJECT_NAME}...")

ENV_FILE_PATH = ".env.sbx"
with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
    f.write(f"ANTHROPIC_AUTH_TOKEN={deepseek_key}\n")

cmd = [
    "sbx", "exec",
    "--env-file", ENV_FILE_PATH, 
    "-e", "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic",
    "test-sandbox",
    "claude", "-p", f"检查 {PROJECT_PATH} 项目，分析技术栈，安装依赖并运行测试。注意：如果测试因为缺少可选依赖（如 cryptography）被跳过，请尽量安装这些依赖并重新运行，确保测试全部覆盖。最后将结果写入 {REPORT_PATH}。\n\nCheck the {PROJECT_PATH} project, run tests, and write results to {REPORT_PATH}. Note: If tests are skipped due to missing optional dependencies (e.g., cryptography), please install them and re-run to ensure full coverage.",
    "--dangerously-skip-permissions"
]

try:
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
    for line in process.stdout:
        print(line, end="")
    process.wait()
    print(f"\n✅ Execution finished. Exit code: {process.returncode}")
except Exception as e:
    print(f"\n❌ Execution failed: {e}")
finally:
    if os.path.exists(ENV_FILE_PATH):
        os.remove(ENV_FILE_PATH)