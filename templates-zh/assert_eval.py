import os
import requests
import sys

DEEPSEEK_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN")
if not DEEPSEEK_KEY:
    print("❌ ANTHROPIC_AUTH_TOKEN environment variable not found.")
    sys.exit(1)

REPORT_PATH = r"C:\ai-arsenal\sandbox-workspaces\vecRecall_test_report.md"

def main():
    if not os.path.exists(REPORT_PATH):
        print(f"❌ Report file not found: {REPORT_PATH}")
        sys.exit(1)
        
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report_content = f.read()

    print("🧠 Calling DeepSeek for Quality Gate review...")
    
    headers = {
        "x-api-key": DEEPSEEK_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    payload = {
        "model": "deepseek-flash",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": f"你是资深测试架构师。请审查以下测试报告，给出最终结论。\n\n要求：\n1. 测试是否全部通过？\n2. 有没有被阻塞的问题？\n3. 最终结论是 Pass 还是 Fail？\n\n报告内容：\n{report_content}"}]
    }

    try:
        resp = requests.post("https://api.deepseek.com/anthropic/v1/messages", headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        
        answer = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                answer += block.get("text", "")
                
        print("\n" + "="*50)
        print("🎯 DSH Quality Gate Review Results:")
        print("="*50)
        print(answer)
        print("="*50)
    except Exception as e:
        print(f"❌ API request failed: {e}")
        if 'resp' in locals(): print(f"Error details: {resp.text}")

if __name__ == "__main__":
    main()