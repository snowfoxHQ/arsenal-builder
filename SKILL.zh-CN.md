---
name: arsenal-builder
description: 在 Windows 上用 Docker sbx 沙箱搭建隔离式 AI 测试平台：沙箱内 Agent 跑测试并产出结构化结果，宿主机校验契约后判定 Pass/Fail。当需要搭建、修复或审查「AI 兵工厂」「sbx 沙箱测试」「测试门禁永远报成功」这类流水线时使用。
---

# AI 兵工厂搭建指南

一台机器上的专用测试平台：**DeepSeek Harness（宿主）** 驱动 **Docker sbx 沙箱（不可信）**，
沙箱内的 Claude Code Agent 跑测试，**判定权在宿主**。

让这套东西成立的一句话：**沙箱只产数据，宿主才下结论。** 不要让沙箱措辞，
更不要让一段散文决定通过与否。

当前版本 **3.0.1**，每一版改了什么、为什么改，见 `CHANGELOG.md`。

## 什么时候用

- 「搭一个隔离测试平台」「构建 AI 兵工厂」「配置 sbx 沙箱」
- 「我的流水线永远报成功」「门禁从不失败」
- 修复已有的兵工厂（本包为 **3.0.1**，替换 v1/v2 版本）

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

根从哪来，按优先级：

1. 命令行 `--root <dir>`
2. 环境变量 `ARSENAL_HOME`
3. **按目录结构推断**：本包位于 `<根>\skills\arsenal-builder\` → 根就是上面两级
4. 都没有（随便解压到某个文件夹试跑）→ 退回包自己的目录

第 3 条是关键：**挪走整个文件夹不用改任何文件**。`C:\ai-arsenal` 搬到
`D:\arena`，根自动变成 `D:\arena`，因为约束是「skills 的上一级」这个几何关系，
不是某个字符串常量。所以环境变量是可选的，不是必需：

```bat
setx ARSENAL_HOME "C:\ai-arsenal"    :: 可选，只是让从任意目录调都更快命中
```

`runner.py --print-home` 打印它实际认出来的根，`start_arsenal.bat` 就是靠这个
拿根，而不是靠猜 `%~dp0` 往上数几层。

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
README.md             包说明（中文），README.en.md 是英文镜像
templates/
  runner.py             驱动沙箱、拉回结果、决定退出码
  arsenal_contract.py   契约校验（门禁逻辑本体）
  review.py             DeepSeek 顾问式点评（永不影响判定）
  selftest.py           门禁自检（21 个场景）
```

另外两个入口在**根**上，不在包里：`arsenal.cmd`（人唯一需要双击的那个，负责设好
`ARSENAL_HOME` 并透传所有参数）和 `install.bat`（幂等安装 + 自检）。

然后 `copy config.example.json ..\..\config.json`，改掉 `project_dir`
（写相对路径 `workspaces/<repo>` 即可，相对根解析）。

根目录的 `install.bat` 做同样的事，但幂等：建目录、补配置、跑自检，
反复运行没有副作用。`arsenal.cmd install` 是它的快捷方式。

报告由 `PROMPT.md` 里一份**九节大纲**规定结构，不是一句话交差。默认全写：

| # | 章节 | 里面必须有什么 |
| --- | --- | --- |
| 1 | 概览 | 这项目是什么、解决什么问题、核心设计原则（读源码，不是抄 README） |
| 2 | 技术栈 | 表：语言／声明要求／**实测**版本／打包／测试框架 |
| 3 | 模块结构 | 目录树，每个文件带 `wc -l` 实数行数与职责 |
| 4 | 依赖 | 逐项表：名称／必需或可选／状态／版本。**这张表决定两次运行可比** |
| 5 | 测试结果 | 逐套件：命令、`158/158` 这样的原始数字、退出码、**覆盖了什么**（分条） |
| 6 | 计数汇总 | 总／过／败／跳，整数 |
| 7 | 基准 | 有跑就列表并对比 README 声称的；没有就明写没有 |
| 8 | 未覆盖与风险 | 这次没验证什么、对采用者意味着什么 |
| 9 | 结论 | 事实归纳：哪些维度已验证、哪些没有。**禁止写 PASS/FAIL**，判定归宿主 |

报告用什么语言写，由 `report_language` 控制，默认中文：

```json
"report_language": "简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）"
```

想换英文就写 `"English"`，章节标题也跟着换。

`report_depth` 控制要不要写全：

```json
"report_depth": "full"    // full = 九节全写（默认）｜brief = 只写 1/5/6/8，给 CI 用
```

想临时切换不必改配置：`--report-depth brief`。

这两个都只影响 `report.md` 和 `result.json` 里 `blocked` 的正常文字——**门禁从不解析报告**，
所以改语言或改深度不可能改变判定。

**报告会单独体检**。跑完 `runner.py` 会数一遍报告有几节、多少表格行、多少字节，
不够就打印出来：

```
  report: 5 section(s), outline asks for 6
  report: 1863 B of prose, too little to explain anything (4000 B expected)
```

默认只警告、不改判定（测试是不是绿的和报告写得好不好是两件事）。
想让薄报告也算失败，加 `--strict-report`。

> 这条为什么存在：报告曾从 174 行 8194 字节悄悄缩到 51 行 2001 字节，
> 标题还在、内容全空，测试仍然全绿，**没有任何地方报警**——
> 直到有人打开文件发现里面只有计数。

`review.py` 的顾问点评默认中文，用 `--language` 改。

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

每次运行都会在 `out_dir` 下留痕——正经的根里就是 `reports\arsenal\<run_id>\`，
而且**运行目录绝不会是空的**：

```
<ARSENAL_HOME>\reports\arsenal\<run_id>\
  summary.json                     机器答案（判定、原因、计数、产物、五个绝对路径）
  agent.log                        沙箱 Agent 的完整输出（窗口被关也留得住）
  sandbox/arsenal-out/             沙箱输出目录整个搬回来，不只两个文件
    result.json  report.md  logs/…
<ARSENAL_HOME>\reports\arsenal\latest.txt   最近一次运行的目录路径
<ARSENAL_HOME>\last-run.txt                 同一个路径，放在根上给启动器用
```

两个指针是有意的：`latest.txt` 跟运行目录放一起，`last-run.txt` 放根上，
启动器不必知道 `out_dir` 指向哪、也不必解析谁的散文就能找到最新那次。

拉的是**整个目录**而不是两个写死的文件名：Agent 额外留下的原始日志、覆盖率、
环境清单都会自动带回来，不会因为宿主事先不知道名字而被悄悄丢掉。

**读 `summary.json`**，那才是机器答案：

```json
{ "run_id": "2026-10-26T22-41-08", "state": "pass", "passed": true, "exit_code": 0,
  "paths": { "home": "C:\\ai-arsenal", "skill": "C:\\ai-arsenal\\skills\\arsenal-builder",
             "project": "C:\\ai-arsenal\\workspaces\\vecRecall",
             "reports": "C:\\ai-arsenal\\reports\\arsenal",
             "run": "C:\\ai-arsenal\\reports\\arsenal\\2026-10-26T22-41-08" },
  "reasons": [], "counts": {"total": 230, "passed": 230, "failed": 0, "skipped": 0},
  "report_ok": true, "report_depth": "full", "report_problems": [],
  "artifacts": ["sandbox/arsenal-out/result.json", "sandbox/arsenal-out/logs/core.log"] }
```

`paths` 是一次运行碰到的五个绝对路径（根／包／项目／报告目录／本次运行），
不用再猜「测的是哪份副本、报告落在哪」——这个猜疑正是本平台曾经测错树的原因。
`report_ok` 与 `report_problems` 记录报告体检结果，除非加 `--strict-report`，
它们永远不改退出码。

## 数据没回来时

沙箱里的结果还在，但运行目录是空的——通常是窗口被关、进程被杀，
拉回那一步没跑完。不用重跑测试（白烧 token），直接收：

```
arsenal.cmd --collect
:: 或者不经过根启动器：
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

二十一 个场景必须全部 `ok`——十一个判定用例（通过／测试失败／依赖阻塞／结果缺失／
计数不符／零用例／Agent 崩溃／JSON 损坏／非 UTF-8 输出／commit 不符／短哈希容错），
加五个契约检查（运行必须留下 `summary.json`／`agent.log`／沙箱产物、数据与代码的路径
分离、根能不能从目录结构推出来、渲染后的提示词有无残留 `{{占位符}}` 并带上报告深度、
同一秒两次运行不许共用目录），加两个报告体检（薄报告要被标出、合格报告要被放行），
加一个沙箱绑定检查，再加两个预检拒绝场景。
任何一条 `MISMATCH`，说明门禁坏了——别信它给出的 PASS。

## 卸载

```
sbx rm <sandbox> --force
```

`--force` 是必须的：不带它会停在确认提示上等输入。然后删掉工作目录。
没有向系统装任何东西。
