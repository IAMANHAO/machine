"""mds.errors —— 引擎异常体系

设计原则：数据缺失必须是一个显式异常，绝不允许退化成一个"看起来合理的数字"。
"""

from __future__ import annotations

from typing import Any


class MDSError(Exception):
    """引擎异常基类。"""

    def as_dict(self) -> dict:
        return {"error": type(self).__name__, "message": str(self)}


class SpecError(MDSError):
    """工作流 YAML 规格本身有问题（缺字段、未知 kind、引用了不存在的步骤等）。"""


class ExprError(MDSError):
    """表达式非法或含被禁止的语法。"""


class InputError(MDSError):
    """用户输入缺失或超出定义域。"""

    def __init__(self, message: str, *, param: str | None = None,
                 value: Any = None, domain: dict | None = None):
        super().__init__(message)
        self.param = param
        self.value = value
        self.domain = domain

    def as_dict(self) -> dict:
        d = super().as_dict()
        d.update({"param": self.param, "value": self.value, "domain": self.domain})
        return d


class DataMissing(MDSError):
    """缓存数据缺失，或所需工况点超出数据表覆盖范围。

    这是本引擎最重要的一个异常：宁可停下来报缺口，也不外推、不猜。
    `gap` 用于把精确缺口告诉用户和知识库页面。
    """

    def __init__(self, message: str, *, table: str | None = None,
                 path: str | None = None, coords: dict | None = None,
                 available: Any = None, gap: str | None = None):
        super().__init__(message)
        self.table = table
        self.path = path
        self.coords = coords
        self.available = available
        self.gap = gap

    def as_dict(self) -> dict:
        d = super().as_dict()
        d.update({
            "table": self.table,
            "path": self.path,
            "coords": self.coords,
            "available": self.available,
            "gap": self.gap,
        })
        return d


class NoSolution(MDSError):
    """在现有标准系列/规格范围内无解——这是一个选型结论，不是数据缺失。

    两者对用户的指引完全相反：数据缺失要去补知识库，无解要去改设计
    （换更大节距、加大规格、降功率）。混为一谈会把人引到错误的方向。
    """

    def __init__(self, message: str, *, table: str | None = None,
                 what: str | None = None, required: Any = None,
                 limit: Any = None, available: Any = None,
                 remedy: str | None = None):
        super().__init__(message)
        self.table = table
        self.what = what
        self.required = required
        self.limit = limit
        self.available = available
        self.remedy = remedy

    def as_dict(self) -> dict:
        d = super().as_dict()
        d.update({"table": self.table, "what": self.what,
                  "required": self.required, "limit": self.limit,
                  "available": self.available, "remedy": self.remedy})
        return d


class NeedsChoice(MDSError):
    """某个 select 步骤需要人或 AI 做决策，引擎不替用户决定。"""

    def __init__(self, message: str, *, step: str, candidates: list,
                 recommended: Any = None, reason: str | None = None,
                 given: Any = None):
        super().__init__(message)
        self.step = step
        self.candidates = candidates
        self.recommended = recommended
        self.reason = reason
        # 用户原本给的那个值（叫法对不上时才有）。留着它，界面才能问
        # "我说的是哪一个" —— 没有它就只能让用户从头再选一遍。
        self.given = given

    def as_dict(self) -> dict:
        d = super().as_dict()
        d.update({
            "step": self.step,
            "candidates": self.candidates,
            "recommended": self.recommended,
            "reason": self.reason,
            "given": self.given,
        })
        return d
