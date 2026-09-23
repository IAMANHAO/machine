# 机械设计物料选型引擎（MDS）

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-352%20passing-brightgreen.svg)](#测试)

**过程可逐行验证的机械物料选型工具。离线可用，免费，开源。**

给你的不是一个答案，是一条算式：每一步的公式、代入值、依据出处、置信度，全部摊开。
12 个物料（同步带、螺栓、平键、轴、弹簧、轴承、联轴器、减速器、链传动、齿轮、
密封件、润滑油），可导出 PDF / Excel 报告，可生成采购链接。

> ### ⚠ 先读这一句
>
> **这是辅助工具，不是设计依据。** 随包数据表全部为单一信源（🟡），
> 没有一张完成双源核验。正式设计、投产、报审**必须对照标准原件或纸质手册复核**。
> 软件按 Apache-2.0 授权，不提供任何形式的保证。
>
> 数据的来源、边界与责任划分见 **[DATA_NOTICE.md](DATA_NOTICE.md)**。

---

## 它和别的选型工具有什么不同

**一、AI 不参与任何数值计算。**
公式、查表、圆整、校核全部由确定性引擎 `mds` 完成。AI 只做三件不确定的事：
解析自然语言工况、为缺失参数给建议、把某一步讲成白话——而且它给的任何参数值
都要走**与手工输入完全相同的校验通道**才能进入计算。

**二、算不出来就说算不出来。**
工况超出数据表覆盖范围时，引擎抛出精确缺口（"L 型 n1=1450 缺 z1=22 与 z1=26"），
**绝不外推出一个看起来合理的数字**。这和"所需带宽超出标准系列上限"是两回事——
后者是选型结论（该换带型），前者是知识缺口（该补数据），两者给你的指引完全相反。

**三、置信度是事实，不是装饰。**
🟢/🟡/🔴 读自数据表真实的 `verification_status`。标 🟢 必须填第二信源且不得与
主信源相同，数据被修改后核验状态自动退回。**假绿灯比没有绿灯更危险**——
它会让人以为这个数已经被验证过了。

**四、新增物料 = 写一份 YAML，不改代码。**
12 个物料的选型流程全部是声明式规格，由同一个解释器执行。
完整说明见 [`docs/workflow-spec.md`](docs/workflow-spec.md)。

---

## 快速开始

**依赖**：Python 3.10+。（改前端另需 Node 18+；打安装包另需 Rust。）

```bash
git clone <this-repo>
cd mds

python -m venv .venv
.venv/Scripts/python.exe -m pip install -e . ".[dev]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # macOS / Linux

.venv/Scripts/python.exe -m pytest -q                    # 352 项，应全绿
```

跑起来：

```powershell
./run.ps1            # 单进程，用已构建的前端 → 自动开浏览器
```

前端要改的话（热更新，两个终端）：

```powershell
.venv/Scripts/python.exe -m server --port 8756   # 终端 1
cd web && npm install && npm run dev             # 终端 2 → http://localhost:5173
```

## 不用界面也能跑

引擎是独立的，纯命令行即可走完全流程，不需要前端、不需要 AI、不需要联网：

```bash
cd engine
python -m mds run synchronous_belt \
    -s P=5.5 -s n1=1450 -s i=2 -s a0=400 -s belt_type=H \
    -s prime_mover=ac_motor_normal -s work_machine=medium_uniform -s hours_per_day=h_le_10
```

其它命令：

```bash
python -m mds materials          # 12 个物料的状态
python -m mds inputs bolt        # 某个物料要哪些参数
python -m mds validate           # 规格与数据状态自检
python -m mds audit              # 三层数据自检（含真跑引擎的可达性探测）
```

## 目录

| 路径 | 说明 |
|---|---|
| `engine/` | 确定性内核 + 工作流 + 数据。**同时是一个 Claude skill** |
| ├ `mds/` | 规格解析、受限表达式求值、查表、圆整、解释器 |
| ├ `workflows/*.yaml` | **可执行工作流规格**（新增物料只写 YAML） |
| ├ `workflows/*.md` | 人读文档，与 YAML 逐条对齐，不被代码消费 |
| └ `knowledge/cache/` | 25 张数据表，每张带 `data_source` / `verification_status` |
| `server/` | FastAPI 本地服务（**只监听 127.0.0.1**） |
| `web/` | Vite + React + TypeScript 前端 |
| `desktop/` | Tauri 2 桌面壳（可选） |
| `docs/` | 完整开发文档，10 篇 |

**完整文档入口：[`docs/README.md`](docs/README.md)** —— 架构、工作流规格参考、
知识库与数据、HTTP API、前端、AI 层、打包、开发指南、决策记录。

## 12 个物料，三类工作流形态

| 形态 | 物料 | 特征 |
|---|---|---|
| **线性** | 同步带、平键、轴、链传动、齿轮、弹簧 | 一串步骤跑到底 |
| **分支** | 润滑油、螺栓、减速器、密封件 | 按对象/工况走不同步骤集，用 `when:` 守卫 |
| **样本校核** | 轴承、联轴器 | 额定值由用户从厂商样本填，引擎做算术与校核 |

分支用 `when:` 步骤守卫表达，不需要嵌套结构：每一步声明自己在什么条件下适用，
不适用标 `not_applicable`——与「前面出错所以没跑到」的 `skipped` 严格区分，
后者是故障，前者不是。

### 当前能力与缺口（如实列出）

| 物料 | 表 | 可达 | 主要缺口 |
|---|---|---|---|
| 同步带 | 6 | 6/15 | 额定功率 P0 表只有示例点 |
| 润滑油 | 5 | 10/11 | 低速 + 油浴组合 |
| 螺栓 | 4 | 6/7 | 合金钢不控制预紧力的安全系数 |
| 平键 | 2 | 6/6 | 许用切应力（剪切校核未做） |
| 轴 | 1 | 15/15 | 只到扭转初估，不做疲劳校核 |
| 弹簧 | 1 | 3/5 | I 类 / II 类许用应力比 |
| 轴承 | 1 | 5/5 | X/Y 系数表（改由样本填） |
| 联轴器 | 1 | 5/5 | 工作机工况系数（改由样本填） |
| 减速器 | 1 | 7/8 | 行星与摆线针轮、圆锥齿轮 9 级 |
| 链传动 | 1 | 6/6 | **额定功率曲线——选不了链号** |
| 密封件 | 1 | 6/6 | 沟槽尺寸与合格判据 |
| 齿轮 | 1 | 6/6 | **ZH / YFa / σHlim 是线图——不做完整强度校核** |

**合计 81 / 95 个代表性工况能算到底，25 张表 0 张完成双源核验。**

这张表放在 README 里而不是藏在文档深处，是有意的：
**一个选型工具最该说清楚的，就是它什么时候靠不住。**

## 数据自检

```bash
cd engine
python -m mds audit synchronous_belt
```

三层检查：信源 frontmatter 体检 → 插值表网格覆盖分析 → **按探测网格真跑引擎**，
报出哪些工况组合现在根本算不出来。界面上对应「知识库」页的三个 tab。

**缺口不会被自动填补。** 技术上完全可以用相邻点插值把空洞填上——但那就是在伪造数据。

标记双源核验（第二信源必填，且不得与主信源相同）：

```bash
python -m mds verify synchronous_belt belt_pitch \
    --second-source "成大先《机械设计手册》第3卷 第14篇（纸质原件核对）"
```

## AI 与账号（BYOK）

**本软件不自带任何 API key，不自建中转服务，不代付任何费用。**
AI 由你在设置页绑定自己的账号提供，请求由本机直连服务商，工况参数不经过第三方。

可绑定 **DeepSeek / 火山方舟（豆包）/ 阿里百炼（通义千问）/ OpenAI（ChatGPT）**，
以及任何自填 base_url 的 OpenAI 兼容服务。**可以同时绑定多家、一键切换**——
一家余额用完时能立刻切到另一家，切换不动任何凭据。

方舟与百炼没有免费的模型列表接口，绑定验证会发一次最小对话请求、
**消耗几个 token**；这一点在点按钮之前就写在对话框里，绑完再如实报出具体消耗。

未绑定时软件完整可用：物料识别（关键词匹配）、计算、校核、出表、采购链接、
知识库自检与补录全部照常。绑定只增强三件事，且都不参与数值计算：

| 接口 | 做什么 | 未绑定 / 离线时 |
|---|---|---|
| `parse_intent` | 整句工况 → 物料 + 参数 | 关键词匹配 |
| `suggest_params` | 缺失参数 → 建议值 + 理由 | 只给规格里写明的典型值 |
| `explain` | 已算完的一步 → 白话 | 关闭（没有诚实的降级方案） |

**建议值绕不过校验闸门**：每个 AI 建议在返回前都会跑一遍 `mds.runner._coerce`——
与用户手输完全同一条通道。过不了的以 `rejected` 返回，调用方拿不到一个绕过校验的数。

凭据存进系统凭据库（Windows 凭据管理器 / Keychain / Secret Service），不落明文文件、
不入数据库、不随项目导出；界面只显示脱敏形式。

用量页只显示**真实 token 数与真实余额**，刻意不做费用换算——单价会变，
硬编码价目表迟早会给出一个过期的「预估费用」，那与本项目的立身之本相悖。

## 导出报告

阶段 5 可导出 PDF 与 Excel。两种格式**投影自同一份报告模型**，所以每一格必然一致。

导出接口收的是输入参数，**服务端自己重跑一遍引擎再渲染**——不接收前端传来的 trace。
这保证了「本报告可由它自己列出的那组输入完整复现」这句话是真的。

报告含五张表：输入工况、计算过程汇总（**带代入式**）、校核、最终选型结果、
**信源清单**（主信源 / 第二信源 / 核验状态 / 核验日期 / 内容指纹）。
未核验的数据会在报告正文里如实写明。

PDF 中文用 reportlab 内置的 STSong-Light CID 字体，不随包分发字体文件，离线可用。

## 打包

| 产物 | 需要 | 说明 |
|---|---|---|
| `dist/mds-server/` | 仅 Python | 独立版 46 MB，双击即用，自动开浏览器 |
| `desktop/.../bundle/nsis/*.exe` | + Rust | 安装包 21 MB，Tauri 原生窗口 |

```powershell
./packaging/build.ps1 -SkipTauri   # 只出独立版，不需要 Rust
./packaging/build.ps1              # 含安装包，需要 Rust
```

打包版有两处与源码态不同，都是有意的：**知识库只读**（随包数据装在 Program Files
里本来也写不进去，编辑接口明确返回 409 而不是静默失败）；
**用户数据在 `%LOCALAPPDATA%\MDS\`**，不在 exe 旁边。

打包产物必须**单独**冒烟测试——源码态全绿不代表打包版能跑：

```powershell
.venv/Scripts/python.exe packaging/smoke_test.py
```

## 测试

```powershell
.venv/Scripts/python.exe -m pytest -q      # 352 项
.venv/Scripts/python.exe packaging/smoke_test.py   # 21 项，需先打包
```

这套测试的重点不是覆盖率，是守住那些**破了就毁掉产品价值**的性质。
测试名本身就说清了守的是什么：

- `test_refuses_to_extrapolate_beyond_row_range`
- `test_width_beyond_series_is_no_solution_not_missing_data`
- `test_skipped_checks_are_not_reported_as_failures`
- `test_out_of_range_suggestion_is_rejected_by_the_engine`
- `test_gear_does_not_pretend_to_do_a_full_strength_calculation`

黄金用例的每一行都是独立笔算过的，不是把引擎输出抄回来的。

## 参与

欢迎的贡献，按价值从高到低：

1. **补数据并完成双源核验**——这是当前最大的短板，见 [DATA_NOTICE.md](DATA_NOTICE.md)
2. **新增物料**——写一份 YAML，见 [`docs/workflow-spec.md`](docs/workflow-spec.md)
3. 修 bug、补测试、改文档

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

**有一条底线请先读**：这个项目里**任何一个来路不明的数字都是 bug**，
包括界面上的"预估"、报告里的"典型值"、和测试里为了让它过而硬填的期望值。

## 许可

代码按 [Apache-2.0](LICENSE) 授权——免费使用、修改、分发、商用。

数据表另见 [DATA_NOTICE.md](DATA_NOTICE.md)。
