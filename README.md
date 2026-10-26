# 🏭 AI Arsenal Builder

> An isolated, AI-powered automated testing platform for Windows using Docker `sbx`, Claude Code, and DeepSeek.

![Platform](https://img.shields.io/badge/Platform-Windows-blue)
![AI](https://img.shields.io/badge/AI-DeepSeek-green)
![Docker](https://img.shields.io/badge/Docker-sbx-blue)

## 🌟 Features
- **Zero-Trust Isolation**: Runs tests inside a Docker microVM (`sbx`).
- **Cost-Effective**: Uses DeepSeek API for execution and quality review.
- **Fully Automated**: Double-click a `.bat` file to run tests, generate reports, and get a Pass/Fail conclusion.
- **AI Skill Ready**: Includes `SKILL.md` for AI agents to auto-configure this platform.

## 📦 What's Included
- `SKILL.md` (English) / `SKILL.zh-CN.md` (Chinese)
- `templates/runner.py`: The foreman that commands the sandbox.
- `templates/assert_eval.py`: The AI quality gate reviewer.
- `templates/start_arsenal.bat`: One-click pipeline launcher.

## 🚀 Quick Start

1. Install Docker Desktop and the `sbx` CLI.
2. Set your DeepSeek API Key as a Windows environment variable:
   ```powershell
   [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "sk-your-key", "User")