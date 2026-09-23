"""锁住 M0 的出口标准：软件化改造不能弄坏 skill 原有的命令行。

这四条命令记在 .workbuddy/memory/MEMORY.md 里，是 skill 的既有契约。
在装有 PyYAML 的环境下它们曾经全挂（缓存文件是两段式 YAML，safe_load 会抛
ComposerError），这组测试确保不再退化。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"命令失败：{' '.join(args)}\n{proc.stderr}"
    return proc.stdout


def test_cache_manager_report():
    out = json.loads(_run("scripts/cache_manager.py", "report", "synchronous_belt"))
    assert out["single_total"] == 6
    assert "condition_factors.yaml" in out["materials"]["synchronous_belt"]


def test_standard_round():
    out = json.loads(_run("scripts/standard_round.py", "612",
                          "belt_length_series", "--belt-type", "L"))
    assert out["result"] == 685.8
    assert out["mode"] == "nearest_above"


def test_calc_engine_check():
    out = json.loads(_run("scripts/calc_engine.py", "check", "--name", "带宽",
                          "--value", "60", "--limit", "76.2", "--unit", "mm"))
    assert out["pass"] is True


def test_procure_link_build():
    out = _run("scripts/procure_link.py", "build", "-m", "lubricant",
               "--set", "kind=导轨油", "--set", "vg=68", "--format", "md")
    assert "VG68" in out and "s.taobao.com" in out


@pytest.mark.parametrize("material", ["synchronous_belt", "lubricant"])
def test_every_cache_file_readable_through_legacy_layer(material):
    """_yaml.load 必须能读通每一张缓存表（两段式合并）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from _yaml import load  # noqa: PLC0415

    for path in sorted((ROOT / "knowledge" / "cache" / material).glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            data = load(fh)
        assert isinstance(data, dict), f"{path.name} 未能解析成 dict"
        assert "verification_status" in data, f"{path.name} 缺 frontmatter"


def test_mds_cli_end_to_end():
    """M1 出口：纯命令行离线跑完 0~6 阶段。"""
    out = json.loads(_run(
        "-m", "mds", "run", "synchronous_belt", "--format", "json",
        "-s", "P=5.5", "-s", "n1=1450", "-s", "i=2", "-s", "a0=400",
        "-s", "prime_mover=ac_motor_normal", "-s", "work_machine=medium_uniform",
        "-s", "hours_per_day=h_le_10", "-s", "belt_type=H"))
    assert out["status"] == "ok"
    assert out["outputs"]["bs"] == 76.2
    assert out["procure"]["links"], "阶段 6 必须产出采购链接"
    assert all(c["detail"]["passed"] for c in out["checks"])
