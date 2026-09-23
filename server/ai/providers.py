"""服务商清单 —— 每一家的接入事实。

本软件**不自带任何 API key**，这里登记的只是"去哪调、怎么调"，
不含任何凭据。key 始终由用户自己在各家开放平台创建，存进系统凭据库。

## 为什么要有这张表

四家都号称"兼容 OpenAI"，但兼容的**程度不一样**：

| | 模型列表 | 余额查询 | 关闭思考模式 |
|---|---|---|---|
| DeepSeek   | ✅ GET /models | ✅ /user/balance | 不需要 |
| OpenAI     | ✅ GET /models | ❌ 早已下线 | 不需要 |
| 火山方舟   | ❓ 文档未提 | ❌ | `thinking: {type: disabled}` |
| 阿里百炼   | ❓ 文档未提 | ❌ | `enable_thinking: false` |

差异集中在这张表里，`client.Provider` 只管按表行事。

## 接口事实的出处（2026-09 核实）

- **DeepSeek**：`https://api.deepseek.com`，`GET /models`、`GET /user/balance`
- **火山方舟**：官方「兼容 OpenAI SDK」文档（更新于 2026.06.23）给出
  `base_url="https://ark.cn-beijing.volces.com/api/v3"`，模型 id 形如
  `doubao-seed-2-1-pro-260628`；关闭深度思考走 `extra_body={"thinking": {"type": "disabled"}}`，
  即请求体顶层的 `thinking` 字段。该文档**只演示了 chat.completions**，
  并明确说向量化不支持 OpenAI API，未提及模型列表接口。
- **阿里百炼**：官方「OpenAI兼容-Chat」文档（更新于 2026-09-22）说明
  北京地域建议迁移到 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`，
  但**「现有域名仍可正常使用」**，所以默认仍用 `https://dashscope.aliyuncs.com/compatible-mode/v1`
  （不需要用户去找业务空间 ID）。`enable_thinking` 是 extra_body 参数。
- **OpenAI**：`https://api.openai.com/v1`，`GET /v1/models`。

## 一条刻意的设计

**能力是"探测"出来的，不是"声明"死的。** 下面的 `may_list_models` 只是
要不要**先试一把**的提示；`client.Provider.validate()` 真的去请求，
404/405 就退回最小对话探针。理由是：这四家的接口都在变，
把"某家不支持模型列表"写死在代码里，迟早会变成一条过期的断言。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    """一家服务商的接入事实。不含任何凭据。"""

    id: str                      # 同时是系统凭据库里的 profile 名
    name_zh: str
    base_url: str
    console_url: str             # 用户去哪创建 key
    model_hint: str              # 输入框占位示例，**不是可用模型清单**
    # 绑定对话框里必须让用户看到的话。**渲染成纯文本，别写 markdown**——
    # 界面不解析它，写了 **粗体** 只会原样显示成星号。
    notes: str
    extra_body: dict = field(default_factory=dict)
    balance_path: str = ""       # 空 = 这家没有余额查询接口
    may_list_models: bool = True  # 只是"先试一把"的提示，不是断言
    key_env_hint: str = ""       # 官方文档里的环境变量名，便于用户对号入座

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name_zh": self.name_zh,
            "base_url": self.base_url,
            "console_url": self.console_url,
            "model_hint": self.model_hint,
            "notes": self.notes,
            "has_balance": bool(self.balance_path),
            "may_list_models": self.may_list_models,
            "key_env_hint": self.key_env_hint,
        }


DEEPSEEK = ProviderSpec(
    id="deepseek",
    name_zh="DeepSeek 深度求索",
    base_url="https://api.deepseek.com",
    console_url="https://platform.deepseek.com/api_keys",
    model_hint="deepseek-chat",
    key_env_hint="DEEPSEEK_API_KEY",
    balance_path="/user/balance",
    notes="支持模型列表与余额查询，绑定验证不消耗 token。",
)

ARK = ProviderSpec(
    id="ark",
    name_zh="火山方舟（豆包 Doubao）",
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    console_url="https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
    model_hint="doubao-seed-2-1-pro-260628",
    key_env_hint="ARK_API_KEY",
    may_list_models=False,
    # 官方文档：关闭深度思考走 extra_body={"thinking": {"type": "disabled"}}。
    # 这三个任务都要结构化输出、不需要思维链，开着只会多花钱还可能破坏 JSON。
    extra_body={"thinking": {"type": "disabled"}},
    notes=(
        "方舟的模型 id 带日期后缀（如 doubao-seed-2-1-pro-260628），"
        "官方文档没有提供模型列表接口，需要你从控制台复制模型 ID 填进来。"
        "已默认关闭深度思考——本软件的三个 AI 任务都要结构化输出，"
        "思考模式只会多花钱。"
    ),
)

DASHSCOPE = ProviderSpec(
    id="dashscope",
    name_zh="阿里百炼（通义千问 Qwen）",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    console_url="https://bailian.console.aliyun.com/?tab=model#/api-key",
    model_hint="qwen-plus",
    key_env_hint="DASHSCOPE_API_KEY",
    may_list_models=False,
    # qwen3 系列是混合思考模型，非流式调用必须显式关掉，否则直接报错。
    extra_body={"enable_thinking": False},
    notes=(
        "百炼在推业务空间专属域名，但官方文档写明「现有域名仍可正常使用」，"
        "所以这里默认用通用域名，不必去找业务空间 ID。"
        "API Key 按地域绑定，key 与域名地域对不上会报 401。"
        "已默认关闭思考模式（qwen3 非流式调用必须关）。"
    ),
)

OPENAI = ProviderSpec(
    id="openai",
    name_zh="OpenAI（ChatGPT）",
    base_url="https://api.openai.com/v1",
    console_url="https://platform.openai.com/api-keys",
    model_hint="gpt-4.1-mini",
    key_env_hint="OPENAI_API_KEY",
    notes=(
        "OpenAI 早已下线面向 API key 的余额查询接口，用量与余额请去控制台看。"
        "国内网络通常直连不到 api.openai.com，需要你自己解决网络可达性——"
        "本软件不内置任何代理。"
    ),
)

OPENAI_COMPATIBLE = ProviderSpec(
    id="custom",
    name_zh="其它 OpenAI 兼容服务（自填）",
    base_url="",
    console_url="",
    model_hint="",
    notes=(
        "自填 base_url 与模型名。只要对方实现了 OpenAI 的 "
        "POST /chat/completions，就能用。"
    ),
)

REGISTRY: dict[str, ProviderSpec] = {
    p.id: p for p in (DEEPSEEK, ARK, DASHSCOPE, OPENAI, OPENAI_COMPATIBLE)
}

DEFAULT_PROVIDER = DEEPSEEK.id


def get(provider_id: str | None) -> ProviderSpec:
    """按 id 取 spec。认不出来的一律当自填服务处理，不猜。"""
    return REGISTRY.get((provider_id or "").strip(), OPENAI_COMPATIBLE)


def listing() -> list[dict]:
    """给设置页的服务商清单，顺序即界面展示顺序。"""
    return [p.to_dict() for p in
            (DEEPSEEK, ARK, DASHSCOPE, OPENAI, OPENAI_COMPATIBLE)]


def guess_from_base_url(base_url: str) -> str:
    """只在迁移旧数据时用：老的 account.json 只存了 base_url，没存 provider。"""
    u = (base_url or "").lower()
    for spec in (DEEPSEEK, ARK, DASHSCOPE, OPENAI):
        host = spec.base_url.split("//", 1)[-1].split("/", 1)[0]
        if host and host in u:
            return spec.id
    return OPENAI_COMPATIBLE.id
