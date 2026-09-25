"""用量与上限 —— 花的是用户自己的钱，必须看得见、管得住。

刻意**不做费用换算**：单价会变，把一张价目表硬编码进来，早晚会给出一个
过期的"预估费用"数字。这个产品的立身之本就是不给来路不明的数字，
所以这里只报两样都是真的东西：本次调用的实际 token 数，以及从
/user/balance 拿到的真实余额。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass
class Limits:
    """默认值取保守值：宁可挡住，也不要让人半夜发现账单跑飞了。

    **但 `max_tokens_per_call` 不是"花费上限"，是"单次输出的长度上限"。**
    服务商按实际生成的 token 计费，把它压低省不下钱——只会把长输出**截断**，
    换来一段没法用的半截 JSON，那次调用的钱照样花了。
    真正管住钱的是 `max_calls_per_day`。

    这个默认值原来是 1200，正好卡死引导式的"整套计算与校核"那一步
    （它要三千多 token），表现成"模型连着两次不合规"——
    实际是我们自己砍断的。所以调到 4000。
    """

    max_tokens_per_call: int = 4000
    max_calls_per_day: int = 200
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


class BudgetExceeded(Exception):
    def __init__(self, message: str, *, limit: str, used: int, cap: int):
        super().__init__(message)
        self.limit = limit
        self.used = used
        self.cap = cap

    def as_dict(self) -> dict:
        return {"error": "BudgetExceeded", "message": str(self),
                "limit": self.limit, "used": self.used, "cap": self.cap}


class Budget:
    """按天记账，存在用户数据目录里。"""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "ai_usage.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # 旧的默认值。它卡死了引导式的"整套计算与校核"那一步，是个 bug，
    # 不是用户的选择——所以**只在它原样没动过时**才上调。
    # 用户自己改成别的数（哪怕更小）就一律不碰：那是他的决定。
    _MISCALIBRATED_DEFAULT = 1200

    def limits(self) -> Limits:
        raw = self._load().get("limits") or {}
        known = {f for f in Limits.__dataclass_fields__}
        lim = Limits(**{k: v for k, v in raw.items() if k in known})
        if lim.max_tokens_per_call == self._MISCALIBRATED_DEFAULT:
            lim.max_tokens_per_call = Limits().max_tokens_per_call
        return lim

    def set_limits(self, limits: Limits) -> Limits:
        data = self._load()
        data["limits"] = limits.to_dict()
        self._save(data)
        return limits

    def today(self) -> dict:
        data = self._load()
        key = date.today().isoformat()
        day = (data.get("days") or {}).get(key) or {}
        return {
            "date": key,
            "calls": int(day.get("calls") or 0),
            "prompt_tokens": int(day.get("prompt_tokens") or 0),
            "completion_tokens": int(day.get("completion_tokens") or 0),
            "total_tokens": int(day.get("total_tokens") or 0),
            "cache_hit_tokens": int(day.get("cache_hit_tokens") or 0),
        }

    def check(self) -> None:
        """调用前的闸门。超限直接拒绝，不静默继续。"""
        lim = self.limits()
        if not lim.enabled:
            return
        used = self.today()["calls"]
        if used >= lim.max_calls_per_day:
            raise BudgetExceeded(
                f"今天已调用 {used} 次，达到上限 {lim.max_calls_per_day} 次。"
                "可以在设置页调整上限；计算与校核不受影响，照常可用。",
                limit="calls_per_day", used=used, cap=lim.max_calls_per_day)

    def cap_tokens(self, requested: int | None = None) -> int:
        lim = self.limits()
        if not lim.enabled:
            return requested or lim.max_tokens_per_call
        return min(requested or lim.max_tokens_per_call, lim.max_tokens_per_call)

    def token_ceiling(self) -> int:
        """用户设的单次上限本身。截断之后要往上顶到这里，不能再高。"""
        return self.limits().max_tokens_per_call

    def require(self, floor: int, what: str) -> None:
        """这一步在结构上至少要多少 token —— 不够就**在发请求之前**拦下来。

        低于这个数产出的一定是被截断的半截 JSON。那不是"省钱"：
        调用照样计费，只是换回来一段没法用的东西，然后还要再花一次钱重试。
        所以宁可现在拦住，并告诉用户去改哪个设置。
        """
        lim = self.limits()
        if not lim.enabled or lim.max_tokens_per_call >= floor:
            return
        raise BudgetExceeded(
            f"「{what}」这一步至少需要 {floor} token 才能输出完整结果，"
            f"而你的单次调用上限是 {lim.max_tokens_per_call}。"
            "请在设置页把它调到 {floor} 以上——**这个上限不是花费上限**，"
            "服务商按实际生成的长度计费，调高它不会让短回复变贵，"
            "只是让长回复不被砍断。".replace("{floor}", str(floor)),
            limit="tokens_per_call", used=lim.max_tokens_per_call, cap=floor)

    def record(self, usage) -> dict:
        data = self._load()
        days = data.setdefault("days", {})
        key = date.today().isoformat()
        day = days.setdefault(key, {})
        day["calls"] = int(day.get("calls") or 0) + 1
        for f in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens"):
            day[f] = int(day.get(f) or 0) + int(getattr(usage, f, 0) or 0)
        # 只留最近 60 天，别让这个文件无限长
        for stale in sorted(days)[:-60]:
            days.pop(stale, None)
        self._save(data)
        return self.today()
