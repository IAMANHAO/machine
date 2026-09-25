# HTTP API

服务只监听 `127.0.0.1`，默认端口 8756。这是一个本地工具，不是一台服务器。

交互式文档：服务起来后访问 `/docs`（FastAPI 自带）。

## 错误约定

所有业务错误走 `detail`，带结构化字段：

```json
{ "detail": { "error": "InputError", "message": "传递功率（kW） = 9999 超出允许上限 500",
              "param": "P", "value": 9999, "domain": {"min": 0.01, "max": 500} } }
```

| 状态码 | 含义 |
|---|---|
| 400 | 引擎错误（SpecError、DataMissing 等） |
| 404 | 物料/项目/数据表不存在，或导出格式不支持 |
| 409 | 状态冲突：未绑定账号调 AI、只读知识库写入 |
| 422 | 参数不合法（`param` 指出是哪一项）、编辑被守卫拒绝 |
| 429 | AI 调用超出每日上限 |
| 502 | 上游 AI 服务暂时不可用（可重试） |

**注意**：选型算不出来**不是** HTTP 错误。`/selection/run` 返回 200，
状态在 `trace.status` 里（`data_missing` / `no_solution` / `needs_choice`）。
这是有意的——"算不出来"是一个有价值的业务结果，不是请求失败。

---

## 健康与目录

### `GET /api/health?mode=auto`

```json
{ "status": "ok", "engine_version": "0.1.0", "skill_root": "...",
  "online": true, "ai_bound": false, "mode": "offline",
  "workflows": 2, "materials": 12,
  "knowledge_writable": true, "packaged": false }
```

`online` 只探 TCP 可达，不发任何请求、不带任何凭据。
`mode` 是**生效模式**（用户偏好 ∧ 绑定状态），不是用户选的那个。

### `GET /api/materials`

首页物料入口。`status` 三态：

- `ready` —— 有可执行 YAML
- `cache_only` —— 有 .md 与缓存，尚未规格化
- `planned` —— 连缓存都还没有（来自 `knowledge/index.yaml` 的 `planned_materials`）

`confidence` 是该物料所有数据表里最低的那一档。

### `GET /api/workflow/{material}`

阶段 2 表单所需的全部信息：`inputs`（含枚举选项与中文标签、`domain`、
`required_when`、`depends_on`）、`steps` 大纲、`sources`、`notes`。

`depends_on` 是 `required_when` 的结构化形式（`{field, equals}`），
前端据此显隐分支字段；复杂条件为 `null`，前端则全部显示。

---

## 选型

### `POST /api/selection/run`

```json
{ "material": "synchronous_belt",
  "values": { "P": 5.5, "n1": 1450, "belt_type": "H", "...": "..." },
  "choices": { "belt_type_sel": "H" },
  "project_id": null,
  "save": true }
```

返回 `{ trace, procure, procure_error, project_id }`。

**这是一个纯函数**：相同输入 + 相同缓存版本 → 相同 trace。
`choices` 用于回答 `select` 步骤的决策。

`trace` 的结构见[架构](architecture.md#数据流一次选型)。关键字段：

| 字段 | 用途 |
|---|---|
| `steps[]` | 阶段 3 计算卡片。每步含 `formula` / `substitution` / `value_display` / `source` |
| `checks[]` | 阶段 4。**已排除 skipped 与 not_applicable** |
| `result[]` | 阶段 5 表 2。不适用的行不会出现 |
| `sources[]` | 信源清单，含 `second_source` 与 `fingerprint` |
| `confidence` | 所有引用表里最低的一档 |
| `blocker` | 中断原因（含 `gap` 或 `remedy`） |

### `POST /api/procure/links`

```json
{ "material_template": "lubricant",
  "fields": { "brand": "美孚", "vg": "68", "kind": "导轨油", "pack": "18L" } }
```

返回关键词、备选关键词与各渠道搜索链接。

链接是**搜索页**而非具体商品，响应里的 `note` 会说明这一点——
不要在界面上把它包装成"找到了这个商品"。

---

## 项目库

| 端点 | 说明 |
|---|---|
| `GET /api/projects?limit=20` | 列表，按更新时间倒序 |
| `GET /api/projects/{pid}` | 详情，含 `trace`、重算的 `procure`、`stale_sources` |
| `DELETE /api/projects/{pid}` | 删除 |

`stale_sources`：存档后被修改过的数据表。
通过比对 trace 里记录的内容指纹与当前值得出，界面据此提示"这份结果是旧数据算出来的"。

`procure` 是**从存档 trace 重算**的，不是存下来的——
关键词模板可能更新，重算保证拿到的是当前规则下的结果。

---

## 知识库

| 端点 | 说明 |
|---|---|
| `GET /api/knowledge/audit?probe=true` | 全物料体检汇总 |
| `GET /api/knowledge/{m}/audit?probe=true` | 单物料，含可达性矩阵与缺口清单 |
| `GET /api/knowledge/{m}/tables/{name}` | 表的完整内容 + 元数据 + 指纹 |
| `POST .../verify` | 标绿。**`second_source` 必填**，否则 422 |
| `POST .../unverify` | 退回 single_source |
| `PATCH .../sources` | 改信源与待办 |
| `POST .../points` | 往插值表补数据点 |
| `PATCH .../scalar` | 改一个标量 |

所有写入在打包态返回 **409**（随包数据只读）。
写入成功后会清引擎缓存，保证界面与下一次计算看到同一份数据。

`probe=true` 会真跑引擎，比较慢；总览页可以传 `false`。

---

## 账号（BYOK）

**软件不自带任何 API key，不自建中转，不代付费用。**

支持 DeepSeek / 火山方舟（豆包）/ 阿里百炼（千问）/ OpenAI，
以及任何自填 base_url 的 OpenAI 兼容服务。**可同时绑定多家，一家生效。**

| 端点 | 说明 |
|---|---|
| `GET /api/account/providers` | 可绑定的服务商清单（**只有接入事实，不含凭据**） |
| `GET /api/account?mode=auto` | 当前绑定、**全部已绑账号**、限额、今日用量 |
| `POST /api/account/bind` | 验证并保存，绑完即生效。体：`{api_key, provider, base_url?, model?}` |
| `POST /api/account/activate` | 切换生效账号。**不动任何凭据** |
| `DELETE /api/account?provider=` | 解绑。不带 `provider` 就解绑当前生效那家 |
| `GET /api/account/models` | 该账号真实可用的模型清单（没有此接口的服务商返回空 + `note`） |
| `POST /api/account/model` | 切换主模型 |
| `GET /api/account/balance` | 真实余额（只有 DeepSeek 有；其余返回 `available:false` + 原因） |
| `GET/PUT /api/account/limits` | 调用上限 |

`bind` 的响应带一个 `validation` 字段，**如实说明这次验证花没花钱**：

```json
{ "validation": { "method": "chat_probe", "model": "doubao-seed-2-1-pro-260628",
                  "cost_hint": "…没有免费的模型列表接口，改用一次最小对话验证，消耗 4 token。" } }
```

有模型列表接口的走 `GET /models`（不消耗 token）；没有的（方舟/百炼）
发一次 `max_tokens=1` 的探针。**悄悄花掉用户的钱不可接受**，所以要返回出来。

**任何响应都不回显 key**，只给脱敏标签（`sk-tes********mnop`）。

---

## AI 三接口

都不碰数值计算。详见 [AI 层](ai.md)。

| 端点 | 未绑定 / 离线时 |
|---|---|
| `POST /api/ai/intent` | 降级为关键词匹配，`source: "offline"` |
| `POST /api/ai/suggest` | 降级为规格里的 `typical` 值 |
| `POST /api/ai/explain` | **409**（没有诚实的降级方案） |
| `POST /api/guided/*` | **409**（阶段 1 要真的联网取证，没有诚实的降级方案） |

`suggest` 的返回分三组：

```json
{ "suggestions": { "a0": { "value": 400, "rationale": "...", "unit": "mm" } },
  "skipped":     { "i": "规格里没有给推荐值，需要你按实际工况填写" },
  "rejected":    { "P": "建议值 99999 没通过参数校验：…超出允许上限 500" } }
```

**`rejected` 是结构性保证**：每个建议值在返回前都跑过 `mds.runner._coerce`——
与用户手输完全同一条通道。调用方拿不到一个绕过校验的数。

### 知识库里没有的物料：引导式选型

按 SKILL.md 的阶段 0~6 一段一段推进，**每一段都由用户拍板**。
（旧的 `POST /api/ai/draft-workflow` 与 `/save-draft` 已删除——
那条路的门槛只有"格式合法 + 不引用数据表"，拦不住编造的公式。）

| 端点 | 说明 |
|---|---|
| `GET /api/guided` | 没走完的会话（用户去翻手册、隔天回来要能接着走） |
| `POST /api/guided` | 体 `{material_text}` → 开一个会话。**此时还没检索，没花 token** |
| `GET /api/guided/{sid}` | 会话视图，含 `can_*`（前端据此置灰按钮） |
| `POST /api/guided/{sid}/research` | 阶段 1：检索 → AI 挑候选 → **服务端逐条取证** |
| `POST /api/guided/{sid}/basis` | 体 `{basis_id}` 或 `{custom}` → 用户拍板选一条依据 |
| `POST /api/guided/{sid}/inputs` | 阶段 2：列出要问的参数（分轮 ≤6 项） |
| `POST /api/guided/{sid}/inputs/confirm` | 体 `{inputs?}` → 确认（可改过） |
| `POST /api/guided/{sid}/steps` | 阶段 3/4：整套计算与校核 |
| `POST /api/guided/{sid}/steps/confirm` | 体 `{confirmed: true}` → **一次性过目确认** |
| `GET /api/guided/{sid}/workflow` | 阶段 2 表单，形状与 `/api/workflow/{m}` 完全相同 |
| `POST /api/guided/{sid}/run` | 体 `{values, choices}` → **落盘之前也能跑**（同一个 runner） |
| `POST /api/guided/{sid}/save` | 存进用户目录，下次离线也能选 |
| `POST /api/guided/{sid}/ai-draft` | **兜底档**：AI 直接做完（含出数）。**返回的不是选型结果** |
| `DELETE /api/guided/{sid}` | 丢弃会话 |
| `DELETE /api/ai/saved/{material}` | 删掉一个已保存的自建物料（随包物料删不掉） |

#### 状态码说的是什么

**顺序不对是 409，不是 400** —— 请求本身没毛病，是流程状态不允许：

```json
{ "detail": { "error": "依据还没确认。阶段 1 必须先定下依据——后面的公式都要对着它核对。",
              "kind": "out_of_order", "stage": "basis" } }
```

| 情况 | 码 |
|---|---|
| 依据/参数/公式没确认就往下走 | 409 `out_of_order` / `not_confirmed` |
| 未绑定账号或离线 | 409 `not_bound` |
| 选了一条取证不通过的依据 | 422 `unverified_basis` |
| 检索三条路都没结果 | 422 `no_search_results` |
| 模型连着几轮都没过闸门 | 422 `guidance_rejected` |
| 超出用量上限，或单次 token 上限低于这一步的结构下限 | 429 |

**闸门在后端，不在前端按钮的置灰状态上。** 前端的 `can_*` 只是提示；
把闸门放在前端等于没有闸门，curl 一下就绕过去了。

#### 模型不合规时的返回

不是直接甩给用户——服务端先把逐条原因**退回给模型让它改**（最多 2 轮），
改不好才返回 422，并把每一轮都带回来：

```json
{ "detail": {
    "error": "guidance_rejected", "stage": "steps",
    "message": "模型在这一步上连着 3 次都没能给出合规的输出。…",
    "reasons": ["步骤 sigma 的 expr 里出现了字面量 1.25。公式里只允许单位换算因子…"],
    "repair_log": [ { "attempt": 1, "reasons": ["…"], "passed": false,
                      "usage": { "total_tokens": 1520 } } ] } }
```

#### 取证结果长什么样

每条候选依据都带 `evidence`：

```json
{ "id": "b1", "claim": "GB/T 1095-2003《普通型 平键》表 1",
  "urls": ["https://www.mechtool.cn/key.html"],
  "dropped_urls": ["https://made-up.example/x"],
  "evidence": { "status": "trusted", "usable": true,
                "domains": ["mechtool.cn"],
                "hits": [ { "url": "…", "title": "…", "tier": "trusted",
                            "fingerprint": "a1b2c3d4e5f6" } ],
                "misses": [], "failures": [] } }
```

`status` 五档：`cross_checked` / `trusted` / `single_source` /
`unverified`（**选不了**）/ `unverifiable_claim`。
`dropped_urls` 是被剔除的引用——不在检索结果集里，即模型凭记忆编的。

#### 兜底档返回什么

`/ai-draft` 的返回**刻意不是 trace 的形状**——它不该能被当成选型结果传下去：

```json
{ "draft": {
    "parsed": true,
    "steps": [ { "label": "…", "formula": "P_ca = K_A * P",
                 "substitution": "1.3 * 5.5", "value": "7.15",
                 "source": "…（待核）",
                 "arith": "ok", "arith_value": "7.15" } ],
    "arith": { "ok": 6, "mismatch": 1, "unreadable": 0 },
    "text": "# ⚠ AI 参考草案 —— 这不是选型结果
…" },
  "session": { "has_ai_draft": true, "can_run": false, "steps": [] } }
```

`arith` 是**引擎重算 AI 自己写的代入式**的结果，不是"公式对不对"。
`session.steps` 保持为空——所以 `/spec` 与 `/save` 照样 409。

#### 搜索服务的绑定

与 AI 绑定**完全独立**，凭据走同一个系统凭据库。

| 端点 | 说明 |
|---|---|
| `GET /api/account/search` | 状态 + 可绑服务商 + 白名单站点 + `rung`（实际会走哪一级） |
| `POST /api/account/search/bind` | 体 `{api_key, provider}`。**验证会真的发一次查询，消耗一次配额** |
| `POST /api/account/search/activate` | 切换生效的那家，不动凭据 |
| `DELETE /api/account/search` | 解绑（**不动 AI 账号**） |

#### 物料出身

`/api/materials` 与 `/api/workflow/{m}` 都带 `provenance`：
`builtin`（随包）/ `user`（手写 YAML）/ `user_guided`（引导式）/
`ai_generated`（旧版起草，只读兼容）。后两者 `confidence` 恒为 `unknown`。

`/api/ai/intent` 认不出物料时不再是死胡同，会带回：

```json
{ "material": null, "unknown_material": "磁吸铁片", "can_guide": true }
```

离线时 `can_guide` 恒为 `false`——阶段 1 要真的联网取证，不给点了没反应的按钮。

---

## 导出

### `POST /api/export/{pdf|xlsx}`

```json
{ "material": "synchronous_belt", "values": {...}, "choices": {...} }
```

**刻意不接收前端传来的 trace**——那等于让客户端决定报告内容。
接口收的是输入参数，服务端自己重跑一遍引擎再渲染。
这保证了"本报告可由它自己列出的那组输入完整复现"是句真话。

响应头：

- `Content-Disposition: attachment; filename*=UTF-8''...` —— 中文文件名走 RFC 5987
- `X-Report-Status` / `X-Report-Confidence` —— 不用解析文件就能知道结果状态

流程没走完也会返回 200 并出报告，但报告里会写明卡在哪、且不会有结果表。
