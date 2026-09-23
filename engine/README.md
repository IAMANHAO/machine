# engine —— 机械选型确定性引擎

机械设计物料选型通用引擎。根据用户要选型的物料（齿轮、轴承、螺栓、同步带、联轴器等），自动检索国家标准的选型流程，引导用户补充工况参数，分步计算与校核，最后输出两表（计算过程 + 选型结果），并附上可直接点击的**采购搜索链接**（淘宝 / 天猫 / 1688）。

## 目录结构

```
engine/
├── SKILL.md                                  # 主入口
├── workflows/
│   ├── _template.md                          # 新物料工作流模板
│   ├── synchronous_belt.md                   # 同步带（梯形齿）完整工作流
│   └── lubricant.md                          # 润滑油（链条/开式齿轮/导轨）工作流
├── knowledge/
│   ├── index.yaml                            # 已缓存物料的索引
│   ├── sources.yaml                          # 信源优先级配置
│   ├── procure/
│   │   └── keyword_templates.yaml            # 采购关键词模板 + 电商渠道 URL 模板
│   └── cache/
│       ├── synchronous_belt/                 # 同步带缓存数据
│       │   ├── condition_factors.yaml        # 工况系数 K_A
│       │   ├── belt_pitch.yaml               # 节距、最大带速
│       │   ├── pulley_teeth_min.yaml         # 最小齿数
│       │   ├── belt_length_series.yaml       # 标准节线长
│       │   ├── belt_width_series.yaml        # 标准带宽
│       │   └── rated_power.yaml              # 基准额定功率 P0
│       └── lubricant/                        # 润滑油缓存数据
│           ├── chain_viscosity.yaml
│           ├── open_gear_viscosity.yaml
│           ├── rail_viscosity.yaml
│           ├── iso_vg.yaml
│           └── additive_functions.yaml
├── scripts/
│   ├── _yaml.py                              # YAML 读写封装（PyYAML 优先，缺失时用 yaml_mini）
│   ├── yaml_mini.py                          # 纯标准库 mini-YAML 实现（fallback）
│   ├── calc_engine.py                        # 通用计算、单位换算、插值、校核
│   ├── standard_round.py                     # 把任意数值圆整到 YAML 中的标准系列
│   ├── cache_manager.py                      # 缓存读写 + 索引维护 + 待核验报告
│   └── procure_link.py                       # 生成采购搜索链接（淘宝/天猫/1688/京东）
└── references/
    ├── handbook_search.md                    # 手册检索规范
    ├── source_priority.md                    # 信源优先级与校验规则
    └── procurement_link.md                   # 采购链接与关键词构成规范
```

## 快速开始

### 触发方式

对 WorkBuddy 说：

> "帮我选一根同步带，电机功率 5.5 kW，转速 1450 r/min..."

引擎会按 SKILL.md 的阶段流程：识别物料 → 加载 workflows/synchronous_belt.md → 读取缓存 → 引导补参 → 计算 → 校核 → 出表。

### 直接运行脚本

本 skill 自带 Python 脚本，便于在外部自动化或者离线使用。

> **依赖**：标准 Python 3.10+ 即可。如果系统 Python 没有 PyYAML，本 skill 自带 `yaml_mini.py` 兜底。

#### 环境准备（推荐）

```bash
cd engine
python -m venv .venv
.venv/Scripts/pip install pyyaml     # Windows
.venv/bin/pip install pyyaml        # Linux/macOS
```

#### 单位换算

```bash
.venv/Scripts/python scripts/calc_engine.py unit 5.5 kW W
# → {"from": "kW", "to": "W", "input": 5.5, "output": 5500.0}
```

#### 校核

```bash
.venv/Scripts/python scripts/calc_engine.py check \
    --name "带速" --value 38 --limit 40 --unit "m/s"
# → {"name": "带速", "value": 38.0, "limit": 40.0, "op": "<=", "unit": "m/s", "pass": true}
```

#### 圆整到标准系列

```bash
.venv/Scripts/python scripts/standard_round.py 612 belt_length_series --belt-type L
.venv/Scripts/python scripts/standard_round.py 60 belt_width_series --belt-type H --mode nearest
```

#### 缓存管理

```bash
.venv/Scripts/python scripts/cache_manager.py list
.venv/Scripts/python scripts/cache_manager.py report synchronous_belt
.venv/Scripts/python scripts/cache_manager.py read synchronous_belt belt_pitch.yaml
.venv/Scripts/python scripts/cache_manager.py verify synchronous_belt belt_pitch.yaml --source "GB/T 11362-2021 表3"
```

#### 采购链接

选型完成后生成可直接点击的电商搜索链接：

```bash
# 按物料模板拼关键词并生成链接（默认淘宝 + 天猫）
.venv/Scripts/python scripts/procure_link.py build -m lubricant \
    --set brand=美孚 --set product=威达Vactra --set vg=68 \
    --set kind=导轨油 --set pack=18L

# 输出可直接粘贴进结果表的 Markdown
.venv/Scripts/python scripts/procure_link.py build -m synchronous_belt \
    --set brand=盖茨 --set model=8M --set spec="带宽20 节线长1200" \
    --set kind=同步带 -c taobao,tmall,1688 --format md

# 已有完整关键词，直接出链接
.venv/Scripts/python scripts/procure_link.py link -k "美孚 威达 VG68 导轨油 18L" -c taobao

# 查看支持的物料模板 / 渠道
.venv/Scripts/python scripts/procure_link.py materials
.venv/Scripts/python scripts/procure_link.py channels
```

| 渠道 | URL 模板 |
|---|---|
| 淘宝 | `https://s.taobao.com/search?q={kw}` |
| 天猫 | `https://s.taobao.com/search?q={kw}&tab=mall` |
| 1688 | `https://s.1688.com/selloffer/offer_search.htm?keywords={kw}` |
| 京东 | `https://search.jd.com/Search?keyword={kw}&enc=utf-8` |

> 淘宝/天猫搜索结果由 JS 动态渲染且需风控校验，程序化抓取拿不到商品列表，
> 因此本脚本**只生成搜索链接，不抓商品数据**。链接在真实浏览器中打开即正常出结果。
> 详见 `references/procurement_link.md`。

## 添加新物料

1. 按 `workflows/_template.md` 写一份新工作流，保存为 `workflows/<material>.md`
2. 在 `knowledge/cache/<material>/` 下放数据 yaml
3. 在 `knowledge/procure/keyword_templates.yaml` 的 `materials:` 下补一段采购关键词模板
   （`keyword_order` + `field_hint` + `examples`；不补则自动走 `generic` 兜底，不会报错）
4. 更新 `knowledge/index.yaml`
5. 测试：用脚本验证 cache 读写、圆整、采购链接生成

```bash
.venv/Scripts/python scripts/cache_manager.py write <material> <table>.yaml \
    --source "<来源>" --todo "需第二信源" --status single_source
.venv/Scripts/python scripts/procure_link.py build -m <material> --set kind=<物料名> --format md
```

## 数据可信度

每份数据表顶部有：

- `data_source` — 引用来源（标准号 / 手册章节 / 厂商样本）
- `last_verified` — 最近核验日期
- `verification_status` — `verified`（双源确认）或 `single_source`（需复核）
- `_todo` — 待核实事项

可用 `cache_manager.py report <material>` 一键看哪些表待核验。

## 与 SKILL 主循环协作

`SKILL.md` 中的工作流推荐这样调用脚本：

- 阶段 3（分步计算）：

  ```bash
  python scripts/calc_engine.py check --name "带速" --value <v> --limit <vmax> --unit "m/s"
  python scripts/standard_round.py <L0> belt_length_series --belt-type <T>
  ```

- 阶段 4（校核）：同 check
- 阶段 5（结果整理）：不必调用脚本，直接由主循环输出
- 阶段 6（采购链接）：必出

  ```bash
  python scripts/procure_link.py build -m <material> --set kind=<物料名> --set ... --format md
  ```

更多协议细则见 SKILL.md。
