"""凭据存取 —— 走操作系统凭据库，不落明文。

约束（来自产品理念，不是实现细节）：

- 软件**不自带任何 API key**。AI 能力由用户绑定自己的账号提供，费用计入用户账号。
- key 存进 Windows 凭据管理器 / macOS Keychain / Linux Secret Service，
  **不写配置文件、不入 SQLite、不随项目导出**。
- 后端只在内存中短暂持有；任何日志与错误信息一律脱敏。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import keyring
    from keyring.errors import KeyringError
    HAS_KEYRING = True
except ImportError:  # pragma: no cover
    HAS_KEYRING = False

    class KeyringError(Exception):  # type: ignore[no-redef]
        pass

SERVICE = "mds-selector"
DEFAULT_PROFILE = "deepseek"


class CredentialError(Exception):
    """凭据库不可用，或凭据本身有问题。"""


def mask(key: str | None) -> str:
    """脱敏。任何时候要把 key 写进日志/响应，都必须先过这里。"""
    if not key:
        return ""
    k = key.strip()
    if len(k) <= 10:
        return "*" * len(k)
    return f"{k[:6]}{'*' * 8}{k[-4:]}"


@dataclass
class Binding:
    """绑定的非敏感部分。可以安全地返回给前端、写进配置。"""

    profile: str = DEFAULT_PROFILE
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = ""
    label: str = ""              # 供界面显示的脱敏 key
    bound_at: str = ""
    models_seen: list[str] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["models_seen"] = self.models_seen or []
        return d


@dataclass
class Account:
    """本机绑定的全部账号。**只有元数据，key 在系统凭据库里。**

    允许同时绑定多家、其中一家是当前生效的。这不是为了花哨：
    DeepSeek 余额用完时能立刻切到百炼继续干活，比"解绑再重绑"实用得多。
    """

    active: str = ""
    bindings: dict = field(default_factory=dict)   # provider id -> Binding

    def get(self, provider: str | None = None) -> Binding | None:
        return self.bindings.get(provider or self.active or "")

    def to_dict(self) -> dict:
        return {
            "schema": 2,
            "active": self.active,
            "bindings": {k: v.to_dict() for k, v in self.bindings.items()},
        }


def _meta_path(data_dir: Path) -> Path:
    return data_dir / "account.json"


def _binding_from(raw: dict) -> Binding:
    known = {f for f in Binding.__dataclass_fields__}
    return Binding(**{k: v for k, v in raw.items() if k in known})


def load_account(data_dir: Path) -> Account:
    """读取全部绑定。**自动迁移 v1 的单绑定格式**，不丢老用户已绑的账号。"""
    path = _meta_path(data_dir)
    if not path.exists():
        return Account()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Account()
    if not isinstance(raw, dict):
        return Account()

    if raw.get("schema") == 2 or "bindings" in raw:
        bindings = {k: _binding_from(v) for k, v in (raw.get("bindings") or {}).items()
                    if isinstance(v, dict)}
        active = str(raw.get("active") or "")
        if active not in bindings:
            active = next(iter(bindings), "")
        return Account(active=active, bindings=bindings)

    # ── v1：整个文件就是一个 Binding ──
    # 老版本只存一个账号，profile 固定是 "deepseek"。原样搬进新结构，
    # 并让它成为当前生效的那个——用户不该因为软件升级而需要重新绑定。
    b = _binding_from(raw)
    if not b.profile and not b.base_url:
        return Account()
    key = b.profile or b.provider or "deepseek"
    b.profile = key
    return Account(active=key, bindings={key: b})


def save_account(data_dir: Path, account: Account) -> None:
    _meta_path(data_dir).write_text(
        json.dumps(account.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


# --- 兼容旧调用方（单绑定语义） ---

def load_binding(data_dir: Path) -> Binding | None:
    """当前生效的那个绑定。"""
    return load_account(data_dir).get()


def save_binding(data_dir: Path, binding: Binding) -> None:
    """存一个绑定并让它生效。"""
    acc = load_account(data_dir)
    key = binding.profile or binding.provider or DEFAULT_PROFILE
    binding.profile = key
    acc.bindings[key] = binding
    acc.active = key
    save_account(data_dir, acc)


def clear_binding(data_dir: Path, provider: str | None = None) -> None:
    """删掉某一个绑定；不指定就清空全部。"""
    if provider is None:
        _meta_path(data_dir).unlink(missing_ok=True)
        return
    acc = load_account(data_dir)
    acc.bindings.pop(provider, None)
    if acc.active == provider:
        acc.active = next(iter(acc.bindings), "")
    save_account(data_dir, acc)


# --- key 本身 --------------------------------------------------------------

def _require_keyring() -> None:
    if not HAS_KEYRING:
        raise CredentialError(
            "找不到 keyring —— 没有它就只能把 API key 写成明文，那是不可接受的。"
            "请安装：pip install keyring")


def store_key(key: str, profile: str = DEFAULT_PROFILE) -> None:
    _require_keyring()
    k = (key or "").strip()
    if not k:
        raise CredentialError("API key 为空")
    try:
        keyring.set_password(SERVICE, profile, k)
    except KeyringError as exc:
        raise CredentialError(f"写入系统凭据库失败：{exc}") from exc


def read_key(profile: str = DEFAULT_PROFILE) -> str | None:
    if not HAS_KEYRING:
        return None
    try:
        return keyring.get_password(SERVICE, profile)
    except KeyringError:
        return None


def delete_key(profile: str = DEFAULT_PROFILE) -> bool:
    if not HAS_KEYRING:
        return False
    try:
        keyring.delete_password(SERVICE, profile)
        return True
    except Exception:
        # 本来就没有也算解绑成功
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
