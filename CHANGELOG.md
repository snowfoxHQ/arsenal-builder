# Changelog

## v2.0.0 — 2026-10-26

重写判定链路。核心变化：**沙箱只产数据，宿主才下结论。**

### 实测对照（同一套假 sbx，同一批场景）

| 场景 | v1 退出码 | v2 退出码 | 说明 |
| --- | --- | --- | --- |
| 测试失败（7/230 挂） | **0**（还打印「流水线执行完毕」） | **1** | v1 的 `runner.py` 打印了 137 却不 `sys.exit` |
| 结果文件缺失 | **0**（静默 `return`） | **1** | v1 把「没结果」当「没问题」 |
| Agent 被 OOM kill（rc=137） | **0**（还打印 ✅） | **1** | v1 只打印 returncode，不传播 |
| 计数不符（`passed+failed≠total`） | 无此检查 | **1** | v1 根本不读计数 |
| 零用例（`total=0`） | 无此检查 | **1** | 空用例集不是通过 |
| 非 UTF-8 输出 | **崩溃 rc=1**（误伤正常结果） | **0** | v1 缺 `errors='replace'` |
| 正常通过 | 0 | **0** | 两版一致 |

### 架构变更

- 沙箱内只写 `/tmp/arsenal-out`，宿主用 `sbx cp` **拉取**，不再直写宿主挂载目录
- 判定从「DeepSeek 读 md 散文」改为「宿主校验 `result.json` 契约」，AI 点评降级为
  `review.py` 顾问输出，**永不影响退出码**
- 新增 `summary.json`：`{passed, reasons, counts}`，供 DSH / CI 机器读取
- 新增 `selftest.py`：9 个场景自检，不碰真沙箱

### 安全变更

- 密钥从 argv 移出：改用 `--env-file` + 0600 临时文件，用完即删
  （`sbx exec -e KEY=VALUE` 会把密钥暴露在宿主进程表里）
- 网络策略从 `Open`（全放行）改为只放行 `api.deepseek.com`
- 明确禁止 `sbx setup`：实验性命令，会把宿主凭据导入全局 secret store，
  与「不要用 `sbx secret set`」自相矛盾
- 默认 `--clone`：宿主仓库只读挂载 + 容器内私有克隆；`bind` 模式需显式确认风险

### 工程变更

- 中英两套模板合并成单一 `templates/`（v1 因此出现 `assert_report.py` / `assert_eval.py` 分叉）
- 路径单一来源：一个 `project_dir` 配置项，内部自动做 `C:\x` → `/c/x` 转换
- 零第三方依赖：去掉 `requests`，改用 `urllib.request`
- 配置驱动：不再要求手改脚本里的常量
- `.bat` 补 `cd /d "%~dp0"`，并在两个阶段后都检查退出码
- frontmatter 移到文件首字节，`name` 与目录名一致
- README 补全（v1 两份都在第 25 行代码块中间截断）

### 真机验证（sbx v0.49.2）

- `sbx exec --env-file <f> test-sandbox printenv ARSENAL_PROBE` → 回显 `probe-value-123`，退出码 0
- 沙箱内 `/tmp/arsenal-out/` 对 `agent` 用户可写
- `sbx cp test-sandbox:/tmp/arsenal-out/result.json <host>` → 退出码 0，内容一致

### 升级提示

v1 的 `templates/` 与 v2 不兼容：`assert_report.py` 没有了，改为 `runner.py` 统一判定。
迁移步骤：把 v2 的 `templates/` 覆盖过去，重建 `config.json`，跑一次
`python templates/selftest.py` 确认 9 个场景全绿，再用 `--recreate` 重建沙箱。

## v2.1 — 2026-10-26 19:15

**修正 `project_dir` 的错误示例（用户发现）**

- `config.example.json` 与 `runner.py` 的 `DEFAULT_CONFIG` 里都硬编码了
  `C:\ai-arsenal\sandbox-workspaces\vecRecall`。真实问题有两个：
  ① 该目录没有 `.git`，`workspace_mode: "clone"` 必然失败；
  ② 示例文件里不该带某台机器的绝对路径。

  > **更正**：本条目初版曾断言「`C:\ai-arsenal` 是旧副本」「`sandbox-workspaces`
  > 是产出区不是源码」，两条都错了。`C:\ai-arsenal` 才是 arsenal-builder 主项目根目录
  > （`install.bat`、`VERSION.txt`、`engine\`、`sandbox-workspaces\` 都在其中），
  > `sandbox-workspaces` 就是它的测试工作区。据此已撤回 runner 里针对该路径名的特判提示。

- `DEFAULT_CONFIG["project_dir"]` 改为 `None`，并注释说明该值没有合理默认。
- `preflight()` 新增拦截：未设置 → 明确报错；目录不存在 → 报错；
  `clone` 模式但无 `.git` → 报错，并提示改用 `bind` 或先 `git init`。
- `config.example.json` 改用占位符 `C:/path/to/your/repo`。
- `selftest.py`：项目夹具补建 `.git`；新增 2 个预检场景（未设置 / 非 git），共 **11** 个场景。
- 文档同步（SKILL / SKILL.zh-CN / README / README_zh）。

实测：`C:\ai-arsenal` 在 `clone` 模式下 → 退出码 2，提示无 `.git`；
建好 `.git` 的目录 → 预检通过。

## v2.2 — 2026-10-26 19:32

**`expect_commit` 改为容错比对（潜伏 bug）**

- `arsenal_contract.py` 原来用精确字符串比对：`data["commit"] != expect_commit`。
  Agent 按契约报的是 `git rev-parse HEAD` 的 40 位完整哈希，而人在配置里通常只填
  7 位短哈希 —— 明明 revision 正确，却会判 `commit mismatch` 并 FAIL。
- 新增 `same_commit()`：任一侧是另一侧的前缀且长度 ≥ 7 即判定相同；完全不匹配时
  仍然 FAIL（fail-closed 不变）。
- `selftest.py` 新增场景 `abbrev commit ok`（结果为完整哈希 / 期望为 7 位 → 退出码 0），
  共 **12** 个场景。

## v2.3.1 — 2026-10-26 19:33

**包根目录补 `start_arsenal.bat` 转发入口（用户反馈：根目录找不到启动文件）**

- 原布局只有 `templates\start_arsenal.bat`，包根目录没有任何可双击的入口，
  用户打开包以后必须记住 `templates\` 这一层才找得到。
- 新增根目录 `start_arsenal.bat`：`cd /d "%~dp0"` 后 `call templates\start_arsenal.bat`，
  原始退出码 `0/1/2` 原样转发；`templates\` 缺失时打印错误并返回 `2`。
- 实测：内部返回 0/1/2/7，外层分别得到 0/1/2/7；`templates\` 删除后外层得到 2。
- README.md / README_zh.md 快速开始与目录树同步。

## v2.3.2 — 2026-10-26 19:48

**`sbx rm` 缺 `--force`，双击运行时必然卡死（阻断级）**

- `runner.py:remove_sandbox()` 调用 `sbx rm <name>` 不带 `--force`。
  官方 `sbx rm --help` 原文：“Removal requires confirmation; use `--force` to skip
  confirmation prompts (for non-interactive scripts)”。
- 双击 `.bat` 时 stdin 是真控制台但无人输入 → 卡在确认提示上，流水线不动、也不报错。
- 已改为 `sbx rm --force <name>`。
- 另：`config.json` 的 `sandbox` 改为 `arsenal-test`、`recreate` 改为 `false`。
  原因：原 `test-sandbox` 是 bind 方式建的（挂 `Documents\...\sandbox-workspaces`，
  里面有既有数据），用 `recreate` 删它既有数据风险，也不必要 —— 换个新名字直接
  用 `--clone` 建，旧沙箱原样保留。
- 未新增自检场景（该分支需要真实 `sbx rm` 语义，假 sbx 覆盖不到）。

## v2.4 — 2026-10-26 19:55

**去掉 `templates/` 层，包改成扁平结构（用户要求：根目录必须能直接 `python selftest.py`）**

- 之前所有脚本都在 `templates\` 下，在包根目录执行 `python selftest.py` 会报
  `can't open file`。用户前后三次被这一层绕住，结构本身就是错的。
- 全部文件提到根目录：`arsenal_contract.py` / `runner.py` / `review.py` /
  `selftest.py` / `PROMPT.md` / `config.example.json` / `result.schema.json` /
  `start_arsenal.bat`，`out/` 一并上移。
- 根目录原来的「转发器」`start_arsenal.bat` 被真正的启动器覆盖（不再需要转发）。
- `runner.py` 新增 `BASE_DIR` + `resolve_from_base()`：配置里的相对路径
  （`prompt_file`、`out_dir`）改为相对**脚本所在目录**解析，而不是 cwd。
  这样从任何目录调用都能用，不再依赖「你正好站在包目录里」。
- 文档同步：SKILL.md / SKILL.zh-CN.md / README.md / README_zh.md 里所有
  `templates/` 路径与目录树全部改写。
- 实测：根目录 `python selftest.py` → 12/12 ok；`python runner.py --config
  config.json --dry-run` → 退出码 0；从 `/tmp` 用绝对路径调用 → 同样退出码 0。
- 工作副本里残留一个**空的** `templates\` 目录，系统的安全删除（回收站）故障
  导致删不掉，需手动右键删除；zip 里不会包含它（打包只写文件）。

## v2.5 — 2026-10-26 20:05

**按用户指定的布局重排：`.py` 进 `templates/`，文档与启动器留在根目录**

```
arsenal-builder/
├── SKILL.md  SKILL.zh-CN.md  README.md  README_zh.md  CHANGELOG.md  LICENSE
├── PROMPT.md  start_arsenal.bat
├── config.example.json  config.json  result.schema.json
└── templates/
    ├── runner.py  arsenal_contract.py  review.py  selftest.py
```

- `start_arsenal.bat` 改为 `set "PYDIR=%~dp0templates"` +
  `python "%PYDIR%\runner.py"` / `python "%PYDIR%\review.py"`。
- `runner.py` 的 `BASE_DIR` 改为「脚本在 `templates/` 时取上一级」，
  因此 `prompt_file`（PROMPT.md）、`out_dir`（out/）仍解析到**包根目录**，
  和配置、产物位置一致。
- `selftest.py` 的 `prompt_file` 改用 `runner.BASE_DIR`，不再用自身所在目录。
- 文档同步（四份）：快速开始、目录树、第二阶段安装说明、自检命令。
- 注意：现在**根目录 `python selftest.py` 不再可用**，命令是
  `python templates\selftest.py`（这是该布局的必然结果，非缺陷）。

实测：根目录 `python templates\selftest.py` → 12/12 ok；
`--dry-run` → 0；用假 `sbx` 端到端跑**未修改的** `start_arsenal.bat`：
测试失败 → 1，全部通过 → 0，结果缺失 → 1（生成到 `out\<run_id>\`）。

## v2.6 — 2026-10-26 20:20

**修复「跑完测试，宿主看不到数据」**

现象：19:52 那次真跑（沙箱 `arsenal-test`，被测 `sandbox-workspaces/vecRecall`），
沙箱内 `/tmp/arsenal-out/` 明明有 `result.json`（230 断言／220 通过／10 跳过）、
`report.md`、`core.log`、`blockchain.log`，宿主 `out/20261026-195204/` 却是**空目录**，
连 `summary.json` 都没有。数据没生成是假象——数据生成了，只是没搬回来。

四个原因，逐个修：

1. **取回只认两个文件名**：`fetch_file` 只搬 `result.json` / `report.md`，
   Agent 额外写的 `core.log`、`blockchain.log` 直接被丢掉。
   → 改为 `sbx cp` **整个输出目录**到 `out/<run_id>/sandbox/arsenal-out/`，
   任何额外产物（原始日志、覆盖率、环境清单）自动跟回来。
2. **落盘在最后一步，中途被杀就什么都不留**：`summary.json` 只在跑完全程后写。
   → 改为 no matter what 都写（`try/except/finally` 结构），运行目录永不为空；
   出错时 `state: "error"` + `error` 字段说明原因。
3. **Agent 输出只进控制台**：窗口一关，跑过什么全没了。
   → 边跑边 tee 到 `out/<run_id>/agent.log`。
4. **`blocked` 与 `failed` 共用 FAIL**：220/220 全绿、只因缺 `cryptography`
   跳了 10 条断言，退出码就是 1，读起来像代码坏了。
   → 新增退出码 **3 = BLOCKED**（`state` 三态 pass/fail/blocked、
   `passed` 仍只在 0 时为 true）。`start_arsenal.bat` 增加 `RESULT: BLOCKED` 分支。

新增 `--collect`：跳过 Agent，只把沙箱里**已有**的输出重新取回＋重新校验＋重新落盘。
窗口被关、进程被杀导致宿主丢数据时，不用重跑测试（省 token）。

- `PROMPT.md` 增加第 5 步：要求 Agent 把每条测试命令的原始 stdout/stderr 写进
  `{{OUT_DIR}}/logs/<suite>.log`，环境版本写进 `logs/env.txt`，不许总结、不许清理。
- `selftest.py` 12 → **14** 场景：新增「audit trail on host」（检查运行目录里
  `summary.json` / `agent.log` / `sandbox/arsenal-out/result.json` 都在）、
  「deps blocked」（期望退出码 3）。假 `sbx` 支持目录拷贝。
- 四份文档同步：退出码表加 3、`out/<run_id>/` 结构、`--collect` 用法、
  `blocked` 需单独放行 pypi 源（`sbx policy allow network pypi.org`）。

实测：`python templates\selftest.py` → **14/14 ok**；
`--collect` 对真沙箱 `arsenal-test` → 取回 4 个文件、判定 BLOCKED、退出码 3；
异常路径（沙箱不存在）→ 退出码 2 且 `summary.json` 落盘。

## v2.7 — 2026-10-26 20:45

**报告语言可配置，默认中文**

现象：v2.6 那次真跑的 `report.md` 通篇英文，只有一行中文（
「（跳过：未安装 cryptography…）」是测试脚本自己打的）。报告是给人看的，
看不懂等于没有。

- `PROMPT.md` 新增 `## Language` 段落 + `{{REPORT_LANGUAGE}}` 占位符：
  `report.md` 和 `result.json` 的 `blocked` 文案都按该语言写；
  代码、路径、命令、测试名、版本号一律保留原文，中英混排是允许的，
  **整篇英文不允许**。
- 新增配置项 `report_language`，默认
  `"简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）"`。
  它只影响报告散文，门禁从不解析报告，改语言不可能改变判定。
- `review.py` 点评改为中文：提示词、标题（「确定性判定（宿主）」「顾问点评
  （不影响判定）」）、`--language` 参数（默认 `简体中文`）。
- **修一个 v2.6 引入的回归**：`review.py` 还在按 `run_dir/result.json` 找文件，
  而产物已经搬到 `run_dir/sandbox/arsenal-out/` 下，导致点评永远读不到报告。
  改为复用 `runner.find_artifact`。
- `selftest.py` 15 场景：新增「prompt fully rendered」，扫描渲染后的提示词里
  是否残留 `{{...}}`（残留会让 Agent 往一个字面叫 `{{RESULT_PATH}}` 的路径写文件，
  然后宿主判它没交结果），并确认 `report_language` 非空。
- 文档同步：`SKILL.md` / `SKILL.zh-CN.md` 加「报告语言」说明，
  两份 README 的快速开始各加一句。

实测：`python templates\selftest.py` → **15/15 ok**；
`python templates\review.py <run_dir>` → 正确从 `sandbox/arsenal-out/` 读到报告，
输出「确定性判定（宿主）：BLOCKED」+ 中文点评；
当前那次运行的英文报告已补一份 `report.zh.md`（同目录）供直接阅读。
## v2.8 — 2026-10-26 21:15

**三方合一：一个根，而不是五个散点**

用户发现"被测项目在一处、报告在一处、技能版本又在第三处"。核对后实际是**五处**：

| # | 位置 | 是什么 | 问题 |
| --- | --- | --- | --- |
| 1 | `C:\ai-arsenal\workspaces\vecRecall` | 被测项目真身（57 文件） | 没有 `.git`，`clone` 模式跑不了 |
| 2 | `C:\ai-arsenal\sandbox-workspaces\vecRecall` | 同一份的**完整副本**（diff 无差异） | 那次测试实际测的是这份，不是① |
| 3 | `C:\ai-arsenal\skills\arsenal-builder` | v1 平台（`templates-en` / `templates-zh`） | 与 v2 结构不兼容，早就该归档 |
| 4 | `C:\tmp\arsenal-builder-v2\arsenal-builder` | v2.7 工作副本 + `out\` 数据 | 报告写在**包里**，重装即丢 |
| 5 | `Documents\技能插件库\ai-arsenal\skills` | v1→v2.7 共 10 个 zip | 版本信息靠文件名，无 tag |

而且 `config.json` 的 `project_dir` 写的是 `C:/ai-arsenal`（整个大目录），
不是 `workspaces\vecRecall`。`--clone` 于是克隆了 1.8 万个文件，
Agent 在克隆里**自己找**到了 `sandbox-workspaces/vecRecall` 并测了它 ——
看起来一切正常，测的根本不是指定项目。

### 变更

- **路径契约拆成两半**：`prompt_file` 相对 **skill 目录**（代码），
  `project_dir` / `out_dir` 相对 **ARSENAL_HOME**（数据）。
  根优先级：`--root` > `$ARSENAL_HOME` > 包目录。全部 `normpath` 归一。
- `summary.json` 新增 `paths`：`home / skill / project / reports / run` 五个绝对路径，
  一眼看清这次到底动了哪三处。
- 运行目录一建立就写 `state: "running"` 的 `summary.json`，结束覆盖。
  硬杀（进程树被 kill）也留得下"没跑完"这个事实，不再是一片空白。
- `last-run.txt` 写在根上，启动器据此找最近一次运行，不用解析程序散文。
- **沙箱绑定校验**：用 `sbx ls --format json` 读出沙箱实际绑定的宿主目录，
  与 `project_dir` 不一致就退出码 2、提示 `--recreate`。
  `--collect` 只告警不拦（回收数据不受此限）。这条直接堵住"测错树"。
- `sbx exec` 加 `stdin=DEVNULL`：非交互调用不再等 3 秒并吐
  `no stdin data received` 告警。
- `start_arsenal.bat` 重写：`ARSENAL_HOME` 优先、`config.json` 从根读（包里那份只作兜底）、
  参数透传 `%*`（`arsenal.cmd --dry-run` 这类用法才成立）。
- 新增 `arsenal.cmd`（根目录唯一入口）：设好 `ARSENAL_HOME` 后调技能内的 bat，
  退出码原样传出。
- `selftest.py` 17 场景：新增「sandbox for other repo」（错绑 → 2）、
  「home path contract」（相对路径必须落到 home 下、绝对路径不被改写、
  `--root` 压过 `$ARSENAL_HOME`、都没设时退回包目录）。假 `sbx` 支持
  `ls --format json` 与 `workspace` 状态文件。

### 落地（本次实际执行）

```
C:\ai-arsenal\
├── arsenal.cmd                  入口
├── config.json                  project_dir=workspaces/vecRecall  out_dir=reports/arsenal
├── last-run.txt
├── workspaces\vecRecall\        真身，已 git init（e63da07），57 文件
├── skills\arsenal-builder\      v2.8（旧的 v1 已移至 archive\skills\arsenal-builder-v1）
├── reports\arsenal\<run_id>\    报告归档（原 out\ 里的数据已回收进来）
└── archive\skills\              旧 v1 目录 + v1 zip
```

- 沙箱 `arsenal-test` 重建，绑定 `C:\ai-arsenal\workspaces\vecRecall`
  （重建前是绑整个 `C:\ai-arsenal`，所以旧数据 `--collect` 时给出了绑定告警）。
- `ARSENAL_HOME=C:\ai-arsenal` 写入用户级环境变量。
- `.gitignore` 增补：`reports/`、`sandbox-workspaces/`、`last-run.txt`、`workspaces/`
  （被测项目自带仓库，不重复跟踪）。

实测：`arsenal.cmd --dry-run` → 预检通过、退出码 0、中文点评正常
（控制台需 UTF-8；`chcp 65001` 已由 bat 设置，`[Console]::OutputEncoding`
未设 UTF-8 的宿主会显示成乱码，是终端侧问题不是输出问题）；
`--collect` → 数据落到 `C:\ai-arsenal\reports\arsenal\20261026-210507\`，
`summary.json` 里 `paths` 五个路径齐全；`python templates\selftest.py` → **17/17 ok**。

### 还没动的（等你点头）

- `Documents\技能插件库\ai-arsenal\` 是与 `C:\ai-arsenal` 几乎一致的**第二份副本**
  （18848 vs 18851 文件，无 `.git`）。删掉它才算真正合一。
- 那 10 个历史 zip 仍在 `Documents\技能插件库\ai-arsenal\skills\`，未搬动。
