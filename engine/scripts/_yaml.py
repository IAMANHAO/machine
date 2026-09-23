"""
_yaml.py —— 统一 YAML 读写入口

优先使用 PyYAML；如果缺失，回退到项目内的 yaml_mini（仅覆盖本 skill 的 YAML 子集）。
所有脚本统一从这里 import，避免到处 try/except。

用法：

    from _yaml import load, dump, HAS_PYYAML

    data = load(text_or_stream)
    dump(data, file_handle)        # 或 dump(data) 返回 str
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


try:
    import yaml as _lib  # type: ignore
    HAS_PYYAML = True
except ImportError:
    import yaml_mini as _lib  # type: ignore
    HAS_PYYAML = False


def load(text_or_stream):
    """读取 YAML。text_or_stream 可以是字符串或 file-like。

    缓存文件是"frontmatter 文档 + 正文文档"两段式（中间一行 ---）。
    PyYAML 的 safe_load 遇到多文档会抛 ComposerError，必须走 safe_load_all
    再把各文档合并到顶层 dict —— 这与 yaml_mini 的行为一致。
    """
    if HAS_PYYAML:
        docs = [d for d in _lib.safe_load_all(text_or_stream) if d is not None]
        if not docs:
            return None
        if len(docs) == 1:
            return docs[0]
        if all(isinstance(d, dict) for d in docs):
            merged = {}
            for d in docs:
                merged.update(d)
            return merged
        return docs
    if hasattr(text_or_stream, "read"):
        return _lib.load(text_or_stream.read())
    if isinstance(text_or_stream, (bytes, bytearray)):
        text_or_stream = text_or_stream.decode("utf-8")
    return _lib.load(text_or_stream)


def dump(obj, stream=None, allow_unicode: bool = True, sort_keys: bool = False):
    """序列化 YAML。stream=None 返回字符串。

    mini 模式：忽略 allow_unicode/sort_keys（始终 UTF-8、按插入序）。
    """
    if HAS_PYYAML:
        if stream is None:
            return _lib.safe_dump(obj, allow_unicode=allow_unicode, sort_keys=sort_keys)
        return _lib.safe_dump(obj, stream, allow_unicode=allow_unicode, sort_keys=sort_keys)
    text = _lib.dump(obj)
    if stream is None:
        return text
    stream.write(text)
    return None
