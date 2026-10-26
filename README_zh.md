# AI 兵工厂构建器 v2

> 在 Windows 上用 Docker `sbx` 沙箱搭一个专用 AI 测试平台：
> DeepSeek Harness 在宿主，Claude Code 在沙箱里跑测试，**判定权留在宿主**。

![Platform](https://img.shields.io/badge/Platform-Windows-blue)
![Runtime](https://img.shields.io/badge/Python-3.9%2B%20stdlib-brightgreen)
![Sandbox](https://img.shields.io/badge/Sandbox-Docker%20sbx-blue)

## 核心设计

一句话：**沙箱只产数据，宿主才下结论。**

- 沙箱内 Agent 跑测试，产出 `result.json`（结构化）+ `report.md`（给人看）
- 宿主用 `sbx cp` **把结果拉回来**，按契约校验，自己算 Pass/Fail
- 退出码是唯一信号：`0` 通过 / `1` 失败 / `2` 配置错误

绝不解析报告正文，绝不让沙箱写结论。无法确认的状态一律判 FAIL。

## v2 相对 v1 修了什么

| v1 的问题 | v2 的处理 |
| --- | --- |
| 测试全挂仍打印「流水线执行完毕」，退出码 0 | 退出码贯通：`runner.py` 返回真实判定，`bat` 逐段检查 |
| `assert_report.py` 零个 `assert`，门禁是句 `print` | 门禁换成确定性契约校验 `arsenal_contract.py` |
| 结果文件缺失 → 静默 `return`，退出码 0 | 缺文件即 FAIL |
| 沙箱直写宿主挂载目录，宿主直接采信 | 沙箱只写自己 `/tmp`，宿主 `sbx cp` 拉取 |
| Key 拼进命令行（`sbx exec -e KEY=VALUE`） | 改 `--env-file` + 0600 临时文件，用完即删 |
| 强制网络策略选 `Open`（全放行） | 只放行 `api.deepseek.com`，用 `sbx policy allow` |
| 让用户跑 `sbx setup`（实验性，会导入全局凭据） | 明确禁止，文档写清原因 |
| `templates/` 目录不存在，中英两套分叉 | 统一布局：文档与启动器在根、脚本在 `templates/` |
| `runner` 写 `/c/...`、`assert` 读 `C:\...` | 单一配置项 `project_dir`，内部自动转换路径 |
| `requests` 依赖零声明 | 全标准库，零第三方依赖 |
| README 在代码块中间截断 | 完整，含自检与卸载 |
| frontmatter 不在首字节、name 与目录名不符 | `name: arsenal-builder`，frontmatter 位于文件首字节 |

## 快速开始

1. 装好 Docker Desktop 和独立版 `sbx` CLI（MSI），`sbx version` 能出版本号。
2. 设用户级环境变量：`ANTHROPIC_AUTH_TOKEN` = 你的 DeepSeek Key。
3. 只放行一个端点：

   ```powershell
   sbx policy init balanced
   sbx policy allow network api.deepseek.com
   sbx policy check network api.deepseek.com   # 必须打印 Allowed
   ```

4. 建配置。文档、配置和启动器在根目录，脚本在 `templates\`：

   ```powershell
   copy config.example.json config.json
   notepad config.json    # 改 project_dir（填被测仓库根目录，须含 .git）
   ```
   顺手看一眼 `report_language`：默认就是简体中文，Agent 会照这个语言写报告。

5. 先自检门禁（不碰真沙箱，几秒钟）：

   ```powershell
   python templates\selftest.py
   ```

6. 跑流水线。双击 `start_arsenal.bat`，或者：

   ```powershell
   .\start_arsenal.bat
   ```

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | PASS —— 计数自洽、无失败、无阻塞 |
| 1 | FAIL —— 测试失败／计数不符／结果缺失／零用例／commit 不符／Agent 崩溃 |
| 2 | 配置错误 —— 缺 sbx、缺 Key、项目路径错、配置不安全、运行中途异常 |
| 3 | BLOCKED —— 没有用例失败，但有一部分没跑（缺依赖／网络被封）。不是通过，也不是失败 |

每次运行产出 `out/<run_id>/`，含 `summary.json`、`agent.log`，以及整个沙箱输出目录
`sandbox/arsenal-out/`——不是两个写死的文件名，额外日志和覆盖率也会一起带回来。
**运行目录绝不会是空的**：中途崩掉的运行也会留下 `summary.json` 说明原因。
下游（DSH、CI）只读 `summary.json` 和退出码。

宿主那份丢了不用重跑测试：

```
python templates/runner.py --config config.json --collect
```

## 目录

```
arsenal-builder/
├── SKILL.md               技能说明（英文）
├── SKILL.zh-CN.md         技能说明（中文）
├── README.md              English readme
├── README_zh.md           本文件
├── CHANGELOG.md           变更记录
├── LICENSE
├── PROMPT.md              给沙箱 Agent 的提示词
├── start_arsenal.bat      一键启动
├── config.example.json    配置样例
├── config.json            你的配置（从样例复制）
├── result.schema.json     契约 JSON Schema
└── templates/
    ├── runner.py             驱动沙箱、拉结果、定退出码
    ├── arsenal_contract.py   契约校验
    ├── review.py             顾问式点评（不影响判定）
    └── selftest.py           19 场景自检
```

## 安全边界（读一遍再上生产）

- 沙箱内 Agent 以 `--dangerously-skip-permissions` 运行，会**无条件执行**它认为要执行的命令
- 因此被测项目必须是你的、且依赖来源可信；「安装依赖」本身就会执行第三方安装脚本
- 默认 `--clone` 模式：宿主仓库只读挂载 + 容器内私有克隆，副作用回不到你的工作区
- 网络只放行 DeepSeek 端点，其余默认拒绝
- 宿主永不信沙箱给的文字结论，只信 `result.json` 的计数和 `summary.json`

## 卸载

```powershell
sbx rm test-sandbox
```

删除工作目录即可，没有向系统安装任何东西。

## License

MIT
