"""mds —— 机械设计物料选型引擎（确定性内核）

Claude skill 与桌面软件共用同一套引擎代码，保证同一份公式只有一处实现。

    from mds import load_spec, run, Knowledge

    spec = load_spec("synchronous_belt")
    trace = run(spec, {"P": 5.5, "n1": 1450, "belt_type": "H", ...})

设计约束：
- 本包内不包含任何 AI 调用。AI 只负责解析意图、建议参数、解释结果，
  其输出必须经过与用户手输相同的校验通道才能进入计算。
- 数据缺失一律抛 DataMissing，绝不外推、绝不猜。
"""

from .errors import (
    DataMissing,
    ExprError,
    InputError,
    MDSError,
    NeedsChoice,
    NoSolution,
    SpecError,
)
from .knowledge import Knowledge, Table, lowest_confidence
from .runner import SelectionTrace, StepTrace, enum_choices, run, validate_inputs
from .spec import WorkflowSpec, available, load as load_spec, parse as parse_spec

__version__ = "0.1.0"

__all__ = [
    "DataMissing", "ExprError", "InputError", "MDSError", "NeedsChoice", "NoSolution",
    "SpecError",
    "Knowledge", "Table", "lowest_confidence",
    "SelectionTrace", "StepTrace", "enum_choices", "run", "validate_inputs",
    "WorkflowSpec", "available", "load_spec", "parse_spec",
    "__version__",
]
