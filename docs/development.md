# 开发指南

## 环境

```powershell
# 依赖（仓库根的 .venv，不是 skill 目录下那个旧的）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e . ".[dev]"

cd web && npm install
```

跑起来：

```powershell
./run.ps1                                          # 单进程，用已构建的前端
# 或者开发模式，两个终端：
.venv/Scripts/python.exe -m server --port 8756
cd web; npm run dev                                 # → :5173，自动代理 /api
```

## 测试

```powershell
.venv/Scripts/python.exe -m pytest -q               # 480 项
.venv/Scripts/python.exe packaging/smoke_test.py    # 30 项，需先打包
```

引擎侧的测试住在 `engine/tests/`，
服务侧的住在仓库根的 `tests/`，`pyproject.toml` 里两处都在 `testpaths` 中。

| 文件 | 守什么 |
|---|---|
| `test_engine.py` | 手算对照、拒绝外推、无解与缺数据的区分、表达式安全、确定性 |
| `test_lubricant.py` | 分支隔离、条件必填、决策表、结果表不漏占位符 |
| `test_bolt.py` | `row_select`、**表值与定义式一致**、假定直径档的显式迭代 |
| `test_key_shaft.py` | 区间左开右闭、最弱材料取值、**省掉 A₀ 不影响结果** |
| `test_spring.py` | 数据拿不到时的分工（用户填 / 留空 / 另给通路） |
| `test_catalog_based.py` | 轴承/联轴器/减速器：算术正确、校核项一个不少、**表里没有厂商数据** |
| `test_chain_seal_gear.py` | 几何算对、**边界守得住**（不假装做完整强度校核） |
| `test_docs_alignment.py` | `.md` 与 YAML 逐项对齐、**每张缓存表都读得出来**、没有假绿灯 |
| `test_legacy_cli.py` | skill 原有 4 条命令行为不变 |
| `test_api.py` | HTTP 契约、引擎语义穿过 HTTP 不降级 |
| `test_knowledge.py` | 双源守卫、写入保注释、引擎立即可见 |
| `test_ai.py` | 未绑定可用、建议绕不过闸门、凭据不泄漏、超限拒绝 |
| `test_providers.py` | 四家服务商的接入差异、**验证代价如实告知**、多账号切换、v1 迁移 |
| `test_research.py` | **取证层**：SSRF 护栏、正文里没有声称的标准号就不予采纳、trusted ≠ 绿灯 |
| `test_search.py` | 三家搜索服务的响应形状、降级不吞掉失败原因、key 不落文件 |
| `test_websearch.py` | 服务商自带联网：**只取链接不取答案**、不支持就老实降级 |
| `test_guided.py` | 引导式全流程：依据取证、编出来的系数过不去、**不合规先退回给模型改**、顺序闸门在后端、保存后离线可用 |
| `conftest.py` | 共享的 FakeProvider 与数据目录/凭据库隔离夹具 |
| `test_export.py` | 信源清单在、代入式在、流程没走完不装作走完了 |

### 写测试的取向

这套测试的重点不是覆盖率，是守住那些**破了就毁掉产品价值**的性质。
测试名本身就该说清守的是什么，比如：

- `test_refuses_to_extrapolate_beyond_row_range`
- `test_width_beyond_series_is_no_solution_not_missing_data`
- `test_skipped_checks_are_not_reported_as_failures`
- `test_out_of_range_suggestion_is_rejected_by_the_engine`
- `test_export_of_a_blocked_run_says_so`

黄金用例（`test_engine.py` 的 `HAND_CALC`）的每一行都是独立笔算过的，
不是把引擎输出抄回来的。改公式时要重新手算，别直接改期望值。

## 命令行

引擎可以完全脱离前端使用：

```bash
cd engine

python -m mds materials                 # 物料与工作流状态
python -m mds inputs <物料>              # 参数清单
python -m mds validate [物料]            # 规格与数据状态
python -m mds audit [物料]               # 三层自检（--no-probe 跳过探测）
python -m mds run <物料> -s k=v ...      # 跑一次选型（--format json）
python -m mds verify <物料> <表> -2 "第二信源"
```

调试工作流时 `run --format json` 最有用：完整 trace 一目了然。

## 已知环境陷阱

这几个都实际踩过，会反复咬人：

### 1. 透明加密软件

机器上装了文件透明加密（文件头 `%TSD-Header-###%`）。
skill 目录下原来那个 `.venv/pyvenv.cfg` 就是被它写成二进制乱码而废掉的。

**症状**：某个文本配置文件读出来是乱码。先想到这个，不是编码问题。

### 2. npm registry 指向已废弃的源

全局 npm registry 是 `registry.npm.taobao.org`——2022 年下线、证书过期，
`npm install` 会**无限挂起且不报任何错**。

已在 `web/.npmrc` 改用继任者 `registry.npmmirror.com`（只改项目级）。
在别的目录建前端项目还会撞上。

### 3. Bash heredoc 与转义

写含大量中文 + 引号的代码文件时，Bash heredoc 会解析失败
（`unexpected EOF while looking for matching '`）。这类文件用 Write 工具。

补丁脚本里的转义要格外小心，实际踩过两次：

- `".cargo\bin"` 的 `\b` 被 Python 当成退格符 → 写成了 `.cargoin`
- `"\\n"` 经 shell → Python → 文件多层传递后变成真换行 → 语法错误

**规避**：Windows 路径用正斜杠；需要换行就写两条语句，别用 `\n`。

### 4. PyYAML 的两段式文档

缓存文件是 frontmatter + 正文两个 YAML 文档，
`safe_load` 会抛 `ComposerError`，必须 `safe_load_all` 再合并。

### 5. 打包态的 `__file__`

PyInstaller 打包后 `__file__` 指向归档内部，推不出随包数据的路径。
引擎的数据根靠 `MDS_SKILL_ROOT` 环境变量确定。

**新增任何按 `__file__` 定位资源的代码，都要想一遍打包态会怎样。**

## 改动时的自检清单

动核心逻辑前问自己：

- [ ] 破坏了三条硬规则吗？（拒绝外推 / AI 不碰计算 / 置信度是事实）
- [ ] 新增的失败模式，用户该做什么？是补数据还是改设计？选对了状态吗？
- [ ] 界面上"未执行"和"不通过"还分得开吗？
- [ ] 打包态会怎样？有按 `__file__` 定位资源吗？
- [ ] 导出报告里会不会漏进未渲染的模板变量？
- [ ] 加了新数字吗？它的出处能追溯吗？
- [ ] 改了 `workflows/*.yaml` 吗？对应的 `.md` 回填了吗？（步骤 id、参数域、校核表、算例）
- [ ] 往缓存表里填了新数字吗？**它有出处吗？能不能用定义式复算一遍？**
- [ ] 表里的 `_todo` 用了中文双引号以外的 ASCII 引号吗？（嵌在双引号字符串里会让 YAML 解析失败，已踩三次）

最后一条最重要。**这个项目里任何一个来路不明的数字都是 bug**，
包括界面上的"预估"、报告里的"典型值"、和测试里为了让它过而硬填的期望值。

## 代码风格

- 注释写**为什么**，不写是什么。尤其是那些"看起来可以简化但不能简化"的地方
- 错误信息面向用户，说清发生了什么 + 该做什么，不要只抛类型名
- 中文用于所有面向用户的文本；代码标识符用英文
- 数值展示统一走 `mds.expr.fmt_num`，别在各处 `round`
