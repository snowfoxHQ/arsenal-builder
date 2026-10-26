> 🌐 [Read English Version](./SKILL.md)
---
name: ai-arsenal-builder
description: 在 Windows 上搭建基于 Docker sbx 和 DeepSeek 的隔离式 AI 自动化测试平台（兵工厂）。
---

# AI 兵工厂搭建指南

当用户要求“搭建隔离测试平台”、“配置 sbx 沙箱”或“构建 AI 兵工厂”时，请严格遵循以下步骤和红线规则：

## 🏗️ 第一阶段：底层环境配置
1. **环境准备**：确认用户已安装 Docker Desktop，并通过 MSI 安装了独立版 `sbx` CLI。
2. **初始化网络**：在终端运行 `sbx setup`。网络策略**必须**选择 `1 (Open)`，否则沙箱内无法访问外部大模型 API。
3. **目录规划**：在工作区创建独立目录，例如 `C:\ai-arsenal\sandbox-workspaces`。
4. **创建沙箱**：运行 `sbx create --name test-sandbox claude C:\ai-arsenal\sandbox-workspaces`。

## 🚨 第二阶段：避坑红线（绝对不要做）
- **绝对不要使用 `sbx secret set` 存储 DeepSeek 的 Key**：代理机制会导致 Claude Code 内部请求挂起（Hang）。
- **绝对不要让 DSH (DeepSeek Harness) 去执行外部 Shell**：DSH Desktop (Windows) 存在 `workspace-write` 权限 Bug (0xC0000142)，会导致子进程静默死亡。DSH 只能用来生成计划和读取报告。
- **环境变量注入**：每次通过 `sbx exec -e ANTHROPIC_AUTH_TOKEN=xxx -e ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"` 直接传参。
- **禁用终端交互**：必须给 Claude Code 加上 `--dangerously-skip-permissions` 参数，否则它会卡在“是否信任此文件夹”的等待中。

## 🛠️ 第三阶段：生成自动化脚本
1. 复制 `templates/runner.py` 到工作区，并提示用户修改目标项目路径。
2. 复制 `templates/assert_report.py` 到工作区，并提示用户修改报告路径。
3. 复制 `templates/start_arsenal.bat` 到工作区。
4. 提示用户将 `ANTHROPIC_AUTH_TOKEN` 设为 Windows 用户环境变量。

## 🚀 第四阶段：执行
让用户双击 `start_arsenal.bat` 即可实现：沙箱启动 -> 执行测试 -> 产出报告 -> DeepSeek 质量审查 的全自动闭环。