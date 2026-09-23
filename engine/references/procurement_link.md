# 采购链接生成规范

选型算完不算完 —— 用户最终要**买得到**。本文件规定：选型结果输出时，什么情况下必须给采购链接、关键词怎么拼、链接怎么给。

## 1. 强制要求

> **任何一次选型结束，最终结果表后面必须附至少 1 条淘宝搜索链接。**

理由：机械件（尤其是标准件、润滑油、皮带、轴承）的最终型号往往要落到具体品牌与包装规格上，用户需要立刻拿到可点击的采购入口。

- 通用消费品类 → 淘宝 1 条即可
- 工业件（轴承、皮带、油品、减速机、密封件）→ 淘宝 + 天猫（品牌旗舰店渠道），必要时加 1688
- 批量采购 / 非标定制 → 额外给 1688

## 2. 渠道与 URL 模板

| 渠道 | URL 模板 | 说明 |
|---|---|---|
| 淘宝 | `https://s.taobao.com/search?q={kw}` | 商品最全，默认渠道 |
| 天猫 | `https://s.taobao.com/search?q={kw}&tab=mall` | 天猫 tab，品牌旗舰店集中；旧地址 `list.tmall.com` 会 302 跳到这里 |
| 1688 | `https://s.1688.com/selloffer/offer_search.htm?keywords={kw}` | 工厂直供 / 批发，注意起订量 |
| 京东 | `https://search.jd.com/Search?keyword={kw}&enc=utf-8` | 自营工业品，比价锚点 |

`{kw}` = 关键词的 UTF-8 URL 编码（空格转 `+`）。中文必须编码，否则部分浏览器/客户端会截断。

**技术说明**：淘宝搜索结果由 JS 动态渲染且需风控校验，程序化抓取（WebFetch）拿不到商品列表。因此本 skill **只生成搜索链接，不抓商品数据**。链接在真实浏览器中打开即正常出结果。

## 3. 关键词构成规则

关键词质量直接决定搜索结果质量。按以下顺序拼，缺项自动跳过：

```
品牌 → 产品线 → 规格型号 → 物料名 → 等级/精度 → 包装/数量
```

**必须包含的 3 要素**：

1. **物料名**（导轨油 / 8M 同步带 / O 形圈）—— 决定品类，不能少
2. **规格型号**（VG68 / 节线长1200 / 6205-2RS）—— 决定选对，不能少
3. **品牌或材质**（美孚 / 304不锈钢 / NBR）—— 决定质量档次，强烈建议给

**反例与修正**：

| 差的关键词 | 问题 | 修正后 |
|---|---|---|
| `导轨油` | 只有品类，结果全是杂牌分装 | `美孚 威达 VG68 导轨油 18L` |
| `68号油` | 平台无法判定品类 | `68号 导轨油` |
| `同步带 1200` | 缺带型与带宽，无法询价 | `盖茨 8M 同步带 带宽20 节线长1200` |
| `轴承 6205` | 可以，但可优化 | `SKF 6205-2RS 深沟球轴承` |

**单位与写法约定**：

- 黏度写 `VG68`，同时备一条 `68号` 写法的变体（淘宝卖家更常用"号"）
- 尺寸写 `M8x30`、`30x47x7`、`外径20x2.4`，避免纯数字
- 同步带写 `8M` 而不是 `8m`（大小写影响匹配）
- 模数写 `模数2`，不要只写 `2`

**变体策略**：主关键词若字段过多（加了包装、精度等级）可能零结果。脚本自动生成 2 条备选：

1. 精简版（丢掉包装 / 等级等非核心字段）
2. `号`写法版（黏度写法的另一种）

## 4. 输出格式

在选型结果表下方按此格式给出：

```markdown
### 采购链接

| 渠道 | 搜索链接 |
|---|---|
| 淘宝 | [美孚 威达 VG68 导轨油 18L](https://s.taobao.com/search?q=...) |
| 天猫 | [美孚 威达 VG68 导轨油 18L](https://s.taobao.com/search?q=...&tab=mall) |

> 链接为**搜索页**而非具体商品，价格与库存随行情浮动，请自行核对。
```

## 5. 平台使用提示（给用户）

- **认准官方渠道**：油品 / 轴承等易被仿冒，优先品牌旗舰店或授权经销店，"正品"字样本身不能说明问题
- **包装规格要对齐选型**：工业油常见 18L / 20L / 200L 桶装，家用小包装可能不标黏度等级
- **索取 MSDS / 检测报告**：食品级（NSF H1）、阻燃、高低温特种油必须索证
- **注意起订量**：1688 常按箱 / 按托盘起订，小批量会加价
- **标准件看精度等级与公差**：轴承 P5/P6、螺栓 8.8/10.9 级差价很大，别只看价格排序
- **交期**：非标件（非标齿轮、非标同步带、特殊孔径联轴器）通常需 7~15 天定制，淘宝搜索可先找代加工

## 6. 脚本用法

```bash
# 按物料模板拼关键词 + 生成链接（最常用）
python scripts/procure_link.py build --material lubricant \
    --set brand=美孚 --set product=威达Vactra --set vg=68 \
    --set kind=导轨油 --set pack=18L

# 指定渠道（默认 taobao,tmall）
python scripts/procure_link.py build -m synchronous_belt \
    --set brand=盖茨 --set model=8M --set spec="带宽20 节线长1200" --set kind=同步带 \
    -c taobao,tmall,1688

# 已有完整关键词，直接出链接
python scripts/procure_link.py link -k "美孚 威达 VG68 导轨油 18L" -c taobao

# 只要关键词（不生成链接）
python scripts/procure_link.py keyword -m lubricant --set kind=导轨油 --set vg=68

# 查看支持的物料模板 / 渠道
python scripts/procure_link.py materials
python scripts/procure_link.py channels

# 输出可直接粘贴的 Markdown（默认 JSON）
python scripts/procure_link.py build -m lubricant --set kind=导轨油 --format md
```

**字段名速查**（`--set KEY=VALUE`）：

| 字段 | 含义 | 示例 |
|---|---|---|
| `brand` | 品牌 | 美孚 / SKF / 盖茨 |
| `product` | 产品线 | 威达Vactra / 特力Tonna |
| `model` | 型号代号 | 8M / 6205 / XL3 |
| `spec` | 关键规格 | 带宽20 节线长1200 |
| `kind` | 物料名（**必填**） | 导轨油 / 深沟球轴承 |
| `vg` | 黏度等级 | 68 → 渲染为 VG68 |
| `grade` | 等级 / 精度 | CLP 220 / P5 / 8.8级 |
| `material` | 材质 | 304不锈钢 / NBR / 45钢 |
| `pack` | 包装数量 | 18L / 50个装 |
| `extra` | 附加自由文本 | 任意补充词 |

物料模板定义在 `knowledge/procure/keyword_templates.yaml`。新增物料时在该文件 `materials:` 下加一段（`keyword_order` + `field_hint` + `examples`），未收录物料自动走 `generic` 兜底模板，不会报错。

## 7. 维护要点

- 平台搜索 URL 结构若变更，只需改 `keyword_templates.yaml` 的 `channels` 段，脚本无需改动
- 本配置 `verification_status: self_defined`，不属于国标数据，无需双源核验
- 若发现某物料关键词长期搜不到结果，回到该物料的 `keyword_order` 调整字段顺序或补充 `field_hint`
