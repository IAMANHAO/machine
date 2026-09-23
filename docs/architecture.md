# 架构

## 为什么这么切

这个项目的价值主张是"过程可逐行验证"。架构的每一刀都是围绕它切的。

最初的 skill 里，选型逻辑写在 `workflows/synchronous_belt.md` 的散文里，由 AI 读着执行算术。
`scripts/` 只有通用原语（单位换算、插值、圆整）。这意味着**当时是 AI 在做确定的事**，
离线模式根本算不出东西，"逐行可验证"也无从谈起。

软件化的第一刀就是把这件事反过来：**工作流变成可执行的确定性资产，AI 退到边上**。

## 分层

```
┌─────────────────────────────────────────────────────────┐
│  web/            React 前端                              │
│                  只渲染 trace，不做任何计算                 │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP (127.0.0.1)
┌────────────────────────┴────────────────────────────────┐
│  server/         FastAPI                                 │
│  ├── routers/    HTTP 契约                               │
│  ├── engine.py   适配层：形状转换 + 缓存，零计算逻辑         │
│  ├── ai/         BYOK 账号 + 三个窄接口（不碰数值）          │
│  ├── export/     PDF / Excel（与界面同源）                 │
│  └── store.py    SQLite 项目库                            │
└────────────────────────┬────────────────────────────────┘
                         │ import
┌────────────────────────┴────────────────────────────────┐
│  mds/            确定性引擎 —— skill 与软件共用             │
│  ├── spec.py     工作流 YAML 加载与静态校验                 │
│  ├── runner.py   解释器 → SelectionTrace                  │
│  ├── expr.py     受限表达式求值（AST 白名单，禁 eval）       │
│  ├── tables.py   查表；★ 凸包外拒绝外推                     │
│  ├── rounding.py 标准系列圆整；★ 超系列上限抛 NoSolution     │
│  ├── knowledge.py 缓存读取 + 信源元数据 + 内容指纹           │
│  ├── editor.py   缓存写入（ruamel 保注释）+ 双源守卫         │
│  ├── audit.py    三层数据自检（含真跑引擎的可达性探测）        │
│  └── procure.py  采购链接（复用 skill 的 procure_link.py）  │
└────────────────────────┬────────────────────────────────┘
                         │ 读
┌────────────────────────┴────────────────────────────────┐
│  workflows/*.yaml   可执行工作流规格                       │
│  knowledge/cache/   数据表 + 信源元数据                    │
└─────────────────────────────────────────────────────────┘
```

### 引擎为什么住在 skill 目录里

`mds/` 在 `engine/` 下，不在仓库根。
因为 `workflows/` 和 `knowledge/` 也在那里，而引擎要读它们。

**一份公式只有一处实现**：Claude skill 和桌面软件调用同一个 `mds`。
如果把引擎搬到仓库根、再复制一份数据过去，两边迟早会漂移，
"计算引擎的结果永远可被独立复现"就成了空话。

## 数据流：一次选型

```
用户填参数
    │
    ├─→ validate_inputs()      域校验、二选一分组、required_when 条件必填
    │
    ├─→ 逐步执行 spec.steps
    │     │
    │     ├─ when 守卫不满足 → status=not_applicable，跳过（不是错误）
    │     ├─ 执行 handler     → StepTrace{公式, 代入, 结果, 信源, 置信度}
    │     ├─ DataMissing      → status=data_missing，halt，后续标 skipped
    │     ├─ NoSolution       → status=no_solution，halt（这是结论，不是故障）
    │     ├─ NeedsChoice      → status=needs_choice，halt，带候选列表
    │     └─ check 不通过     → status=check_failed，**继续跑**
    │                            （一次看到全部问题，而不是修一个报一个）
    │
    └─→ SelectionTrace
          ├─ steps[]      阶段 3 计算卡片
          ├─ checks[]     阶段 4 校核（排除 skipped 与 not_applicable）
          ├─ result[]     阶段 5 表 2（不适用的行直接不出现）
          ├─ sources[]    信源清单（含内容指纹）
          ├─ confidence   所有引用表里最低的那一档
          └─ warnings[]   未核验数据的风险提示
```

### 唯一的展示数据源

界面的阶段 3 计算卡片、阶段 4 校核项、阶段 5 表 1、PDF/Excel 报告，
**全部由同一份 `SelectionTrace` 渲染**。不存在第二份"展示用数据"。

这条约束对导出件尤其重要：报告是要拿去评审、归档、当采购依据的，
它比界面更不能有第二套说法。

## 状态语义

7 个状态，两两之间的区别都是刻意的：

| 状态 | 含义 | 用户该做什么 |
|---|---|---|
| `ok` | 算出来了 | — |
| `check_failed` | 算出来了，但校核不过 | 改设计参数 |
| `no_solution` | 所需值超出标准系列 | **改设计**（换更大规格） |
| `data_missing` | 数据表覆盖不到这个工况 | **补知识库** |
| `needs_choice` | 需要人/AI 决策，引擎不猜 | 从候选里选 |
| `skipped` | 前面出问题，没跑到 | 解决前面的问题 |
| `not_applicable` | 本条分支用不到这一步 | 什么都不用做 |

三对最容易被混同、也最不能混同的：

- **`no_solution` vs `data_missing`**：指引完全相反。前者数据没问题，是"L 型带就是带不动
  5.5 kW"；后者是知识库缺东西。混为一谈会把人引到错误的方向。
- **`skipped` vs `not_applicable`**：前者是故障，后者不是。
  把"本分支用不到"显示成"未执行"，会让人以为流程出了错。
- **`skipped` vs `check_failed`**：把未执行的校核显示成"不通过"是危险的误导，
  反过来更危险——`SelectionTrace.checks` 明确排除了这两种状态，不让它们进入 "N/M 项通过" 的计数。

## 三条硬规则的落点

### 1. 拒绝外推

`mds/tables.py:_bracket()` 在插值前检查 x 是否落在数据点凸包内，
超出就抛 `DataMissing` 并报出精确缺口。

这条规则的来由：原 `scripts/calc_engine.py` 的 `interpolate()` 在凸包外做**静默最近邻外推**。
`rated_power.yaml` 每种带型只有 2~4 个示例点，拿它算任意工况会编出一个看起来合理的 P0。
对一个卖"可验证"的产品，这是最不能出的错。

### 2. 表达式不用 eval

`mds/expr.py` 用 `ast.parse` + 节点白名单求值。
工作流 YAML 可能来自不可信的物料包，`eval` 等于把执行权交出去。

白名单：12 个函数（`min max abs round int float floor ceil sqrt log exp ent`）、
2 个常量（`pi e`）。`ent` 是机械设计手册的取整记号，等价于 `floor`。

字符串只能参与 `==` / `!=`（分支守卫需要），
参与算术会被当场拒绝——`"a" * 10000000` 是内存炸弹。

### 3. AI 的输出过同一条闸门

`server/ai/tasks.py:suggest_params()` 里，每个 AI 建议值在返回前都跑一遍
`mds.runner._coerce()`——与用户手输**完全同一个函数**。
过不了的以 `rejected` 返回，调用方拿不到那个数。

这是结构性保证，不是注释里的君子协定。测试 `test_out_of_range_suggestion_is_rejected_by_the_engine`
让假模型吐一个 `P=99999 kW`，断言它出现在 `rejected` 而非 `suggestions`。

## 运行形态

同一份代码在三种形态下工作，差异由 `server/config.py` 收口：

| | 源码态 | 独立版 | Tauri 安装版 |
|---|---|---|---|
| 引擎数据 | skill 目录 | 随包 `_internal/engine` | 同左 |
| 用户数据 | `.mds-data/` | `%LOCALAPPDATA%\MDS\` | 同左 |
| 知识库 | 可写 | **只读** | **只读** |
| 前端 | `web/dist` 或 Vite | 随包 `_internal/web` | 同左 |

打包态知识库只读是有意的：装在 Program Files 里本来就写不进去，
与其让用户点了编辑再失败，不如提前禁用并说明原因。

详见 [打包与部署](packaging.md)。

## 两个数据根

| 根 | 内容 | 可写 | 优先级 |
|---|---|---|---|
| `SKILL_ROOT`（随包） | 12 个物料的工作流 + 25 张数据表 | 打包态只读 | **高** |
| 用户目录（`MDS_DATA_DIR`） | AI 起草并保存的物料、项目库、账号元数据 | 是 | 低 |

查找顺序是**内置优先、用户目录兜底**，顺序不能反：
已随包核验过的工作流不该被用户目录里的同名文件顶掉——
那会让一份 AI 起草的 🔴 规格悄悄取代一份有信源的 🟡 规格。
落盘时也会拒绝与随包物料重名。

`SKILL_ROOT` 在 import 时确定（打包态由启动器提前设好），
用户目录**每次调用都读环境变量**——它可能在进程跑起来之后才确定。
