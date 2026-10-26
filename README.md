# AI Arsenal Builder 3.0.1

在 Windows 上用 Docker `sbx` 沙箱搭一个**专用 AI 测试平台**：
DeepSeek Harness 在宿主，Claude Code 在沙箱里跑测试，**判定权留在宿主**。

**沙箱只产数据，宿主才下结论。** 沙箱永远不写判定，散文永远不参与判定。

> 这是**平台包**的说明（你解压到的这一层）。
> 一个完整部署长什么样、根怎么定、报告的九节各写什么，见同包的
> `SKILL.md`（英文）／`SKILL.zh-CN.md`（中文），以及根目录的 `README.md`。

---

## 这个包里有什么

| 文件 | 作用 |
| --- | --- |
| `start_arsenal.bat` | 启动器：解析根 → 调 `runner.py` → 按退出码收尾。**唯一需要双击的东西** |
| `PROMPT.md` | 交给沙箱 Agent 的提示词，含九节报告大纲与语言／深度占位符 |
| `SKILL.md` / `SKILL.zh-CN.md` | 技能说明（架构、四个阶段、红线、门禁为什么 fail-closed） |
| `config.example.json` | 配置样例，复制到**根**再改 `project_dir` |
| `result.schema.json` | `result.json` 契约参考（门禁校验的就是它） |
| `CHANGELOG.md` | 每一版改了什么、为什么改、实测数字 |
| `templates/runner.py` | 驱动沙箱、拉回结果、跑门禁、写 `summary.json`、决定退出码 |
| `templates/arsenal_contract.py` | 契约校验本体（纯函数，永不抛异常） |
| `templates/review.py` | DeepSeek 顾问式点评，**永不影响判定** |
| `templates/selftest.py` | 21 个场景的门禁自检，不碰真沙箱 |

零第三方依赖，只用 Python 标准库（3.9+）。

## 怎么用

### 方式一：装进一个根（推荐）

```
<ARSENAL_HOME>\                     例如 C:\ai-arsenal
├── arsenal.cmd                     唯一入口，双击就跑
├── install.bat                     幂等安装 + 自检
├── config.json                     机器接线：测哪个项目、报告写哪
├── last-run.txt                    runner 写，指向最近一次运行
├── workspaces\<repo>\              被测项目，必须自带 .git
├── skills\
│   └── arsenal-builder\            ← 本包解压到这里
├── reports\arsenal\<run_id>\       每次运行的产物
├── dist\                           导出包
└── archive\                        旧版本、旧报告，只读封存
```

把本包解压到 `<ARSENAL_HOME>\skills\arsenal-builder\`，然后在根上跑
`arsenal.cmd install`：建目录、补 `config.json`、跑自检，反复运行没有副作用。

### 方式二：独立试跑

随手解压到任意文件夹，直接跑 `start_arsenal.bat`。找不到根时它会退回包自己所在目录
（`out/` 也写在包内），适合只验证流水线能不能起来。

## 路径契约

只有两条，记这两条就不会再问「这份是哪个」：

| 配置项 | 相对谁解析 | 例子 |
| --- | --- | --- |
| `prompt_file`（**代码**，随包走） | skill 目录 `skills\arsenal-builder\` | `PROMPT.md` |
| `project_dir`、`out_dir`（**数据**，属于根） | 根 `ARSENAL_HOME` | `workspaces/vecRecall`、`reports/arsenal` |

根按优先级取：`--root <dir>` → `$ARSENAL_HOME` → **按结构推断**（本脚本在
`<根>\skills\arsenal-builder\`，根就是上两级）→ 退回包目录。

因为第三条是几何约束而不是字符串常量，**挪走整个文件夹不用改任何文件**。

## 命令

```bat
arsenal.cmd                 跑一次完整流水线
arsenal.cmd --dry-run       只预检：sbx 在不在、Key 有没有、项目是不是 git 仓库
arsenal.cmd --collect       不跑测试，只把沙箱里已有的数据收回来（丢数据时用，省 token）
arsenal.cmd --recreate      重建沙箱（换了 project_dir 必须重建，一个沙箱只绑一个目录）
arsenal.cmd install         安装 / 自检
```

`runner.py` 另有 `--report-depth brief`（报告只写 1/5/6/8 节）、`--strict-report`
（薄报告算失败）、`--print-home`（打印解析出的根）。启动器透传所有参数。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | PASS —— 计数自洽、无失败、无阻塞 |
| 1 | FAIL —— 测试失败／计数不符／结果缺失／零用例／commit 不符／Agent 崩了 |
| 2 | 配置错误 —— 缺 sbx、缺 Key、项目路径错、沙箱绑错目录 |
| 3 | BLOCKED —— 没有失败，但有一部分没跑（缺依赖／网络被封）。不是通过，也不是失败 |

**退出码就是结论，不要读散文。** 机器答案在 `<OUT_DIR>\<run_id>\summary.json`。

## 报告

`PROMPT.md` 规定九节：概览／技术栈（实测版本）／模块结构（`wc -l`）／依赖逐项表／
逐套件结果 + 覆盖了什么／计数／基准对比／未覆盖与风险／结论（禁写 PASS/FAIL）。

语言与深度由 `report_language`、`report_depth` 控制，**都动不了判定**（门禁从不解析报告）。
跑完还会单独体检报告：数章节、表格行、字节，不够就打印；默认只警告，
`--strict-report` 时才计入失败。

## 自检

```
python templates\selftest.py
```

21 个场景必须全部 `ok`。任何一条 `MISMATCH`，说明门禁坏了——别信它给出的 PASS。

## 红线

- **绝不** 用 `sbx setup`：实验性，且会把宿主凭据静默导入全局 secret store
- **绝不** 把网络策略设成 allow-all，要放行就加单个主机
- **绝不** 把 Key 写成命令行上的 `KEY=VALUE`，它会进进程表（用 `--env-file` + 0600 临时文件）
- **绝不** 让沙箱下结论，**绝不** 解析 `report.md` 做决定

## 卸载

```
sbx rm <sandbox> --force
```

然后删掉工作目录。没有向系统装任何东西。
