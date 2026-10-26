> 🌐 [阅读中文版说明 (Chinese Version)](./SKILL.zh-CN.md)
---
name: ai-arsenal-builder
description: Build an isolated AI automated testing platform (Arsenal) on Windows using Docker sbx and DeepSeek.
---

# AI Arsenal Builder Guide

When a user asks to "set up an isolated testing platform", "configure sbx sandboxes", or "build an AI arsenal", follow these steps and avoid the critical pitfalls documented below.

## 🏗️ Phase 1: Infrastructure Setup
1. **Prerequisites**: Ensure the user has Docker Desktop installed and the standalone `sbx` CLI (installed via MSI).
2. **Network Initialization**: Run `sbx setup` in the terminal. **The network policy MUST be set to `1 (Open)`**; otherwise, the sandbox cannot access external LLM APIs.
3. **Directory Planning**: Create a dedicated workspace, e.g., `C:\ai-arsenal\sandbox-workspaces`.
4. **Create Sandbox**: Run `sbx create --name test-sandbox claude C:\ai-arsenal\sandbox-workspaces`.

## 🚨 Phase 2: Critical Pitfalls (DO NOT DO)
- **NEVER use `sbx secret set` for the DeepSeek Key**: The proxy mechanism causes Claude Code's internal requests to hang indefinitely.
- **NEVER let DSH (DeepSeek Harness) execute external shells directly**: DSH Desktop (Windows) has a `workspace-write` permission bug (0xC0000142) that silently kills subprocesses. DSH should only be used for generating plans or reading reports.
- **Environment Injection**: Pass the key directly via `sbx exec -e ANTHROPIC_AUTH_TOKEN=xxx -e ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"`.
- **Disable Terminal Interaction**: Always add the `--dangerously-skip-permissions` flag to Claude Code, otherwise it will hang at the "Do you trust this folder?" prompt.

## 🛠️ Phase 3: Generate Automation Scripts
1. Copy `templates/runner.py` to the workspace and prompt the user to modify the target project path.
2. Copy `templates/assert_eval.py` to the workspace and prompt the user to modify the report path.
3. Copy `templates/start_arsenal.bat` to the workspace.
4. Remind the user to set `ANTHROPIC_AUTH_TOKEN` as a Windows User Environment Variable.

## 🚀 Phase 4: Execution
Instruct the user to simply double-click `start_arsenal.bat`. This triggers the fully automated closed-loop: Sandbox Start -> Run Tests -> Generate Report -> DeepSeek Quality Review.