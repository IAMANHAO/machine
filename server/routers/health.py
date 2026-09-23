"""健康检查与运行模式 —— 顶栏的在线/离线徽章读这里。"""

from __future__ import annotations

import socket

from fastapi import APIRouter

from .. import ai, engine
from ..config import describe, knowledge_writable, skill_root
from ..schemas import HealthOut

router = APIRouter(tags=["health"])


def _network_reachable(timeout: float = 1.0) -> bool:
    """只探 TCP 可达，不发任何请求、不带任何凭据。

    探测目标跟着**用户实际绑定的那家**走：绑了 OpenAI 的人，
    api.deepseek.com 通不通与他无关；反过来也一样。
    末尾的公共 DNS 是兜底——没绑任何账号时也要能判断有没有网。
    """
    targets: list[tuple[str, int]] = []
    b = ai.binding()
    if b and b.base_url:
        host = b.base_url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        if host:
            targets.append((host, 443))
    targets.append(("223.5.5.5", 53))

    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


@router.get("/health", response_model=HealthOut)
def health(mode: str = "auto") -> dict:
    mats = engine.materials()
    bound = ai.is_bound()
    return {
        "status": "ok",
        "engine_version": engine.engine_version(),
        "skill_root": str(skill_root()),
        "online": _network_reachable(),
        "ai_bound": bound,
        "mode": ai.effective_mode(mode),
        "workflows": sum(1 for m in mats if m["status"] == "ready"),
        "materials": len(mats),
        "knowledge_writable": knowledge_writable(),
        "packaged": describe()["frozen"],
    }
