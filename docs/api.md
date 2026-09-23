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

`suggest` 的返回分三组：

```json
{ "suggestions": { "a0": { "value": 400, "rationale": "...", "unit": "mm" } },
  "skipped":     { "i": "规格里没有给推荐值，需要你按实际工况填写" },
  "rejected":    { "P": "建议值 99999 没通过参数校验：…超出允许上限 500" } }
```

**`rejected` 是结构性保证**：每个建议值在返回前都跑过 `mds.runner._coerce`——
与用户手输完全同一条通道。调用方拿不到一个绕过校验的数。

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
