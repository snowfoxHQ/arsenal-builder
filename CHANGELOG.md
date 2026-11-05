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
  官方 `sbx rm --help` 原文："Removal requires confirmation; use `--force` to skip
  confirmation prompts (for non-interactive scripts)"。
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

## v2.9.0 — 2026-10-26 21:40

**全部合并到 C:/ai-arsenal/，代码里不留一处绝对路径**

上一版把「一个根」写进了文档，但磁盘上还是五处：主目录、`C:/tmp/arsenal-builder-v2`
工作副本、`Documents\技能插件库\ai-arsenal` 整份副本、根目录 `engine\` 这个 v1 引擎、
`sandbox-workspaces\` 里那份重复的被测项目。版本号还靠文件名区分（v1…v2.8 十个 zip）。
结果是：报告看着正常，但没人说得清它测的是哪棵树——事实上确实测错过一次。

**合拢动作**

| 原位置 | 处置 |
| --- | --- |
| `C:/tmp/arsenal-builder-v2/` | → `archive\workcopies\arsenal-builder-v2-tmp\`（工作副本退役，`out\` 旧数据早前已回收到 `reports\`） |
| `Documents\技能插件库\ai-arsenal\` 的 10 个历史 zip | → `archive\dist\v1-to-v2.7\` |
| 同上，旧测试报告 | → `archive\reports\`（与 sandbox-workspaces 那份同源，加了来源后缀） |
| 根目录 `engine\`（v1，写死 `C:\ai-arsenal\sandbox-workspaces\`） | → `archive\legacy\engine-v1\` |
| 根目录 `install.bat`（指向早已不存在的 `templates-zh`） | → `archive\legacy\install-v1.bat`，重写为可用版本 |
| `sandbox-workspaces\vecRecall\`（与 `workspaces\vecRecall` 逐字节一致，仅缺 `.git`） | → `archive\duplicates\` |
| `archive\skills\arsenal-builder-v1\templates-*\.env.sbx` | 删除（旧凭据不该随归档四处留） |

**代码里的地址问题，逐个修**

1. **根改成「按结构推断」**：新增 `home_from_layout()`——本包在
   `<根>\skills\arsenal-builder\`，根就是上两级。优先级变成
   `--root` > `$ARSENAL_HOME` > **结构推断** > 包目录。
   意义：整个文件夹从 `C:\` 搬到 `D:\`，**一个文件都不用改**；环境变量成了可选项，
   不再是「忘了设就静默写错地方」的陷阱。
2. **`--print-home`**：`runner.py` 打印它实际认出的根。`start_arsenal.bat` 改成向
   runner 要这个值，不再靠 `%~dp0` 往上数层数——数错一层就会把报告写进包里。
3. **`--config` 相对路径先找根**：从任意目录执行 `--config config.json`，
   会依次找 cwd、根；以前会静默退回默认值（默认 `out_dir=out`，报告就落进包里）。
4. **相对路径统一 `normpath`**：`last-run.txt` 里曾出现 `reports/arsenal\` 混用
   两种分隔符，`set /p` 读回后 `cd` 会失败。
5. **根目录入口重写**：`arsenal.cmd` 加 `install` / `selfcheck` 子命令；
   新 `install.bat` 幂等（建目录、补 config、跑自检），全程只用 `%~dp0`，
   没有一行 `C:\ai-arsenal`。
6. **`selftest.py` 不再污染包目录**：把 `ARSENAL_HOME` 钉在临时目录，
   自检不再往安装好的包里写 `last-run.txt`（上一版包里就带着一个指向已被删除的
   临时目录的指针文件）。

7. **沙箱绑定校验提前到预检**：原来只在真正执行时才验，`--dry-run` 报「预检通过」，
   但实际会去测错的树。现在预检就查，`--dry-run` 能提前拦住；读不到绑定时
   打一行警告（不静默放过）。
8. **`--dry-run` 不再触发顾问点评**：预检通过后 `start_arsenal.bat` 会接着跑
   `review.py`，点评读的是 `last-run.txt` 指向的**上一次**运行，还会真调一次模型
   API——一次干跑的预检变成几十秒的网络等待。现在参数里有 `--dry-run` 就直接收工。

**自检 17 → 19 场景**

- 新增「home from layout」——`<根>\skills\<skill>\` 必须解出 `<根>`；不在 `skills\`
  下的目录不许冒充根；`templates\` 不许被当成 skill 目录。
- 新增「run dirs unique」——顺带揪出一个真 bug：`run_id` 只到秒，**同一秒内跑两次
  会共用同一个运行目录**，后一次直接继承前一次的 `result.json`，于是输出一份
  「每个数字都是真的，但不是这次跑的」判定。这类错误比崩溃更难发现。改为
  `make_run_dir()` 分配，冲突就加 `-2`、`-3` 后缀，并且用 `exist_ok=False` 建目录，
  让碰撞不可能被静默吞掉。

**新增根目录文档**：`README.md`（一个根、路径契约、命令、退出码、归档说明）、
`VERSION.txt` 升到 2.9.0。旧 `VERSION.txt` 存 `archive\legacy\VERSION-v1.txt`。

实测：`python templates\selftest.py` → **19/19 ok**（连跑三次稳定）；
`arsenal.cmd install` → 目录/配置/自检全过，退出码 0；
`arsenal.cmd --dry-run` → 预检通过，报告的根是 `C:\ai-arsenal`；
`runner.py --print-home` 在包内、在根、在 `C:\` 下执行都返回同一个根。

## v3.0.0 — 2026-10-26 22:47

**报告从「51 行计数」恢复到「174 行以上」，并且以后退化了会被抓出来**

现象：同一套测试，v1 时代的报告 174 行 / 8194 字节（项目概览、技术栈、模块结构带行数、
依赖逐项状态、每套件覆盖了什么、benchmark、结论），v2 之后的报告 51 行 / 2001 字节
（Stack 四条 bullet、命令、计数表、失败项）。**结论只剩「绿了没有」**，看完不知道
这项目是干什么的、测了哪些行为、哪些没测。

根因是一句话。`PROMPT.md` 里对报告的全部要求就是：

```
{{REPORT_PATH}} — human-readable summary: stack, commands run, what failed
```

Agent 严格照做，所以只给了这三样。v2 重写时全部精力都在「门禁不能假成功」
（取回、落盘、退出码、路径、编码、绑定校验），**报告要能读这件事从头到尾没被审视过**。

修法四处：

1. **`PROMPT.md` 换成九节大纲**：概览（读源码不是抄 README）／技术栈（含**实测**版本）／
   模块结构（`wc -l` 实数行数）／依赖逐项表（**这张表决定两次运行可比**）／
   逐套件结果 + **覆盖了什么**（必须来自真实用例名，不许从 README 推导）／计数／
   基准（对比 README 声称值）／未覆盖与风险／结论（事实归纳，禁止写 PASS/FAIL）。
2. **新增 `report_depth`**：`full`（默认，九节全写）／`brief`（只写 1/5/6/8，给 CI）。
   可用 `--report-depth brief` 临时覆盖。未知值一律退回 `full`——报告多写一页浪费一分钟，
   少写一页浪费一次运行。
3. **报告体检 `audit_report()`**：跑完数章节数、表格行、bullet 数、字节数，不够就打印。
   默认只警告（测试绿不绿与报告好不好是两件事，让散文质量翻退出码会让判定不可预测）；
   `--strict-report` 时计入失败。阈值取自两份真实报告：好的那份 8194 B / 27 表格行 /
   13 标题，薄的那份 2001 B / 6 / 5，阈值卡在中间偏薄一侧。
4. **`PROMPT.md` 补两条约束**：依赖装进项目或系统解释器，**不许装进 `{{OUT_DIR}}`**
   （输出目录只该有报告和日志）；被网络策略拦住就记下原因，不要反复重试。

**顺带修一个真 bug（v2.9 就有，这次才暴露）**

`describe_artifacts()` 用 `os.path.getsize` 遍历运行目录，撞上从沙箱拷回的
`venv/lib64` 符号链接时在 Windows 抛 `PermissionError`。它跑在 `VERDICT: PASS`
打印**之后**、写 `summary.json` **之前**，于是：判定已经说 PASS，进程却崩了，
退出码变 1，`summary.json` 一个字节都没写——正是「跑完什么都没留下」这个
本版本要解决的问题，被自己的收尾代码又制造了一次。
修：`os.walk(onerror=...)` 忽略错误、跳过目录符号链接、`getsize` 单独兜住
`OSError`、输出最多 40 行并补一行 `... N more not listed`。

自检 19 → **21 场景**：新增「thin report flagged」（薄报告必须被标出但退出码仍 0）、
「full report accepted」（合格报告必须放行）；「prompt fully rendered」增加
`Report depth` 与 `brief` 渲染检查。

**实测**（真沙箱，被测 `workspaces\vecRecall`，6 分 13 秒）：

- 报告 **16574 字节**（旧版好的那份 8194，薄的那份 2001），九节齐全；
  `test_core.py` 28 条用例逐条列出断言了什么，`test_blockchain.py` 13 条；
  模块结构 18 个源文件 5585 行（`wc -l` 实数）；benchmark 表里 `bow` R@5 76.2% /
  MRR 0.635 与 README 一致，`sentence` 分支明写「未跑，依赖缺失」；
  风险节 8 条，具体到「`prune(use_forgetting=True)` 组合路径未直接断言」
- `report_ok: true`、`report_problems: []`
- 判定 `BLOCKED`（exit 3）：220/230，10 项静态加密因 `cryptography` 装不上而自跳过，
  pypi.org 被沙箱防火墙 403 拦下
- 薄报告回归验证：`--collect` 对旧那次 → 三项问题全部打印、退出码仍 0；
  加 `--strict-report` → 退出码 1，`summary.json` 记录 `report_ok: false`
- `python templates\selftest.py` → **21/21 ok**

## v3.0.1 — 2026-10-26 23:10

**只改文档，代码与门禁零变化。** 起因：解压 `v3.0` 的包，第一份文档还在讲 v2。

三处不一致：

1. 包里的 `README.md` 开头是 `# AI Arsenal Builder v2`（英文），`README_zh.md` 是
   `# AI 兵工厂构建器 v2`（中文），而 `VERSION.txt` 写 3.0.0。谁打开 zip 都会以为
   装了个旧版——这比没有文档更糟，因为它错得很有说服力。
2. `SKILL.md` / `SKILL.zh-CN.md` 里运行目录还写着 `out/<run_id>/`、`out/latest.txt`。
   实际根上 `out_dir` 是 `reports/arsenal`，并且**两个指针都会写**：
   `<out_dir>/latest.txt` 和根上的 `last-run.txt`。
3. `summary.json` 的示例缺 `paths` / `report_ok` / `report_depth` /
   `report_problems` —— 而这四个字段正是「这次到底动了哪三处」和「报告有没有缩水」
   的答案，恰恰是 3.0 的两条主线。

改法：

- 包内 `README.md` 重写为 **v3 中文精简版**（包视角：包里有什么／怎么装进根／
  路径契约／命令／退出码／报告／自检／红线／卸载），英文改为 `README.en.md`；
  旧的 `README_zh.md` 删除——两个 README 内容重复是漂移的温床。
- `SKILL.md` / `SKILL.zh-CN.md`：运行目录改 `reports\arsenal\<run_id>\`，
  说明两个 `latest` 指针为什么要各放一处；`summary.json` 示例补齐五个绝对路径与
  报告体检字段；补「`arsenal.cmd` 与 `install.bat` 在根上、不在包里」；
  `--collect` 同时给出根启动器写法；卸载命令加 `--force`（不带会停在确认提示上）。
- `LICENSE` 换行符统一（包内原为 LF、根为 CRLF，`diff` 报全文 21 行全不同）。
  现在两份 SHA-256 一致。

**没有改的**：`templates/*.py`、`PROMPT.md`、`config.example.json`、
`result.schema.json` 一字未动；`selftest.py` 仍 21/21 ok。

## v3.0.2 — 2026-10-26 23:57

**包内 README 升级为完整手册：架构原理 + 实际操作步骤 + 故障排查。**

用户反馈明确：README 里要有对这个 AI 测试「兵工厂」的**完整**介绍，不能只是
「包里有什么」。v3.0.1 把包 README 做短只讲包，方向错了——zip 发出去就该
自带全部原理与操作，不依赖读者再去翻根目录。

新增内容（全部与代码逐条核对过，不是抄散文）：

- **1.1 它防的是什么**：三类真实事故（假成功／门禁是 print／报告缩水）与对策
- **1.2 信任边界**：宿主 vs 沙箱的信任级别与职责；三条推论——数据只拉不推、
  散文不参与判定、fail-closed
- **1.3 一次运行的完整生命周期**：`runner.py` 六步（preflight → ensure → execute →
  fetch → validate → exit code）的宿主/沙箱两侧时序图；沙箱绑定校验放在第 2 步里讲
- **1.4 门禁校验清单**：`arsenal_contract.py` 的八条 fail-closed 检查
- **1.5 退出码契约**：0/1/2/3 各自的典型原因，BLOCKED 为什么独立
- **1.6 一个根**：根的布局、两条路径契约、四级根解析、为什么报告不进包
- **1.7 报告**：九节大纲逐节要求、`report_language`/`report_depth` 两个开关、
  独立体检与阈值示例
- **1.8 安全红线**：六条，含 `sbx setup` 实测翻车记录
- **第二节 实操**：第 0 步前置（setx 用户级 Key）→ 第 1 步网络策略（含 pypi 放行）→
  第 2 步安装（`arsenal.cmd install` + config 关键字段逐个解释）→ 第 3 步先
  `--dry-run` 再全跑 → 第 4 步读 `summary.json`（字段速查）；日常四命令 + 自检 + 卸载
- **第三节 故障排查表**：八种症状 → 原因 → 处理（绑错目录、数据没回来、
  BLOCKED、薄报告、缺 .git、bat 闪退、批处理乱码、依赖 403）

同步改动：`README.en.md` 英文镜像；根 README 的「文档在哪」改为新的分工铁律——
**机器的事在根 README，平台的事在包 README**。

**没有改的**：`templates/*.py`、`PROMPT.md`、`config.example.json`、
`result.schema.json` 一字未动；selftest 仍 21/21 ok。

## v3.0.3 — 2026-10-27 00:13

**README 手册与 SKILL 两份（中英）逐条对齐。** 交叉审查发现两处事实错误、五处口径不同步。

事实错误：

1. SKILL.md / SKILL.zh-CN.md 的版本号停在 **3.0.1**，README 手册已是 3.0.2。
2. 「**220/220** 全绿、只因缺 `cryptography` 跳了 10 条断言」——数学上自相矛盾
   （220 过 + 10 跳 = 总数 230）。按 v3.0.0 实测记录改为 **220/230**。

口径不同步（两边各说一半，合并后与代码行为一致）：

3. 退出码 2 的原因：SKILL 写「unsafe config, run aborted mid-way」，README 写
   「bind 未确认、绑错目录」。对照 `runner.py` 证实**四种都触发**（preflight 失败、
   config JSON 损坏、运行中途 error、绑定校验），两份文档都补全。
4. SKILL 把 README.md 描述为 "package overview"——3.0.2 起它是完整手册。
5. 红线 SKILL 5 条、README 6 条：补上「bind 读写挂载必须显式确认」。
6. fail-closed 清单补「短哈希 ≥7 位前缀容错」（`arsenal_contract.py` 的
   `same_commit` 实际行为，selftest 也有对应场景）。
7. 第四阶段开头补 `arsenal.cmd` 为人用入口；根布局图补 `install.bat` 与 `dist`；
   架构节「copies the file back」改为「copies the whole output directory」。

**没有改的**：`templates/*.py`、`PROMPT.md`、json 配置、CHANGELOG 历史条目；
selftest 仍 21/21 ok。

## v3.1.0 — 2026-11-05

**陈旧产物防线（run_id nonce）。** 2026-11-05 实测踩坑：agent 因 API key 失效
196 秒即崩、什么都没写，runner 仍从沙箱 `/tmp/arsenal-out` "回收"了 10 月 26 日
的 result.json + report.md，且 commit 相同、`expect_commit` 为空——若 agent 恰好
退出 0，旧产物会被当成新结果判 PASS。

双保险：

1. **执行前清场**：runner 在驱动 agent 前先 `sbx exec <sbx> rm -rf <sandbox_out_dir>`
   （best-effort，失败只告警）。
2. **run_id nonce（强保证）**：run_id 渲染进 prompt（`{{RUN_ID}}`），要求 agent
   原样回显进 result.json；`arsenal_contract.validate()` 新增第三参数
   `expect_run_id`，缺失或不符 → FAIL。`--collect` 模式显式豁免（数据恢复本就
   不属于本次运行），summary.json 记 `run_id_verified: false` 让下游知道未验。

配套：PROMPT.md 结果契约加 `run_id` 字段；result.schema.json 补说明；
summary.json 新增 `run_id` / `run_id_verified`。

selftest：假 sbx 的 exec 分支改为模拟合规 agent（从渲染后 prompt 里取 run_id
回写 result.json），支持 `nonce_mode=wrong/omit` 两种偏差；新增
「stale result rejected」「run_id omitted rejected」两场景，共 **23** 个。
install.bat 去掉会漂移的「19 scenarios」硬编码。

实测：selftest 23/23 ok；真机全流水线（vecRecall，agent 正确回显 run_id）→
BLOCKED(exit 3)，`run_id_verified: true`。

## v3.1.1 — 2026-11-05

**沙箱无关第一步：sbx 从「依赖」降级为「适配器」。**

- 新增 `templates/backends/` 包：五能力契约（probe / exists+binding /
  create+remove / in_sandbox_path+clear_dir+exec_agent / fetch_dir+fetch_file）
  + `SbxBackend` 参考实现（原 runner 里的 sbx 专有代码整体迁入，行为不变）。
- `runner.py` 不再出现任何 sbx 专有调用；新增 `backend` 配置项（默认 `"sbx"`，
  旧配置不写此键行为不变），未知后端名 → 退出码 2。
- agent 命令策略（`claude -p ... --dangerously-skip-permissions`）留在 runner
  （`run_agent_command`），执行机制归后端——判定策略与执行机制分家。
- selftest 新增**后端契约套件**：对每个后端跑同一组检查（probe 拒绝缺失二进制、
  exists/binding 对结构化列表、in_sandbox_path 路径转换、fetch_file 对缺失文件
  返回 False）。新后端加入 `backends/__init__.py` 时必须同时进契约套件，
  「沙箱无关」不靠文档靠测试。

实测：selftest 23 场景 + 后端契约全绿；真机 dry-run exit 0、全流水线
BLOCKED(exit 3)，执行/清场/回收路径全部走新后端。

## v3.2.0 — 2026-11-05

**任何沙箱都能跑：docker / none 后端落地 + 绑定自证 + 无隔离红线。**

新后端：

- **docker**：普通 Docker 容器，bind 挂载项目（读写），binding 记在容器
  label（`arsenal.project`），经 `docker inspect` 结构化读回。clone 模式不
  支持（无远端可克隆），必须 `i_accept_bind_mount_risk: true`；`docker_image`
  必填（probe 阶段就拦截，含 claude CLI 的镜像）。
- **none**：无隔离，agent 直接跑在宿主机项目目录。合法后端（快速迭代/无沙箱
  CI），但**改写信任模型**——preflight 强制 `i_accept_no_isolation: true`，
  否则退出码 2；prompt 追加 Isolation notice（原文声称"isolated sandbox"
  在此模式下是谎言）。产物目录固定在系统临时目录，`agent_bin` 可换 CLI
  （selftest 用它注入假 agent，不真调 claude）。

绑定自证（binding() 返回 unknown 时不再只是告警）：

- **clone 模式 → commit 比对**：沙箱内 `git rev-parse HEAD` 与宿主 HEAD
  经 `same_commit`（缩写容错）比对，不一致 → 退出码 2；
- **bind 模式 → nonce 文件**：往 project_dir 放一次性 nonce，让实例读回，
  内容不符 → 退出码 2；读不到 → 保持 unknown 告警。
- summary.json 新增 `binding_status` / `binding_verified` / `backend`。

runner 同时新增 `agent_bin`（默认 `claude`）；selftest 夹具改为**真 git
仓库**（自证需要两个可比较的 HEAD），新增假 docker CLI、假 agent。

selftest：28 场景 + sbx/docker/none 三后端契约套件全绿；真机 dry-run
exit 0。

## v3.2.1 — 2026-11-05

**机器可比性：run_manifest.json + `--compare`。**

README 说「依赖表决定两次运行可比」，但那张表是 agent 写的散文，宿主从未
解析过。manifest 是机器答案，每次运行在动任何东西之前写出：

- `arsenal_version` / `platform` / `host_python` — 跑在什么上面
- `backend` + `backend_version()`（sbx/docker 报 CLI 版本，none 报
  "host (no isolation)"）— 用什么隔离
- `project_commit` — 宿主 HEAD（宿主自己取，不信 agent）
- `prompt_sha256` — 发出去的 prompt 指纹
- `config_fingerprint` — 配置指纹（排除 run_id/recreate/home 等易变键）

`runner.py --compare RUN_A RUN_B`：diff 两个 manifest，exit 0 可比 /
1 有差异（逐字段列出）/ 2 manifest 缺失。崩溃的运行也留 manifest——
「跑在什么上面」本身就是证据。summary.json 带上 `manifest` 全文与
`manifest_hash`。

selftest：29 场景（新增 manifest comparable），审计夹具要求
run_manifest.json 且 project_commit 与夹具 HEAD 一致。全绿。

## v3.2.2 — 2026-11-05

**灵活应用：profile 差异覆盖 + 幂等续跑（--resume）。**

- `--profile <name>`：深合并 `<home>/configs/<name>.json` 到 config.json，
  profile 只写差异字段（如 CI 用 `{"report_depth": "brief"}`）。profile 缺失
  或非法 → 退出码 2。红线不变：profile 能改报告形态与后端，**改不了通过
  标准**——判定阶段的失败语义仍写死在代码里。
- `--resume <run_dir>`：复用原 run 目录（run_id 不变，nonce 自洽）。三态：
  ① `stages/agent.json` 记录的 manifest 哈希一致且 result.json 带本 run
  nonce → **跳过 agent**（不烧 token），直接重验产物；② 阶段未完成/崩过 →
  重跑 agent（补写阶段记录）；③ manifest 有任何差异 → 拒绝（退出码 2），
  续跑不同实验等于伪造出处。
- summary.json 新增 `resumed` / `agent_skipped`；install.bat 建 `configs\`。

selftest：新增 lifecycle 套件（baseline 干净跑 → sabotage exec.rc=137 验证
跳过 → 阶段记录改 137 验证重跑 → 改 manifest 验证拒绝 → profile 指向非 git
目录验证覆盖生效），29 场景 + 三套件全绿。
