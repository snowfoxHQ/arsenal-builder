# AI Arsenal Builder 3.0.3 —— 完整手册

在 Windows 上用 Docker `sbx` 沙箱搭一个**专用 AI 测试平台**：
DeepSeek Harness 在宿主，Claude Code 在沙箱里跑测试，**判定权留在宿主**。

一句话法则：**沙箱只产数据，宿主才下结论。**

> 本手册随包分发：架构原理（第一节）+ 实际操作步骤（第二节）+ 故障排查（第三节）。
> 这台机器自己的现状（目录布局、归档说明）见根目录 `README.md`；
> 每一版改了什么、为什么改，见 `CHANGELOG.md`。

---

## 一、架构原理

### 1.1 它防的是什么

这套平台为三类真实事故而生，每一类都真的发生过：

| 事故 | 症状 | 平台的对策 |
| --- | --- | --- |
| **假成功** | 测试挂了 7 条，流水线还打印「完成」、退出码 0 | 判定权收归宿主：沙箱交结构化数据，宿主按契约算退出码 |
| **门禁是摆设** | 校验脚本里一个 `assert` 都没有，全靠 `print` | 契约校验是纯函数 `arsenal_contract.py`，永不抛异常、无法确认即 FAIL |
| **报告缩水没人发现** | 报告从 174 行悄悄缩到 51 行，标题还在、内容全空，测试照样全绿 | 报告按九节大纲生成，跑完单独体检（章节/表格行/字节） |

### 1.2 信任边界

| 一侧 | 信任级别 | 职责 | 拿到什么 |
| --- | --- | --- | --- |
| **宿主**（DeepSeek Harness + `runner.py`） | 可信 | 预检、建沙箱、拉结果、跑契约校验、定退出码、写 `summary.json` | API Key、config.json、全部产物 |
| **沙箱**（Docker `sbx` + Claude Code Agent） | **不可信** | 装依赖、跑测试套件、写 `result.json` + `report.md` | 项目只读克隆、网络白名单、自己的 `/tmp` |

三条铁律从这里推出：

1. **数据只往外拉，不往里推。** Agent 写沙箱内 `/tmp/arsenal-out`，宿主用 `sbx cp`
   把**整个目录**拉回来。沙箱进程拿不到宿主任何写路径——拉取方向就是权限方向。
2. **散文不参与判定。** `report.md` 是给人看的，门禁**从不解析它**；`review.py`
   的 AI 点评是顾问，写一百条好评也改不了退出码。判定只来自 `result.json` 的数字
   和宿主的算术。
3. **fail-closed。** 校验清单里任何一条命中即 FAIL，无法确认的状态永远不给 PASS。
   一个说不出「失败」的测试平台比没有更糟，因为它制造虚假信心。

### 1.3 一次运行的完整生命周期

`runner.py` 六步，每一步失败都有明确的退出码：

```
  宿主                                    沙箱（不可信）
  ────                                    ────────────
  1 preflight   sbx 在？Key 在？配置对？
                项目是 git 仓库？沙箱绑的目录对不对？
  2 ensure      沙箱不存在 → sbx create --clone ──→ 宿主仓库只读挂载
                （clone 模式：只读挂 + 容器内私有克隆，        │
                 测试副作用碰不到你的工作区）                  ▼
  3 execute     写 0600 临时 env-file ─────────→ Claude Code 跑测试
                （Key 不进进程表）                写 /tmp/arsenal-out/
                                                  result.json + report.md + logs/
  4 fetch       sbx cp 整个输出目录 ←──────────── 拉取，沙箱不写宿主
                全部落到 <out_dir>\<run_id>\sandbox\
  5 validate    契约校验（纯函数，fail-closed）
                + 报告体检（章节/表格行/字节）
  6 exit code   写 summary.json + 两个 latest 指针
                0 PASS / 1 FAIL / 2 SETUP / 3 BLOCKED
                （之后 review.py 顾问点评，不影响判定）
```

**沙箱绑定校验**（第 2 步内）：`sbx` 沙箱一辈子只绑一个宿主目录。复用绑过别的项目
的沙箱，会去测**错的树**——报告看起来正常，测的却是另一份副本，这是最难察觉的故障。
`runner.py` 用 `sbx ls --format json` 读出真实绑定，与 `project_dir` 不一致直接
退出码 2，要求 `--recreate`。`--dry-run` 也能拦住它。

### 1.4 门禁校验清单（fail-closed）

`arsenal_contract.py` 对拉回的 `result.json` 逐条检查，任一命中即判失败：

- `result.json` 缺失，或不是合法 JSON
- 必填字段缺失，或计数是布尔／字符串而不是整数
- `passed + failed + skipped != total`（计数不自洽）
- `total == 0`——空用例集不算通过
- `failed > 0` → FAIL；`blocked` 非空 → BLOCKED
- `commit` 为空，或与 `expect_commit` 不一致（短哈希前缀容错 ≥7 位）
- `finished_at` 不晚于 `started_at`
- Agent 进程退出码非 0

### 1.5 退出码契约

**退出码就是结论，下游不要解析任何散文。**

| 码 | 含义 | 典型原因 |
| --- | --- | --- |
| 0 | PASS | 计数自洽、无失败、无阻塞 |
| 1 | FAIL | 测试失败／计数不符／结果缺失／零用例／commit 不符／Agent 崩了 |
| 2 | SETUP | 缺 sbx、缺 Key、项目路径错、config 损坏、`bind` 未确认风险、沙箱绑错目录、运行中途异常 |
| 3 | BLOCKED | 没有失败，但有一部分**根本没跑**（缺依赖／网络被封） |

`BLOCKED` 单独给一个码，是因为「测试挂了」和「测试没跑完」是两件事。早先两者都报
FAIL：一次 220/230 全绿、只因缺 `cryptography` 跳了 10 条断言的运行，看起来就像代码
是坏的。`passed` 字段仍只在退出码 0 时为 `true`，任何只认 0 的老脚本行为不变。

### 1.6 一个根：项目、平台、报告三方合一

三样东西散在三处，就会出现「报告正常，测的是另一份副本」。所以定一个根：

```
<ARSENAL_HOME>\                     例如 C:\ai-arsenal
├── arsenal.cmd                     唯一入口，双击就跑（设好 ARSENAL_HOME 后转交启动器）
├── install.bat                     幂等安装 + 自检
├── config.json                     机器接线：测哪个项目、报告写哪（升级不覆盖）
├── last-run.txt                    runner 写，指向最近一次运行
├── workspaces\<repo>\              被测项目，必须自带 .git
├── skills\
│   └── arsenal-builder\            ← 本包解压到这里
├── reports\arsenal\<run_id>\       每次运行的产物（含 sandbox\arsenal-out\）
├── dist\                           导出包
└── archive\                        旧版本、旧报告，只读封存
```

**路径契约只有两条**：

| 配置项 | 相对谁解析 | 例子 |
| --- | --- | --- |
| `prompt_file`（**代码**，随包走） | skill 目录 `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`、`out_dir`（**数据**，属于根） | 根 `ARSENAL_HOME` | `workspaces/vecRecall`、`reports/arsenal` |

根按优先级取：`--root <dir>` → `$ARSENAL_HOME` → **按目录结构推断**（本脚本在
`<根>\skills\arsenal-builder\`，根就是上两级）→ 退回包目录。

第三条是**几何约束**而不是字符串常量，所以**挪走整个文件夹不用改任何文件**：
`C:\ai-arsenal` 搬到 `D:\arena`，根自动变成 `D:\arena`。环境变量因此是可选的。
`runner.py --print-home` 打印它实际认出的根，启动器靠这个拿根，不猜 `%~dp0` 数层数。

报告不写在包里、`config.json` 放根上，都是同一个理由：**升级不该毁掉数据和接线**
——下次解压新版本覆盖旧包，报告若在包内就一起没了。

### 1.7 报告：九节大纲 + 独立体检

报告曾从 174 行 8194 字节缩到 51 行 2001 字节，标题还在、内容全空、没有任何地方报警。
所以 `PROMPT.md` 不再一句话交差，而是规定**九节**：

| # | 章节 | 里面必须有什么 |
| --- | --- | --- |
| 1 | 概览 | 这项目是什么、解决什么问题、核心设计原则（**读源码**，不是抄 README） |
| 2 | 技术栈 | 表：语言／声明要求／**实测**版本／打包／测试框架 |
| 3 | 模块结构 | 目录树，每个文件带 `wc -l` 实数行数与职责 |
| 4 | 依赖 | 逐项表：名称／必需或可选／状态／版本。**这张表决定两次运行可比** |
| 5 | 测试结果 | 逐套件：命令、`158/158` 这样的原始数字、退出码、**覆盖了什么**（分条，来自真实用例名） |
| 6 | 计数汇总 | 总／过／败／跳，整数 |
| 7 | 基准 | 有跑就列表并对比 README 声称的；没有就明写没有 |
| 8 | 未覆盖与风险 | 这次没验证什么、对采用者意味着什么 |
| 9 | 结论 | 事实归纳：哪些维度已验证、哪些没有。**禁止写 PASS/FAIL**，判定归宿主 |

两个开关只管报告、动不了判定（门禁从不解析报告）：

```json
"report_language": "简体中文（技术专有名词、命令、路径、代码、测试名保留英文原文）",
"report_depth": "full"
```

`report_depth`：`full` = 九节全写（默认）｜`brief` = 只写 1/5/6/8，给 CI 用。
临时切换不必改配置：`--report-depth brief`。未知值一律退回 `full`。

**报告单独体检**：跑完 `runner.py` 数章节、表格行、bullet、字节，不够就打印：

```
  report: 5 section(s), outline asks for 6
  report: 1863 B of prose, too little to explain anything (4000 B expected)
```

默认只警告——测试绿不绿和报告写得好不好是两件事，让散文质量翻退出码会让判定
不可预测。想让薄报告也算失败，加 `--strict-report`。结果记在 `summary.json` 的
`report_ok` / `report_problems` 字段里。

### 1.8 安全红线

- **绝不** 用 `sbx setup`：实验性命令，没有网络策略步骤，还会把宿主凭据静默导入
  全局 secret store（实测发生过）
- **绝不** 把网络策略设成 allow-all / Open，要放行就加单个主机
- **绝不** 把 Key 写成命令行上的 `KEY=VALUE`，它会进宿主进程表。
  `runner.py` 用 `--env-file` + 0600 临时文件，用完即删
- **绝不** 让沙箱下结论：提示词禁止，宿主校验，`review.py` 只是顾问
- **绝不** 解析 `report.md` 做任何决定，那是给人看的散文
- `bind` 模式把宿主目录**读写**暴露给 Agent，必须显式设
  `"i_accept_bind_mount_risk": true`——设之前先想清楚

## 二、实际操作步骤

### 第 0 步：宿主前置

1. 安装 Docker Desktop，以及 MSI 安装的独立版 `sbx` CLI。`sbx version` 必须能跑。
2. 把 Key 写进**用户级**环境变量（不是会话变量，沙箱要继承它）：

   ```bat
   setx ANTHROPIC_AUTH_TOKEN "sk-你的DeepSeekKey"
   ```

3. **不要**运行 `sbx setup`（见红线）。

### 第 1 步：网络策略（只放行必要主机）

```bat
sbx policy init balanced
sbx policy allow network api.deepseek.com
sbx policy check network api.deepseek.com
```

最后一条必须打印 `Allowed`，否则停下来修规则，**绝不退回 allow-all**。

测试套件要装依赖时，只缺包源不会导致失败，但那部分用例会以 BLOCKED（退出码 3）
报回来。需要就把包源单独放行：

```bat
sbx policy allow network pypi.org
sbx policy allow network files.pythonhosted.org
```

注意：`sbx policy` 只在宿主可用，沙箱里没有这个命令——Agent 遇到
「Approval required」没法自己批，它只会如实记录，不会反复重试。

### 第 2 步：安装

把本包解压到 `<ARSENAL_HOME>\skills\arsenal-builder\`，然后在**根目录**：

```bat
arsenal.cmd install
```

它做三件事：建 `workspaces\`、`reports\` 目录，补 `config.json`（缺才补，已有的
不覆盖你的接线），跑 21 场景自检。幂等，反复运行没有副作用。

然后检查 `config.json` 的关键字段：

```json
{
  "sandbox": "arsenal-test",
  "project_dir": "workspaces/vecRecall",
  "workspace_mode": "clone",
  "out_dir": "reports/arsenal",
  "prompt_file": "PROMPT.md",
  "report_language": "简体中文（…）",
  "report_depth": "full"
}
```

`project_dir` 要填**被测项目本身**，且必须是仓库根（含 `.git` 的那层）：
`clone` 模式（默认）靠 `sbx create --clone`，没有 `.git` 跑不了，预检会直接拦。
项目不是 git 仓库才改用 `bind`（并接受读写挂载风险）。

### 第 3 步：先预检，再跑

```bat
arsenal.cmd --dry-run     :: 只预检：sbx／Key／config／git 仓库／沙箱绑定，不花钱
arsenal.cmd               :: 完整流水线。首次运行自动 sbx create --clone 建沙箱
```

跑完终端最后三行就是结论：`RESULT: PASS / FAIL / BLOCKED`（附退出码含义）。
双击 `arsenal.cmd` 等价；跑完窗口会停住（`ARSENAL_NO_PAUSE=1` 可关掉）。

### 第 4 步：读结果

一次运行在 `<out_dir>\<run_id>\`（默认 `reports\arsenal\<run_id>\`）留下：

```
summary.json                     机器答案，先读这个
agent.log                        沙箱 Agent 的完整输出（窗口被关也留得住）
sandbox/arsenal-out/             沙箱输出目录整个搬回来
  result.json  report.md  logs/…
```

`summary.json` 字段速查：

```json
{ "state": "pass", "passed": true, "exit_code": 0,
  "paths": { "home": "…", "skill": "…", "project": "…",
             "reports": "…", "run": "…" },
  "reasons": [], "counts": {"total": 230, "passed": 230, "failed": 0, "skipped": 0},
  "report_ok": true, "report_depth": "full", "report_problems": [],
  "artifacts": ["sandbox/arsenal-out/result.json", "…"] }
```

- `paths`：这次运行碰到的五个绝对路径——「测的是哪份副本、报告落在哪」不用猜
- `reasons`：非 PASS 时这里是**具体原因**（英文短句），比读报告快
- `counts`：四计数字段全为整数，自洽性由门禁保证
- `report_ok` / `report_problems`：报告体检结果，默认不改退出码

`report.md` 按九节大纲写（见 1.7），供人阅读；最近一次运行的路径记在根上
`last-run.txt` 和 `<out_dir>\latest.txt` 两处。

### 日常操作

```bat
arsenal.cmd --collect              沙箱里已有数据但没拉回来（窗口被关/进程被杀）时用，
                                   跳过 Agent 直接取回+重校验+重落盘，省一次 token
arsenal.cmd --recreate             重建沙箱。换了 project_dir 必须重建（一个沙箱只绑一个目录）
arsenal.cmd --strict-report        薄报告算失败而不是警告
arsenal.cmd --report-depth brief   本次只写 1/5/6/8 节（CI 场景）
runner.py --print-home             打印实际解析出的根
```

所有参数由 `arsenal.cmd` 原样透传给 `runner.py`。

### 自检

```bat
python skills\arsenal-builder\templates\selftest.py
```

21 个场景不碰真沙箱：11 个判定用例（通过／测试失败／依赖阻塞／结果缺失／计数不符／
零用例／Agent 崩溃／JSON 损坏／非 UTF-8／commit 不符／短哈希容错）+ 5 个契约检查
（运行必须留全三样产物、数据代码路径分离、根按结构推断、提示词渲染完整、
运行目录不许同名复用）+ 2 个报告体检 + 1 个沙箱绑定 + 2 个预检拒绝。
全部 `ok` 才可信；任何 `MISMATCH` 说明门禁坏了，别信它的 PASS。

### 卸载

```bat
sbx rm <sandbox名> --force
```

`--force` 必须带，否则 `sbx rm` 停在确认提示上等输入。然后删工作目录即可——
没有向系统装任何东西。

## 三、故障排查

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 退出码 2，提示 sandbox bound to another directory | 复用了绑过别的项目的沙箱 | `arsenal.cmd --recreate` |
| 测试明明跑完了，运行目录却空的 | 拉回前窗口被关／进程被杀 | `arsenal.cmd --collect`，别重跑 |
| 220/230 全绿但退出码 3 | BLOCKED：部分用例缺依赖没跑 | 看 `summary.json` 的 `reasons`；需要就放行 pypi（第 1 步） |
| 退出码 0 但报告很薄 | 报告体检在警告 | 读 `report_problems`；要硬约束加 `--strict-report` |
| 退出码 2，提示 needs a git repository | `clone` 模式但项目没 `.git` | 项目建库，或改 `bind` 并接受读写风险 |
| 双击 bat 一闪就没 | 窗口跑完即关 | 正常现象时看 `last-run.txt` 指向的运行目录；要窗口停住就删掉 `ARSENAL_NO_PAUSE` |
| `.bat` 中文变乱命令 | cmd 按 OEM 码页读批处理 | 本包所有批处理已全 ASCII+CRLF，中文文档只在 `.md` 里 |
| 报告里依赖一栏一堆 403 | 沙箱网络策略没放行包源 | 见第 1 步，只加包源主机，不开 allow-all |

## 四、包内文件清单

| 文件 | 作用 |
| --- | --- |
| `start_arsenal.bat` | 启动器：解析根 → 调 `runner.py` → 按退出码收尾 |
| `PROMPT.md` | 交给沙箱 Agent 的提示词，含九节大纲与语言／深度占位符 |
| `SKILL.md` / `SKILL.zh-CN.md` | 技能说明（给 AI 读的那份，四个阶段 + 红线） |
| `README.en.md` | 本手册英文镜像 |
| `config.example.json` | 配置样例，复制到根再改 |
| `result.schema.json` | `result.json` 契约参考 |
| `CHANGELOG.md` | 每一版改了什么、为什么、实测数字 |
| `templates/runner.py` | 六步流水线：预检→建沙箱→执行→拉取→校验→退出码 |
| `templates/arsenal_contract.py` | 契约校验本体（纯函数，永不抛异常） |
| `templates/review.py` | DeepSeek 顾问点评，**永不影响判定** |
| `templates/selftest.py` | 21 场景门禁自检 |

零第三方依赖，只用 Python 标准库（3.9+）。
