# 工作流规格参考

**新增一个物料 = 写一份 `workflows/<material>.yaml`，不改代码、不重新打包。**

这份文档是这句话的完整说明书。

对应的 `.md` 文档（如 `workflows/lubricant.md`）是**人读说明与评审物**，不被代码消费，
但**与 YAML 逐条对齐**：文档里每个步骤都标了 YAML 的步骤 id，参数表抄的是真实 `domain`，
算例是引擎真跑出来的。加物料要写两份文件，模板见 `workflows/_template.md`。

两者若有出入，**以 YAML 为准**，并在 `notes` 里写明原因。
现有两个工作流各发现了一处 `.md` 的转写错误（同步带啮合齿数取整位置、导轨速度公式量纲），
`.md` 已按 YAML 更正并保留勘误说明，**仍待标准/手册原件确认**。

## 顶层结构

```yaml
schema_version: 1
material: synchronous_belt          # 必填，与目录名一致
name_zh: 同步带（梯形齿）
standard: GB/T 11362-2021
workflow_doc: workflows/synchronous_belt.md

notes: []        # 已知偏差、未实现项、引用口径说明。会透传到界面与报告
inputs: []       # 参数定义
probe: {}        # 数据自检的探测网格
result: []       # 阶段 5 的结果表
procure: {}      # 阶段 6 的采购关键词
steps: []        # 计算步骤
```

`notes` 不是装饰。它是你向使用者交代"这份规格哪里不完整、哪里与文档不符"的地方，
会显示在阶段 1 的"已知偏差与限制"里。**宁可写长，不要留空。**

## inputs：参数定义

```yaml
inputs:
  - id: P                    # 必填，表达式里用这个名字
    name_zh: 传递功率
    unit: kW
    type: number             # number(默认) | enum | text
    required: true
    domain: {min: 0.01, max: 500}
    hint: 电机名义功率        # 显示在输入框下方
    default: null            # 用户没填时的取值
    typical: null            # 离线参数建议用；没有就别编
```

全部字段：`id name_zh unit type required domain options options_from default typical hint one_of required_when`

### 枚举

两种写法。选项来自数据表时用 `options_from`，这样表变了选项自动跟着变：

```yaml
  - id: prime_mover
    type: enum
    required: true
    options_from: {table: condition_factors, path: prime_movers, label: name_zh}

  - id: hours_per_day
    type: enum
    default: h_le_10
    options:                               # 也可以写死
      - {value: h_le_10, label: 每天不超过 10 小时}
      - {value: h_gt_16, label: 每天超过 16 小时}
```

裸值 `options: [a, b, c]` 也支持，但界面上会直接显示 `a`——
**给人看的东西就该用 `{value, label}`**。

### 二选一：`one_of`

```yaml
  - id: i
    name_zh: 传动比
    one_of: ratio
  - id: n2
    name_zh: 从动轴转速
    one_of: ratio
```

同组至少给一个，否则报缺参。

### 条件必填：`required_when`

分支型工作流用。选了导轨就不该被要求填链条的齿数：

```yaml
  - id: z1
    name_zh: 小链轮齿数
    required_when: "target == 'chain'"
```

服务端会把 `field == 'value'` 这种简单形式解析成结构化的 `depends_on`
传给前端，界面据此**只显示当前分支的字段**。复杂条件解析不了就全显示——
前端不重复实现表达式求值，同一套规则两处实现迟早对不上。

## steps：计算步骤

每步的公共字段：

```yaml
  - id: Pd                   # 必填，唯一
    name_zh: 计算设计功率      # 界面与报告里显示的名字
    kind: formula            # 必填，见下
    outputs: [Pd]            # 产出的变量名；省略则等于 id
    unit: kW
    note: ""                 # 补充说明，显示在卡片下方
    source: {ref: "GB/T 11362-2021 6.2"}
    when: ""                 # 分支守卫，见下
```

### `when`：分支守卫

表达式为真时才执行；为假则标 `not_applicable` 跳过。
这是分支型工作流的唯一机制——不需要嵌套结构，任意条件都能表达：

```yaml
  - id: v_chain
    kind: formula
    when: "target == 'chain'"
    expr: "z1 * p_chain * n_chain / 60000"
```

`not_applicable` 与 `skipped` 在界面和报告里严格区分：
前者是"本分支用不到"，后者是"出错没跑到"。

---

## 十一种步骤类型

### `formula` —— 表达式求值

```yaml
  - id: Pd
    kind: formula
    expr: "K_A * P"
    unit: kW
```

可用函数：`min max abs round int float floor ceil sqrt log exp ent`，
以及三角函数 `sin cos tan asin acos atan radians degrees`（弧度制）；
常量：`pi e`。支持三元表达式：

```yaml
    expr: "1 if zm >= 6 else 1 - 0.2 * (6 - zm)"
```

`ent` 是机械设计手册的取整记号（= `floor`），写公式时直接照抄手册即可。

### `table_lookup` —— 按路径取标量

```yaml
  - id: K_A
    kind: table_lookup
    table: condition_factors
    key: "prime_movers.{prime_mover}.hours.{hours_per_day}.{work_machine}"
```

`{var}` 用当前上下文填充。取到的必须是标量，是 dict 会报错——那种情况用 `table_pick`。

路径走不通时报出**这一层有哪些可用键**，而不是干巴巴一句"找不到"。

### `table_pick` —— 按路径取一组推荐字段

决策表用。润滑油这类表返回的不是一个数，而是一整组建议：

```yaml
  - id: chain_rec
    kind: table_pick
    table: chain_viscosity
    key: "by_speed_and_method.{chain_speed}_{chain_method}"
    fields:                          # 表里的字段名 → 输出变量名
      ISO_VG_recommended: iso_vg
      base_oil_class: base_oil
      additives: additives
      notes: rec_notes
    allow_partial: false             # true 则缺字段不报错
    outputs: [iso_vg, base_oil, additives, rec_notes]
```

**列表字段会自动多产出一个 `<名>_first`**：推荐往往是个区间
（VG 150 / 220 / 320），但采购关键词只能带一个值。

注意 `key` 里可以拼多个变量（`{chain_speed}_{chain_method}`）。
表里没覆盖到的组合会如实报数据缺失并列出可用组合，
而不是硬凑一个最接近的档。

### `table_interp` —— 二维插值（凸包外拒绝）

```yaml
  - id: P0
    kind: table_interp
    table: rated_power
    group: "belt_type_{belt_type}"    # 分组路径
    rows_key: examples                # 分组下的数据行列表
    x: n1                             # 行的键字段
    y: z1                             # 行内 points 的键字段
    z: P0                             # 取值字段
```

对应的数据结构：

```yaml
belt_type_H:
  examples:
    - n1: 1450
      points:
        - {z1: 18, P0: 1.60}
        - {z1: 24, P0: 2.10}
```

**x 或 y 超出覆盖范围一律抛 `DataMissing`，不外推。**
这是整个引擎最重要的一条规则，别绕过它。

### `bucket` —— 连续量落到命名分档

```yaml
  - id: speed_bucket
    kind: bucket
    value: n1                # 变量名或表达式
    bins:
      - {max: 900,  key: n1_le_900}
      - {max: 1800, key: n1_900_to_1800}
      - {key: n1_gt_3600}    # 最后一档省略 max = 兜底
```

产出的是档位名，通常接着喂给 `table_lookup` 的 `{}` 占位。

### `round_to_series` —— 圆整到标准系列

```yaml
  - id: bs
    kind: round_to_series
    table: belt_width_series
    path: "belt_width.{belt_type}.series"
    value: bs_req
    mode: nearest_above      # nearest_above(默认) | nearest_below | nearest
    on_exceed: error         # clamp(默认) | error
    what: 带宽                # 报错信息里怎么称呼它
```

`on_exceed: error` 时超出系列上限抛 **`NoSolution`**（不是 `DataMissing`）：
"所需带宽 180mm 超出 L 型上限 101.6mm" 是一个**选型结论**——该换带型，
不是知识库缺数据。两者给用户的指引完全相反。

### `row_select` —— 在有序行集里按阈值挑一行

```yaml
  - id: bolt_size
    kind: row_select
    table: thread_series
    rows_key: coarse              # 行列表所在路径
    order_by: As                  # 排序字段（取满足条件里最小的那行）
    where:                        # 可写多条，全部满足
      - {field: As, op: ">=", value: As_req}
    what: 螺栓规格                 # 报错信息里怎么称呼它
    fields:                       # 取回该行的哪些字段，映射成什么名字
      label: size_label
      d: d_bolt
      P: pitch
      As: As_actual
    outputs: [size_label, d_bolt, pitch, As_actual]
```

`round_to_series` 只能圆整**扁平数字列表**，拿不回同一行的其它字段。
但选型里反复出现同一个模式：*在有序行集里找第一个满足阈值的行，取它整行的多个字段*
——螺栓按 As 选规格、键按轴径查截面、O 圈按线径选档。

绕开它的办法是把同一份数据存两遍（一份扁平系列 + 一份反查字典），
那正是这个项目最该避免的事：**同一个数字存两处，迟早会对不上。**

**没有"取最接近的一行"这个退路。** 选不中就是选不中：
给一个比所需规格更小的螺栓，比停下来说"系列内无解"危险得多。

选不中时引擎会分辨两种情况：

- 阈值条件卡在了系列上/下限 → **`NoSolution`**（该换设计）
- 其它情况（等值筛选没命中、行里就没这个字段）→ **`DataMissing`**（该补表）

行里缺 `where` 用到的字段时**当作不满足**，而不是跳过——
一张缺格子的表不该让某一行悄悄胜出。

### `check` —— 校核

```yaml
  - id: chk_v
    kind: check
    value: v                 # 变量名、字面量或表达式
    op: "<="                 # <= < >= > == !=
    limit: v_max
    unit: m/s
    on_fail: 减小 z1 或改用小节距带型
```

`value` 和 `limit` 都按三种形式解析：字面量数字 → 表达式（含运算符）→ 变量名。
所以 `limit: "6"` 和 `limit: "0.7 * (d1 + d2)"` 都能写。

**校核不通过不会中断流程**——后续步骤继续算，让用户一次看到全部问题。

### `select` —— 引擎给候选，人/AI 决策

数据不足以唯一确定时用它。引擎筛出候选并说明**为什么自己不能决定**：

```yaml
  - id: belt_type_sel
    kind: select
    from_input: belt_type        # 用户已指定就直接采用
    candidates:
      table: belt_pitch
      path: belt_types
      label_field: name_zh
      detail_fields: [pitch_pb, vmax, typical_use]
    reason: "带型需查 GB/T 11362-2021 带型选择图确定。该图为图形数据，
             缓存中没有，引擎不做猜测。"
    outputs: [belt_type]
```

没给值就抛 `NeedsChoice`，带候选列表和理由。
前端渲染成可点的候选卡片，点了之后把答案放进 `choices` 重跑。

`candidates` 也可以直接给列表：`candidates: [A, B, C]`。

### `default` —— 取用户输入，缺省时回退

```yaml
  - id: a0_eff
    kind: default
    from_input: a0
    fallback: "1.35 * (d1 + d2)"
    note: "未提供时取 0.7(d1+d2) ~ 2(d1+d2) 推荐区间的中部"
```

用了回退值时 `detail.by = "engine_default"`，界面会标"该值未由用户指定"。

### `text` —— 渲染模板文本

给结果表凑标签、拼采购关键词用：

```yaml
  - id: label_chain
    kind: text
    when: "target == 'chain'"
    template: 链条
    outputs: [target_label]

  - id: v_label_chain
    kind: text
    when: "target == 'chain'"
    template: "链速 {v_chain} m/s"
    outputs: [v_label]
```

`text` 步骤**不会出现在"计算过程汇总"里**——它是标签赋值，不是推导。

---

## result：阶段 5 结果表

```yaml
result:
  - {label: 带型, value: "{belt_type}"}
  - {label: 带宽 bs, value: "{bs}", unit: mm}
  - {label: 采购关键字, value: "{belt_type} 同步带 带宽{bs} 节线长{Lp}"}
```

`{var}` 取自最终环境；列表渲染成 `150 / 220 / 320`。

**模板里有任何一个变量没产出，整行不显示。** 分支型工作流会列出所有分支
可能产出的字段，没走到的分支那几行自然消失——把 `{form}` 原样打在结果表里
比少一行糟糕得多。

## procure：阶段 6 采购

```yaml
procure:
  material_template: synchronous_belt    # knowledge/procure/keyword_templates.yaml 里的模板名
  channels: [taobao, tmall]
  fields:
    model: "{belt_type}"
    spec: "带宽{bs} 节线长{Lp}"
    kind: 同步带
```

新物料要在 `keyword_templates.yaml` 的 `materials:` 下补一段同名配置，
否则走 `generic` 兜底（不报错，但关键词质量差）。

## probe：数据自检的探测网格

`mds audit` 会按这张网格**真跑引擎**，报出哪些工况组合现在根本算不出来。
这是回答"这个软件到底能选什么"的唯一可信方式。

两种写法。线性工作流用笛卡尔积：

```yaml
probe:
  base: {P: 5.5, i: 2, a0: 400, prime_mover: ac_motor_normal}
  sweep:
    belt_type: [XL, L, H, XH, XXH]
    n1: [720, 1450, 2880]
```

分支工作流必须逐条列举——各分支要的参数根本不是一套：

```yaml
probe:
  base: {T_work: 55}
  cases:
    - {name: 链条·低速手加油, target: chain, z1: 19, p_chain: 12.7, n_chain: 300, chain_method: hand}
    - {name: 开式齿轮·中速, target: open_gear, d_gear: 3000, n_gear: 15}
```

**建议故意包含几个你预期会失败的用例**，并在 `name` 里写明预期，
比如 `链条·低速油浴（表内无此组合）`。这样审计报告本身就是一份诚实的能力清单。

---

## 加一个新物料：完整流程

1. **备数据**。在 `knowledge/cache/<material>/` 下建 YAML，格式见
   [知识库与数据](knowledge.md)。每张表都要有 `data_source` 和
   `verification_status`。
2. **写规格**。`workflows/<material>.yaml`。先把 `inputs` 和 `steps` 写全，
   `probe` 可以后补。
3. **加载校验**：
   ```bash
   python -m mds validate <material>
   ```
   规格有问题会在这里报 `SpecError`，不会等跑到一半才炸。
4. **跑一遍**：
   ```bash
   python -m mds run <material> -s 参数=值 ...
   ```
5. **补 probe，做自检**：
   ```bash
   python -m mds audit <material>
   ```
   看可达性矩阵，把缺口补上或如实记进 `notes`。
6. **补采购模板**：`knowledge/procure/keyword_templates.yaml`。
7. **写测试**。参考 `tests/test_lubricant.py`——重点测分支隔离、
   数据缺口如实报告、结果表不漏占位符。

界面不需要任何改动：物料卡片、参数表单、计算卡片、结果表全部由规格驱动。

## 静态校验会拦住什么

`spec.load()` 在加载时就检查，不等运行：

- 未知的 `kind`
- 重复的 `id`（输入与步骤分别检查）
- 表达式引用了**此刻还不可用的变量**（前向引用）
- `check` 的比较符不在 `<= < >= > == !=` 里
- 各 kind 的必填字段缺失
- `result` 行缺 `label` 或 `value`

前向引用检查特别有用：步骤顺序写错了会立刻报出来，
而不是在某个偏门工况下才暴露。
