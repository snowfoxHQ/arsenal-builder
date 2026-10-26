---
name: arsenal-builder
description: 在 Windows 上用 Docker sbx 沙箱搭建隔离式 AI 测试平台：沙箱内 Agent 跑测试并产出结构化结果，宿主机校验契约后判定 Pass/Fail。当需要搭建、修复或审查「AI 兵工厂」「sbx 沙箱测试」「测试门禁永远报成功」这类流水线时使用。
---

# AI 兵工厂搭建指南

一台机器上的专用测试平台：**DeepSeek Harness（宿主）** 驱动 **Docker sbx 沙箱（不可信）**，
沙箱内的 Claude Code Agent 跑测试，**判定权在宿主**。

让这套东西成立的一句话：**沙箱只产数据，宿主才下结论。** 不要让沙箱措辞，
更不要让一段散文决定通过与否。

## 什么时候用

- 「搭一个隔离测试平台」「构建 AI 兵工厂」「配置 sbx 沙箱」
- 「我的流水线永远报成功」「门禁从不失败」
- 修复已有的兵工厂（本 v2 替换早期版本）

## 架构

| 一侧 | 信任级别 | 职责 |
| --- | --- | --- |
| 宿主（DSH） | 可信 | 掌握判定权，读 `summary.json`，按退出码决策 |
| 沙箱（sbx） | 不可信 | 装依赖、跑测试、写 `result.json` + `report.md` |

数据是**宿主拉出来的**，不是沙箱推过去的：Agent 写入沙箱内 `/tmp/arsenal-out`，
宿主用 `sbx cp` 取回。沙箱不往宿主目录写任何东西。

## 第一阶段 — 宿主前置

1. Docker Desktop，以及 MSI 安装的独立版 `sbx` CLI。用 `sbx version` 确认。
2. 把 Key 写进 **用户级** 环境变量：`ANTHROPIC_AUTH_TOKEN` = 你的 DeepSeek Key。
3. **不要** 运行 `sbx setup`。它是实验性命令，没有网络策略这一步，
   而且会把宿主凭据导入全局 secret store。
4. 只放行一个端点，不要开网：

   ```
   sbx policy init balanced
   sbx policy allow network api.deepseek.com
   sbx policy check network api.deepseek.com
   ```

   最后一条必须打印 `Allowed`。若不是，停下来修规则，**绝不退回 allow-all**。

   测试要装依赖时，只缺包源会让那部分用例以 `blocked` 报回来（退出码 3）。
   需要的包源按需单独放行，例如：

   ```
   sbx policy allow network pypi.org
   sbx policy allow network files.pythonhosted.org
   ```

   注意 `sbx policy` 只在宿主可用，沙箱里没有这个命令——在沙箱里遇到
   「Approval required」是没法自己批的。

## 第一阶段半 — 一个根（三方合一）

项目、平台、报告**不放三个地方**。定一个根，三样东西各就各位：

```
<ARSENAL_HOME>\                    例如 C:\ai-arsenal
├── arsenal.cmd                     唯一入口（双击就跑）
├── config.json                     机器接线：测哪个项目、报告写哪
├── last-run.txt                    runner 写，指向最近一次运行
├── workspaces\vecRecall\           被测项目，自己带 .git
├── skills\arsenal-builder\         平台本体（本包解压到这）
├── reports\arsenal\<run_id>\       每次运行的产物
└── archive\                        旧版本、旧报告，只读封存
```

两条解析规则，记这两条就够：

| 路径 | 相对谁解析 | 例子 |
| --- | --- | --- |
| `prompt_file`（代码） | **skill 目录** `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`、`out_dir`（数据） | **根** `%ARSENAL_HOME%` | `workspaces/vecRecall`、`reports/arsenal` |

根从哪来：`--root` > 环境变量 `ARSENAL_HOME` > 本包所在目录（单机试跑）。
设成用户级环境变量一次，之后从任何目录调都能跑对：

```bat
setx ARSENAL_HOME "C:\ai-arsenal"
```

为什么必须分开：报告若写在包目录里，下一次解压新版本覆盖旧版本，报告就没了。
同理 `config.json` 放在根而不是包里——升级不该覆盖你的接线。

**沙箱绑定校验**：`sbx` 沙箱一辈子只绑一个宿主目录。复用绑定到别的项目的沙箱
会去测错的树（本项目就踩过：报告里跑的是 `sandbox-workspaces/vecRecall`，
而不是指定的 `workspaces/vecRecall`）。现在 `runner.py` 用
`sbx ls --format json` 读出实际绑定，与 `project_dir` 不一致直接退出码 2，
要求 `--recreate`。

## 第二阶段 — 安装脚本

包内布局：**文档与启动器在包根目录，所有 `.py` 脚本在 `templates/`**。
解压到 `<ARSENAL_HOME>\skills\arsenal-builder\`：

```
start_arsenal.bat     一键启动（也可由根目录 arsenal.cmd 调）
PROMPT.md             交给沙箱 Agent 的提示词
config.example.json   复制成 <ARSENAL_HOME>\config.json 再改
result.schema.json    契约参考
templates/
  runner.py             驱动沙箱、拉回结果、决定退出码
  arsenal_contract.py   契约校验（门禁逻辑本体）
  review.py             DeepSeek 顾问式点评（永不影响判定）
  selftest.py           门禁自检（17 个场景）
```

然后 `copy config.example.json ..\..\config.json`，改掉 `project_dir`
（写相对路径 `workspaces/<repo>` 即可，相对根解析）。

报告语言由 `report_language` 控制，默认就是中文：

```json
"report_language": "简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）"
```

它只影响 `report.md` 和 `result.json` 里 `blocked` 的正常文字——**门禁从不解析报告**，
所以改语言不可能改变判定。想换英文就写 `"English"`。`review.py` 的顾问点评
默认中文，用 `--language` 改。

`project_dir` 要填**被测项目本身**，不是它的副本：

- 必须指向仓库根目录（即含 `.git` 的那层）。`workspace_mode` 默认是 `clone`，
  非 git 仓库会在预检阶段直接判失败——`sbx create --clone` 没有 `.git` 跑不了。
  项目本身不是 git 仓库的话，改用 `workspace_mode: "bind"`。
- 样例里故意写的是占位符 `C:/path/to/your/repo`。这个值没有合理默认，
  `runner.py` 在它被设成真实路径之前拒绝启动。

不需要任何第三方包，只用 Python 标准库。

## 第三阶段 — 建沙箱

首次运行 `runner.py` 会自动创建。默认 `--clone`：宿主仓库以**只读**方式挂载，
Agent 拿到容器内私有克隆，测试副作用碰不到你的工作区。

`workspace_mode: "bind"` 会把宿主目录**读写**挂载，必须显式打开
`"i_accept_bind_mount_risk": true` 才允许——设之前先想清楚。

## 第四阶段 — 运行与读结果

双击 `start_arsenal.bat`，或 `python runner.py --config config.json`。

退出码就是契约：

| 码 | 含义 |
| --- | --- |
| 0 | 判定 PASS —— 计数自洽、无失败、无阻塞 |
| 1 | 判定 FAIL —— 测试失败／计数不符／结果缺失／零用例／commit 不符／Agent 崩了，任一命中 |
| 2 | 配置错误 —— 缺 sbx、缺 Key、项目路径错、配置不安全、运行中途异常 |
| 3 | 判定 BLOCKED —— 没有用例失败，但有一部分**根本没跑**（缺依赖、网络被封）。不是通过，也不是失败 |

`blocked` 单独给一个码，是因为「测试挂了」和「测试没跑完」是两件事。
早先两者都报 FAIL，一次 220/220 全绿、只因缺 `cryptography` 跳了 10 条断言的运行，
看起来就像代码是坏的。`passed` 字段仍然只在 0 时为 `true`，任何只认退出码为 0
的脚本行为不变。

每次运行都会在 `out/<run_id>/` 留痕，**运行目录绝不会是空的**：

```
out/<run_id>/
  summary.json                     机器答案（判定、原因、计数、产物清单）
  agent.log                        沙箱 Agent 的完整输出（窗口被关也留得住）
  sandbox/arsenal-out/             沙箱输出目录整个搬回来，不只两个文件
    result.json  report.md  logs/…
out/latest.txt                     最近一次运行的目录路径
```

拉的是**整个目录**而不是两个写死的文件名：Agent 额外留下的原始日志、覆盖率、
环境清单都会自动带回来，不会因为宿主事先不知道名字而被悄悄丢掉。

**读 `summary.json`**，那才是机器答案：

```json
{ "state": "pass", "passed": true, "exit_code": 0,
  "reasons": [], "counts": {"total": 230, "passed": 230, "failed": 0, "skipped": 0},
  "artifacts": ["sandbox/arsenal-out/result.json", "sandbox/arsenal-out/logs/core.log"] }
```

## 数据没回来时

沙箱里的结果还在，但宿主 `out/` 是空的——通常是窗口被关、进程被杀，
拉回那一步没跑完。不用重跑测试（白烧 token），直接收：

```
python templates/runner.py --config config.json --collect
```

它跳过 Agent，只把沙箱里已有的输出重新取回、重新校验、重新落盘。


## 为什么门禁是 fail-closed

只要命中任意一条就判失败。这些都是真实故障模式，不是假想：

- `result.json` 缺失，或不是合法 JSON
- 必填字段缺失，或计数是布尔／字符串而不是整数
- `passed + failed + skipped != total`
- `total == 0` —— 空用例集不算通过
- `failed > 0` → FAIL；`blocked` 非空 → BLOCKED（3），两者都不是通过
- `commit` 为空，或与 `expect_commit` 不一致
- `finished_at` 不晚于 `started_at`
- Agent 进程退出码非 0

无法确认的状态一律不给 PASS。这就是全部要义：**一个说不出「失败」的测试平台，
比没有测试平台更糟，因为它制造虚假信心。**

## 红线

- **绝不** 用 `sbx setup` —— 实验性，且会静默导入宿主凭据
- **绝不** 把网络策略设成 allow-all / Open，要放行就加单个主机
- **绝不** 把 Key 写成命令行上的 `KEY=VALUE`，它会进进程表。
  `runner.py` 用 `--env-file` + 0600 临时文件
- **绝不** 让沙箱下结论。提示词禁止，宿主校验，`review.py` 只是顾问
- **绝不** 解析 `report.md` 做任何决定，那是给人看的散文

## 验证安装

不碰真沙箱就能验门禁：

```
python templates/selftest.py
```

十七 个场景必须全部 `ok`——十三判定／留痕用例，加一个路径约定检查、一个沙箱绑定检查，再加
summary.json / agent.log / 沙箱产物」的留痕检查，再加两个预检拒绝场景。
任何一条 `MISMATCH`，说明门禁坏了——别信它给出的 PASS。

## 卸载

```
sbx rm test-sandbox
```

然后删掉工作目录。没有向系统装任何东西。
