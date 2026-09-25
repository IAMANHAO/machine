"""server.guided —— 引导式选型的会话状态机（SKILL.md 阶段 0~6）。

## 它替代了什么

上一版：AI 一次性起草一份 YAML，过两道闸门就交给引擎跑。
**门槛太低**——拦得住编造的数据表，拦不住编造的公式；而且跳过了整个阶段 1，
没有依据、没有交叉验证，用户全程没有做出过任何一个选择。

现在：严格按 SKILL.md 的阶段走，一段一段推进，每一段都由用户拍板。

    阶段 0  物料识别      →  AI 提 id/中文名，用户确认
    阶段 1  依据检索      →  真的联网检索 → 服务端逐条取证 → **用户选一条**
    阶段 2  参数引导      →  AI 按已确认依据给清单（分轮 ≤6 项），用户填
    阶段 3  分步计算  ┐
    阶段 4  校核      ┘   →  AI 给整套步骤 → **用户一次性过目确认** → 引擎跑
    阶段 5  结果          →  与内置物料完全一致（同一份 trace）
    阶段 6  采购          →  与内置物料完全一致

## 两条不能含糊的规矩

1. **阶段推进是这里的 `if`，不是 AI 输出里的某个字段。** AI 只填内容，
   不控流程。它说"我觉得可以进下一步"不算数。
2. **每一道门都真的会拦。** 依据没确认就调 `/inputs` → 409；
   公式没确认就想执行 → 409。不是前端把按钮置灰就完事——
   前端只是提示，后端才是闸门。

## 会话为什么要落盘

一次引导要花二十分钟：用户得去翻手册查系数、得对照依据核公式。
放内存里撑不过一次热重载，更撑不过用户隔天回来接着走。
所以存进 SQLite（`server/store.py` 的 `guided_sessions`）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# 取证层按它的职责起个别名：本模块里有一个同名的 research() 函数（阶段 1），
# 直接 `from . import research` 会被那个函数遮住，`evidence.verify_basis`
# 在运行时才炸。与 decisions #22 里 user_root 那次是同一类毛病。
from . import drafts, search_providers, store
from . import research as evidence
from .config import data_dir

# 会话阶段。名字说的是"现在轮到谁做什么"，不是"做完了什么"。
STAGE_NEW = "new"              # 刚开，还没检索
STAGE_BASIS = "basis"          # 候选依据已取证，等用户选一条
STAGE_INPUTS = "inputs"        # 依据已确认，参数清单已给出，等用户确认
STAGE_STEPS = "steps"          # 参数已确认，整套步骤已给出，等用户一次性确认
STAGE_READY = "ready"          # 公式已确认，可以按普通物料那样跑了
STAGE_SAVED = "saved"          # 已存进用户目录

# 喂给模型的网页摘录上限。抓回来的正文可能有 2 MB，全塞进上下文既贵又没用。
EXCERPT_CHARS = 2600
MAX_EXCERPTS = 4


class GuidedError(Exception):
    """引导流程的阻断。`kind` 让前端能分类，`stage` 说明卡在哪一段。

    大多数场景是"顺序不对"（依据还没确认就想要参数清单），
    对应 HTTP 409 —— 那不是请求格式错，是流程状态不允许。
    """

    def __init__(self, message: str, *, kind: str = "out_of_order",
                 stage: str = ""):
        super().__init__(message)
        self.kind = kind
        self.stage = stage

    def as_dict(self) -> dict:
        return {"error": str(self), "kind": self.kind, "stage": self.stage}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Session:
    """一次引导的全部状态。**只有它说流程走到哪了。**"""

    id: str
    material_text: str
    stage: str = STAGE_NEW
    material_id: str = ""
    name_zh: str = ""
    model: str = ""

    # 阶段 1
    search: dict = field(default_factory=dict)      # 走了哪条检索路径
    known_urls: list = field(default_factory=list)  # 允许 AI 引用的 URL 全集
    candidates: list = field(default_factory=list)  # 每条带 evidence
    basis: dict = field(default_factory=dict)       # 用户确认的那一条
    excerpts: list = field(default_factory=list)    # 取证通过的正文摘录

    # 阶段 2
    inputs: list = field(default_factory=list)
    inputs_confirmed_at: str = ""

    # 阶段 3/4
    steps: list = field(default_factory=list)
    result: list = field(default_factory=list)
    procure: dict = field(default_factory=dict)
    missing_inputs: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    confidence_note: str = ""
    formulas_confirmed_at: str = ""

    # 兜底档：AI 直接做完的参考草案。**刻意不是 trace 的形状**，
    # 也刻意不参与 spec_dict() —— 它不能变成物料，不能进选型报告。
    # 存在会话里只是为了刷新页面之后还看得见。
    ai_draft: dict = field(default_factory=dict)

    # 每个阶段修了几轮才过闸门。**不是可以藏起来的事。**
    repair_log: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- 存取 -------------------------------------------------------------------

_SESSION_FIELDS = set(Session.__dataclass_fields__)


def _persist(sess: Session) -> Session:
    sess.updated_at = _now()
    data = {k: v for k, v in sess.to_dict().items()
            if k not in ("id", "material_text", "stage")}
    store.save_guided(sess.id, sess.material_text, sess.stage, data)
    return sess


def load(session_id: str) -> Session:
    raw = store.load_guided(session_id)
    if raw is None:
        raise GuidedError(f"没有这个引导会话（{session_id}）。它可能已经完成或被删掉了。",
                          kind="not_found")
    return Session(**{k: v for k, v in raw.items() if k in _SESSION_FIELDS})


def listing(limit: int = 10) -> list[dict]:
    return store.list_guided(limit)


def discard(session_id: str) -> dict:
    return {"removed": store.delete_guided(session_id)}


def start(material_text: str, model: str = "") -> Session:
    """阶段 0：开一个会话。还没有检索，也还没有花任何 token。"""
    text = (material_text or "").strip()
    if not text:
        raise GuidedError("没有说要选什么物料。", kind="empty")
    sess = Session(id=store.new_guided_id(), material_text=text[:80],
                   model=model, created_at=_now())
    return _persist(sess)


# --- 阶段 1：依据检索 + 服务端取证 ------------------------------------------

def research(session_id: str, provider, model: str, budget=None,
             provider_search=None) -> Session:
    """阶段 1：检索 → 让 AI 挑候选依据 → **服务端逐条取证**。

    取证是这一步的要点，也是整个改动的地基：AI 声称"依据是 GB/T 1095 表 1"，
    服务器自己去抓它引用的页面，核对正文里是否真的出现了这个标准号。
    对不上的候选标 `unverified`，**用户选不了**。
    """
    from .ai import tasks

    sess = load(session_id)
    if model:
        sess.model = model

    outcome = search_providers.search(
        f"{sess.material_text} 选型 计算 设计步骤 标准",
        root=data_dir(), client=search_providers.current_client(),
        provider_search=provider_search)
    sess.search = outcome.to_dict()
    sess.known_urls = [h.url for h in outcome.hits]
    if not outcome.hits:
        raise GuidedError(
            "检索没有返回任何可用结果，这一步没有依据可挑。"
            "可以在设置页绑定一个搜索服务、换个说法再试，或者直接自己填写依据。",
            kind="no_search_results", stage=STAGE_BASIS)

    proposed = tasks.research_basis(
        sess.material_text, [h.to_dict() for h in outcome.hits],
        provider, sess.model or model, budget=budget)
    sess.repair_log["basis"] = proposed["repair_log"]
    sess.material_id = proposed["material_id"]
    sess.name_zh = proposed["name_zh"]

    # 取证。同一个 URL 在一次检索里只抓一次。
    fetched: dict[str, evidence.Doc] = {}
    cands: list[dict] = []
    for raw in proposed["candidates"]:
        urls = [str(u) for u in (raw.get("urls") or [])]
        ev, dropped = evidence.verify_basis(
            str(raw.get("claim") or ""), urls, sess.known_urls, fetched=fetched)
        cands.append({
            "id": str(raw.get("id") or f"b{len(cands) + 1}"),
            "claim": str(raw.get("claim") or ""),
            "standard": str(raw.get("standard") or ""),
            "why": str(raw.get("why") or ""),
            "outline": [str(s) for s in (raw.get("outline") or [])],
            "urls": urls,
            "dropped_urls": dropped,
            "evidence": ev.to_dict(),
        })
    sess.candidates = cands
    sess.excerpts = _excerpts_from(fetched)
    sess.stage = STAGE_BASIS
    return _persist(sess)


def _excerpts_from(fetched: dict) -> list[dict]:
    """把抓回来的正文裁成能进上下文的摘录。

    只留取到正文的那些，每篇截断，总数也限。**整页不留**——
    既是上下文预算问题，也是版权问题（见 DATA_NOTICE.md）。
    """
    out: list[dict] = []
    for doc in fetched.values():
        if not doc.ok:
            continue
        out.append({"url": doc.final_url or doc.url, "title": doc.title,
                    "excerpt": doc.text[:EXCERPT_CHARS],
                    "fingerprint": doc.fingerprint})
        if len(out) >= MAX_EXCERPTS:
            break
    return out


def choose_basis(session_id: str, basis_id: str = "",
                 custom: dict | None = None) -> Session:
    """用户拍板选一条依据（或自己填一条）。**这一步之前什么都不许往下走。**

    取证没过的候选（`unverified`）选不了：抓到了正文却没有它声称的标准号，
    那正是最该拦的情况。自己填的依据一律标 `self_declared` ——
    引擎不去核用户自己的话，但会如实记下这是用户填的。
    """
    sess = load(session_id)
    if custom:
        claim = str(custom.get("claim") or "").strip()
        if len(claim) < 4:
            raise GuidedError("自己填写的依据太短 —— 请写出可核对的标准号或手册章节。",
                              kind="bad_basis", stage=STAGE_BASIS)
        sess.basis = {
            "id": "custom", "claim": claim,
            "standard": str(custom.get("standard") or ""),
            "outline": [str(s) for s in (custom.get("outline") or [])],
            "urls": [str(u) for u in (custom.get("urls") or [])],
            "status": "self_declared",
            "confirmed_at": _now(), "confirmed_by": "user",
        }
    else:
        if not sess.candidates:
            raise GuidedError("还没有检索过依据，先走阶段 1。",
                              kind="out_of_order", stage=STAGE_BASIS)
        hit = next((c for c in sess.candidates if c["id"] == basis_id), None)
        if hit is None:
            raise GuidedError(f"候选依据里没有 {basis_id!r}。",
                              kind="unknown_basis", stage=STAGE_BASIS)
        ev = hit.get("evidence") or {}
        if not ev.get("usable"):
            raise GuidedError(
                f"这条依据没通过取证（{ev.get('status')}）：服务器抓到了它引用的页面，"
                "但正文里没有出现它声称的标准号。**不能拿它作为依据往下走。**"
                "请换一条，或者自己填写依据。",
                kind="unverified_basis", stage=STAGE_BASIS)
        sess.basis = {
            "id": hit["id"], "claim": hit["claim"], "standard": hit["standard"],
            "outline": hit["outline"],
            # 只留取证真的命中的那些 URL —— 抓不到或对不上的不该写进依据
            "urls": [h.get("url") for h in (ev.get("hits") or []) if h.get("url")]
                    or hit["urls"],
            "status": ev.get("status") or "",
            "domains": ev.get("domains") or [],
            "confirmed_at": _now(), "confirmed_by": "user",
        }
    sess.stage = STAGE_INPUTS
    # 换依据要作废后面所有已确认的东西 —— 公式是对着旧依据核的，不能留着
    sess.inputs, sess.inputs_confirmed_at = [], ""
    sess.steps, sess.formulas_confirmed_at = [], ""
    return _persist(sess)


# --- 阶段 2：参数引导 -------------------------------------------------------

def propose_inputs(session_id: str, provider, model: str = "",
                   budget=None) -> Session:
    """阶段 2：按已确认的依据列出要问用户的参数。"""
    from .ai import tasks

    sess = load(session_id)
    _require_basis(sess)
    out = tasks.guide_inputs(sess.material_text, sess.basis, sess.excerpts,
                             provider, model or sess.model, budget=budget)
    sess.inputs = out["inputs"]
    sess.inputs_confirmed_at = ""
    sess.repair_log["inputs"] = out["repair_log"]
    if out.get("notes"):
        sess.notes = [n for n in sess.notes if n] + [str(out["notes"])]
    sess.stage = STAGE_INPUTS
    return _persist(sess)


def confirm_inputs(session_id: str, inputs: list | None = None) -> Session:
    """用户确认参数清单（可以改过）。改动照原样收下——这是他的依据，不是我们的。"""
    sess = load(session_id)
    _require_basis(sess)
    if inputs is not None:
        if not isinstance(inputs, list) or not inputs:
            raise GuidedError("参数清单不能为空。", kind="bad_inputs",
                              stage=STAGE_INPUTS)
        sess.inputs = inputs
    if not sess.inputs:
        raise GuidedError("还没有参数清单，先让引导给出一份。",
                          kind="out_of_order", stage=STAGE_INPUTS)
    sess.inputs_confirmed_at = _now()
    sess.stage = STAGE_STEPS
    return _persist(sess)


# --- 阶段 3/4：整套步骤，一次性确认 ------------------------------------------

def propose_steps(session_id: str, provider, model: str = "",
                  budget=None) -> Session:
    """阶段 3/4：给出整套计算与校核步骤。**给完还不能跑**，要等用户确认。"""
    from .ai import tasks

    sess = load(session_id)
    _require_basis(sess)
    if not sess.inputs_confirmed_at:
        raise GuidedError("参数清单还没确认，先走阶段 2。",
                          kind="out_of_order", stage=STAGE_INPUTS)

    out = tasks.guide_steps(sess.material_text, sess.basis, sess.inputs,
                            sess.excerpts, provider, model or sess.model,
                            budget=budget)
    sess.steps = out["steps"]
    sess.result = out["result"]
    sess.procure = out["procure"]
    sess.missing_inputs = out["missing_inputs"]
    sess.confidence_note = out["confidence_note"]
    sess.notes = [n for n in sess.notes if n] + list(out["notes"])
    sess.repair_log["steps"] = out["repair_log"]
    # 重新出了一套步骤，之前那次确认作废 —— 用户确认的是"那一套"，不是"任意一套"
    sess.formulas_confirmed_at = ""
    sess.stage = STAGE_STEPS
    return _persist(sess)


def confirm_formulas(session_id: str, confirmed: bool = False) -> Session:
    """用户一次性过目并确认整套公式。**没有这一步，引擎不执行。**

    确认的不是"AI 很靠谱"，而是"我已对照我自己选的那条依据核对过这些公式"。
    这句话会原样进 trace 警告与导出报告——它是这份结果唯一的人工背书。
    """
    sess = load(session_id)
    _require_basis(sess)
    if not sess.steps:
        raise GuidedError("还没有步骤可确认，先走阶段 3。",
                          kind="out_of_order", stage=STAGE_STEPS)
    if not confirmed:
        raise GuidedError(
            "需要你勾选「我已对照依据核对以上全部公式」才能继续。"
            "这一步不是走过场：引擎只做算术，公式对不对只有你能判断。",
            kind="not_confirmed", stage=STAGE_STEPS)
    sess.formulas_confirmed_at = _now()
    sess.stage = STAGE_READY
    # 组装一遍，确保它真的能被引擎解析 —— 宁可现在报错，不要等用户填完表才炸
    spec_dict(sess)
    return _persist(sess)


def _require_basis(sess: Session) -> None:
    if not sess.basis or not sess.basis.get("confirmed_at"):
        raise GuidedError(
            "依据还没确认。阶段 1 必须先定下依据——后面的公式都要对着它核对。",
            kind="out_of_order", stage=STAGE_BASIS)


# --- 兜底档：AI 参考草案 ----------------------------------------------------

def ai_draft(session_id: str, provider, model: str = "", budget=None) -> Session:
    """让 AI 把整个选型直接做完，**包括出数**。

    这一档没有合规闸门，所以它产出的东西**不是选型结果**：

    - 不写进 `steps` / `result`，因此 `spec_dict()` 看不见它
    - 不改变 `stage`，因此 `can_run` 不会因为它变成 True
    - 存不成物料，也进不了选型报告

    它存在的唯一理由是：前面几道闸门都过不去时，**总得有东西交给用户**。
    代价（每个数都没有出处）在界面与文本头部都写死了。
    """
    from .ai import tasks

    sess = load(session_id)
    out = tasks.ai_draft(sess.material_text, sess.basis, sess.inputs,
                         sess.excerpts, provider, model or sess.model,
                         budget=budget)
    sess.ai_draft = out
    return _persist(sess)


# --- 组装成一份普通的工作流规格 ---------------------------------------------

def spec_dict(sess: Session) -> dict:
    """把已确认的部分组装成一份**普通的**工作流规格。

    重点在"普通"：组装出来的东西走同一个 `spec.parse()`、同一个 `runner.run()`，
    阶段 5、6 与内置物料没有任何区别。引导式没有给引擎加任何新的执行机制。
    """
    from mds import spec as mds_spec

    if not sess.formulas_confirmed_at:
        raise GuidedError(
            "整套公式还没有经你确认，引擎不会执行。",
            kind="not_confirmed", stage=STAGE_STEPS)

    data = {
        "material": _unique_id(sess.material_id or "user_material"),
        "name_zh": sess.name_zh or sess.material_text,
        "standard": str(sess.basis.get("standard") or ""),
        "schema_version": 1,
        "provenance": "user_guided",
        "generated_by": sess.model,
        "basis": dict(sess.basis),
        "notes": _notes(sess),
        "inputs": [_clean_input(i) for i in sess.inputs],
        "steps": sess.steps,
        "result": sess.result,
        "procure": sess.procure,
    }
    try:
        mds_spec.parse(data)
    except Exception as exc:
        raise GuidedError(
            f"组装出来的规格没通过引擎的静态校验：{exc}　"
            "这通常意味着某个公式引用了不存在的变量。请回到阶段 3 重新生成。",
            kind="parse_failed", stage=STAGE_STEPS) from exc
    return data


def _notes(sess: Session) -> list:
    head = (
        f"本流程由引导式选型组装：依据是你确认的「{sess.basis.get('claim', '')}」"
        f"（取证：{sess.basis.get('status', '未取证')}），"
        f"整套公式由你于 {sess.formulas_confirmed_at} 过目确认。"
        "引擎只做算术，所有需要查手册的量都是输入项，由你自己填。")
    tail = [str(n) for n in sess.notes if str(n).strip()]
    if sess.confidence_note:
        tail.append(f"最不确定的一处：{sess.confidence_note}")
    return [head, *tail]


# from_handbook / round 是引导过程里用的元数据，不属于工作流规格。
# 留着会让 spec.parse() 报"未知字段"——那个校验是对的，不该为此放宽。
_GUIDE_ONLY = {"from_handbook", "round"}


def _clean_input(raw: dict) -> dict:
    out = {k: v for k, v in raw.items() if k not in _GUIDE_ONLY}
    # 去哪查这件事必须留下来 —— 它是用户能把这个数填对的唯一线索
    if raw.get("from_handbook") and out.get("hint"):
        out["hint"] = f"【需查手册】{out['hint']}"
    return out


def _unique_id(base: str) -> str:
    """不许与随包物料重名。重名会让人以为自己改的是随包那一份。"""
    from mds import spec as mds_spec
    from mds.knowledge import SKILL_ROOT

    builtin = set(mds_spec.available(SKILL_ROOT))
    mid = base if base else "user_material"
    return f"{mid}_user" if mid in builtin else mid


# --- 落盘 -------------------------------------------------------------------

def save(session_id: str) -> dict:
    """存进用户目录，下次离线也能选。

    `drafts.save()` 会用**同一份判据**再独立复核一遍——不信任调用方，
    也不信任这份规格在会话里待过的那二十分钟。
    """
    sess = load(session_id)
    out = drafts.save(spec_dict(sess))
    sess.stage = STAGE_SAVED
    _persist(sess)
    return {**out, "session": sess.id}


# --- 给接口用的视图 ---------------------------------------------------------

def view(sess: Session) -> dict:
    """返回给前端的会话视图。

    刻意把"现在能做什么"算在服务端（`can_*`）：前端据此置灰按钮，
    但**后端仍然会拦**——前端只是提示，闸门在这边。
    """
    d = sess.to_dict()
    d["can_choose_basis"] = bool(sess.candidates)
    d["can_propose_inputs"] = bool(sess.basis.get("confirmed_at"))
    d["can_propose_steps"] = bool(sess.inputs_confirmed_at)
    d["can_confirm_formulas"] = bool(sess.steps)
    d["can_run"] = bool(sess.formulas_confirmed_at)
    d["material"] = _unique_id(sess.material_id) if sess.material_id else ""
    d["usable_candidates"] = sum(
        1 for c in sess.candidates if (c.get("evidence") or {}).get("usable"))
    # 有草案**不等于**能跑。can_run 只看公式确认过没有，和它无关。
    d["has_ai_draft"] = bool(sess.ai_draft)
    return d
