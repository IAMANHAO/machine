"""服务端测试的共享夹具。

FakeProvider 与 env/client/vault 原先只住在 test_ai.py 里。接了四家服务商之后
test_providers.py 也要用，**复制一份必然会漂**——这个测试替身要跟着真的
Provider 接口走，两处副本迟早有一处忘了改。所以挪到 conftest 共用。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

FAKE_KEY = "sk-testonly-0123456789abcdefghijklmnop"


class FakeProvider:
    """假 provider：不发网络请求，按剧本回话。"""

    models = ["deepseek-flash", "deepseek-v4-pro"]
    script: dict = {}
    calls: list = []
    # 按调用顺序消耗的 finish_reason。"length" = 被 max_tokens 砍断，
    # 此时正文故意给一段半截 JSON —— 真实服务商就是这么表现的。
    finish_reasons: list = []

    def __init__(self, api_key, *, spec=None, base_url="", timeout=45.0):
        if not api_key or not api_key.strip():
            from server.ai.client import AIError
            raise AIError("没有可用的 API key", kind="not_bound")
        from server.ai.providers import DEFAULT_PROVIDER, get as get_spec
        self._key = api_key
        self.spec = spec or get_spec(DEFAULT_PROVIDER)
        self.base_url = base_url or self.spec.base_url

    def list_models(self):
        # 声明没有模型列表的服务商（方舟、百炼）在这里也要表现成没有，
        # 否则测试会跑在一条真实环境里不存在的路径上。
        return list(self.models) if self.spec.may_list_models else []

    def validate(self, model=""):
        from server.ai.client import ValidationResult
        models = self.list_models()
        if models:
            chosen = model if model in models else (model or models[0])
            return ValidationResult(models=models, model=chosen,
                                    method="models_list",
                                    cost_hint="未消耗任何 token。")
        chosen = (model or self.spec.model_hint or "").strip()
        if not chosen:
            from server.ai.client import AIError
            raise AIError("需要先填写模型名。", kind="model_required")
        probe = self.chat([{"role": "user", "content": "hi"}],
                          model=chosen, max_tokens=1, temperature=0)
        return ValidationResult(models=[chosen], model=chosen,
                                method="chat_probe",
                                cost_hint=f"消耗 {probe.usage.total_tokens} token。",
                                usage=probe.usage)

    def balance(self):
        return {"is_available": True, "currency": "CNY", "total_balance": "42.00",
                "granted_balance": "0.00", "topped_up_balance": "42.00"}

    @staticmethod
    def _scripted(key, default):
        """取剧本。**列表按调用顺序逐个消耗** —— 修正循环要靠这个来测：
        第一次给个不合规的，第二次给个合规的，断言引擎真的退回去让它改了。
        """
        val = FakeProvider.script.get(key, default)
        if isinstance(val, list):
            return val.pop(0) if val else default
        return val

    def chat(self, messages, *, model, json_mode=False, max_tokens=800, temperature=0.2):
        from server.ai.client import Completion, Usage
        FakeProvider.calls.append({"model": model, "json_mode": json_mode,
                                   "max_tokens": max_tokens,
                                   "turns": len(messages)})
        sys_prompt = messages[0]["content"]
        if "意图解析" in sys_prompt:
            body = self._scripted("intent", {
                "material": "synchronous_belt",
                "values": {"P": 5.5, "n1": 1450}, "unmatched": [], "notes": ""})
        elif "参数建议" in sys_prompt:
            body = self._scripted("suggest", {
                "suggestions": {"a0": {"value": 400, "rationale": "取推荐区间中部"}},
                "skipped": {}})
        elif "依据检索" in sys_prompt:
            body = self._scripted("basis", {"candidates": [], "material_id": "",
                                            "name_zh": "", "notes": ""})
        elif "参数引导" in sys_prompt:
            body = self._scripted("inputs", {"inputs": [], "notes": ""})
        elif "分步计算与校核" in sys_prompt:
            body = self._scripted("steps", {"steps": [], "result": []})
        elif "参考草案" in sys_prompt:
            body = self._scripted("ai_draft", {"name_zh": "", "steps": [],
                                               "result": [], "caveats": []})
        else:
            return Completion(text=self._scripted("explain", "这一步在算设计功率。"),
                              usage=Usage(10, 20, 30, 0, model))
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        reason = (FakeProvider.finish_reasons.pop(0)
                  if FakeProvider.finish_reasons else "stop")
        if reason == "length":
            # 砍掉后半截，模拟真实的截断：JSON 解析不了，但不是模型写错了
            text = text[:max(1, len(text) // 3)]
        return Completion(text=text, finish_reason=reason,
                          usage=Usage(100, 50, 150, 20, model))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """每个测试独立的数据目录 + 内存凭据库，绝不碰开发机的真实凭据。"""
    monkeypatch.setenv("MDS_DATA_DIR", str(tmp_path / "data"))

    vault: dict = {}
    from server.ai import credentials as cred
    monkeypatch.setattr(cred, "HAS_KEYRING", True)
    monkeypatch.setattr(cred, "keyring", type("K", (), {
        "set_password": staticmethod(lambda s, u, p: vault.__setitem__((s, u), p)),
        "get_password": staticmethod(lambda s, u: vault.get((s, u))),
        "delete_password": staticmethod(lambda s, u: vault.pop((s, u))),
    })(), raising=False)

    FakeProvider.script = {}
    FakeProvider.calls = []
    FakeProvider.finish_reasons = []
    from server import ai as ai_mod
    from server.ai import tasks
    monkeypatch.setattr(ai_mod, "Provider", FakeProvider)
    monkeypatch.setattr(tasks, "Provider", FakeProvider, raising=False)

    from server.main import app
    with TestClient(app) as c:
        yield c, vault


@pytest.fixture()
def client(env):
    return env[0]


@pytest.fixture()
def vault(env):
    return env[1]

@pytest.fixture()
def bind_as(client):
    """按服务商绑定一个账号。绑完即生效，返回响应体。

    没有模型列表接口的服务商（方舟/百炼）必须给 model，
    这一点与真实行为一致——夹具不替测试绕过这条约束。
    """
    def _bind(provider: str = "deepseek", *, model: str = "", key: str = FAKE_KEY):
        payload = {"api_key": key, "provider": provider}
        if model:
            payload["model"] = model
        r = client.post("/api/account/bind", json=payload)
        assert r.status_code == 200, r.text
        return r.json()
    return _bind


@pytest.fixture()
def fake():
    """conftest 里那个 FakeProvider 类**本身**。

    **不要在测试模块里写 `from tests.conftest import FakeProvider`。**
    tests/ 不是包，pytest 把 conftest 当顶层模块加载；那样 import 会生成
    第二个模块对象、第二个 FakeProvider 类，往它身上摆的剧本对真正在用的
    那个类毫无作用——表现是"剧本明明设了，模型却总返回默认值"。
    排查过一次，用这个夹具拿类，别再踩。
    """
    return FakeProvider
