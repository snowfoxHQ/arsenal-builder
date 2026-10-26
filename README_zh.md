# 🏭 AI 兵工厂构建器 (AI Arsenal Builder)

> 基于 Docker `sbx`、Claude Code 和 DeepSeek 构建的 Windows 隔离式 AI 自动化测试平台。

![Platform](https://img.shields.io/badge/Platform-Windows-blue)
![AI](https://img.shields.io/badge/AI-DeepSeek-green)
![Docker](https://img.shields.io/badge/Docker-sbx-blue)

## 🌟 核心特性
- **零信任隔离**：所有测试均在 Docker 微型虚拟机 (`sbx`) 内部运行，确保宿主机绝对安全。
- **高性价比**：底层调用 DeepSeek API，兼顾极高智商与极低的测试成本。
- **全自动闭环**：双击一个 `.bat` 文件，即可自动完成：拉起沙箱 -> 跑测试 -> 写报告 -> AI 质量门禁审查（给出 Pass/Fail 结论）。
- **AI 技能就绪**：内置 `SKILL.md`，AI Agent 可自动读取并帮你完成环境配置。

## 📦 包含内容
- `SKILL.md`（英文版）/ `SKILL.zh-CN.md`（中文版）
- `templates/runner.py`：指挥沙箱执行测试的“工头”。
- `templates/assert_eval.py`：负责读取报告并呼叫 DeepSeek 的“质检员”。
- `templates/start_arsenal.bat`：一键启动流水线。

## 🚀 快速开始

1. 确保已安装 Docker Desktop 和 `sbx` CLI。
2. 在 Windows 中配置你的 DeepSeek API Key 环境变量：
   ```powershell
   [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "sk-你的Key", "User")