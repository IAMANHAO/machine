"""
yaml_mini.py —— 纯标准库最小化 YAML 读写工具
=========================================

覆盖本 skill 的所有 YAML 用法：

  ---
  key: value              整型/浮点/字符串/bool/null
  key:                    嵌套 dict / list
  nested:
    k1: 1
    k2: 2
  list_example:
    - 1
    - 2
    - key1: a
      key2: b
  inline_list: [1, 2, 3]
  ---

不支持：
  - block scalar（>、|）
  - merge key / anchor / flow 风格 map（仅支持 list inline）
  - 多行字符串

如果有 PyYAML，请优先使用 PyYAML；本模块作为 fallback。
"""

from __future__ import annotations

import re


# ---------------- 加载 ----------------

def load(text):
    """解析 YAML 文本，返回 dict / list / scalar。

    支持 `---` 之间的 frontmatter，会将其合并到顶层 dict。
    """
    text = text.replace("\r\n", "\n")
    m = re.match(r"\A---\s*\n(.+?)\n---\s*\n", text, re.DOTALL)
    front = {}
    body_text = text
    if m:
        front_text = m.group(1)
        body_text = text[m.end():]
        front_prep = _preprocess(front_text)
        if front_prep:
            front, _ = _parse_block(front_prep, 0, _indent(front_prep[0]))
        body_prep = _preprocess(body_text)
        if not body_prep:
            return front
        body, _ = _parse_block(body_prep, 0, _indent(body_prep[0]))
    else:
        body_prep = _preprocess(text)
        if not body_prep:
            return None
        body, _ = _parse_block(body_prep, 0, _indent(body_prep[0]))
        front = {}

    if isinstance(body, dict) and isinstance(front, dict):
        body.update(front)
        return body
    if isinstance(body, dict):
        return body
    if isinstance(front, dict):
        return front
    return body


def _preprocess(text):
    text = text.replace("\r\n", "\n")
    out = []
    for line in text.split("\n"):
        # 行内注释：' #' 后面的视为注释（行首 '#' 单独处理）
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def _indent(line):
    return len(line) - len(line.lstrip())


def _parse_block(lines, pos, parent_indent):
    """在 pos 处解析一个容器（dict 或 list）。"""
    if pos >= len(lines):
        return None, pos
    ind = _indent(lines[pos])
    content = lines[pos].lstrip()
    if content.startswith("- "):
        return _parse_list(lines, pos, ind)
    return _parse_dict(lines, pos, ind)


def _parse_dict(lines, pos, indent):
    result = {}
    n = len(lines)
    while pos < n:
        line = lines[pos]
        ind = _indent(line)
        if ind < indent:
            return result, pos
        if ind > indent:
            return result, pos
        content = line.lstrip()
        if content.startswith("- "):
            return result, pos
        if ":" not in content:
            raise ValueError(f"非法行：{line!r}")
        key, _, val = content.partition(":")
        key = key.strip()
        val = val.strip()
        pos += 1
        if val == "":
            # 可能是嵌套 / 也可能是空字符串
            if pos < n and _indent(lines[pos]) > indent:
                sub, pos = _parse_block(lines, pos, _indent(lines[pos]))
                result[key] = sub
            else:
                result[key] = None
        elif val.startswith("[") and val.endswith("]"):
            # flow style list
            result[key] = _parse_inline_list(val)
        else:
            result[key] = _scalar(val)
    return result, pos


def _parse_list(lines, pos, indent):
    result = []
    n = len(lines)
    while pos < n:
        line = lines[pos]
        ind = _indent(line)
        if ind < indent:
            return result, pos
        if ind > indent:
            return result, pos
        content = line.lstrip()
        if not content.startswith("- "):
            return result, pos
        after = content[2:]
        if ":" in after:
            # dict 项
            item = {}
            key, _, val = after.partition(":")
            key = key.strip()
            val = val.strip()
            pos += 1
            if val == "":
                if pos < n and _indent(lines[pos]) > indent + 2:
                    sub, pos = _parse_block(lines, pos, _indent(lines[pos]))
                    item[key] = sub
                else:
                    item[key] = None
            else:
                item[key] = _scalar(val)

            # 后续同缩进的兄弟 key
            while pos < n:
                next_ind = _indent(lines[pos])
                if next_ind != indent + 2:
                    break
                next_content = lines[pos].lstrip()
                if next_content.startswith("- "):
                    break
                if ":" not in next_content:
                    break
                k2, _, v2 = next_content.partition(":")
                k2 = k2.strip()
                v2 = v2.strip()
                pos += 1
                if v2 == "":
                    if pos < n and _indent(lines[pos]) > next_ind:
                        sub, pos = _parse_block(lines, pos, _indent(lines[pos]))
                        item[k2] = sub
                    else:
                        item[k2] = None
                else:
                    item[k2] = _scalar(v2)
            result.append(item)
        else:
            # 标量项
            after = after.strip()
            result.append(_scalar(after) if after else None)
            pos += 1
    return result, pos


def _parse_inline_list(s):
    s = s.strip()[1:-1]
    if not s.strip():
        return []
    out = []
    for part in _split_top_level(s, ","):
        out.append(_scalar(part.strip()))
    return out


def _split_top_level(s, sep):
    parts, buf, depth = [], [], 0
    for ch in s:
        if ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _scalar(s):
    s = s.strip()
    if not s or s in ("null", "~"):
        return None
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    # 整型
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    # 浮点
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+[eE][-+]?\d+|\d+[eE][-+]?\d+\.?\d*)", s):
        return float(s)
    # 带引号字符串
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


# ---------------- 序列化 ----------------

def dump(obj, indent_step=2):
    """序列化 Python 对象为 YAML 文本。"""
    lines = []
    _dump(obj, lines, 0, indent_step)
    return "\n".join(lines) + ("\n" if lines else "")


def _dump(obj, lines, indent, step):
    pad = " " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                if not v:
                    lines.append(f"{pad}{k}: {{}}")
                else:
                    lines.append(f"{pad}{k}:")
                    _dump(v, lines, indent + step, step)
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{pad}{k}: []")
                else:
                    lines.append(f"{pad}{k}:")
                    _dump(v, lines, indent + step, step)
            elif v is None:
                lines.append(f"{pad}{k}: null")
            elif isinstance(v, bool):
                lines.append(f"{pad}{k}: {'true' if v else 'false'}")
            else:
                lines.append(f"{pad}{k}: {_fmt_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                lines.append(f"{pad}-")
                _dump(item, lines, indent + step, step)
            elif isinstance(item, list) and item:
                lines.append(f"{pad}-")
                _dump(item, lines, indent + step, step)
            elif item is None:
                lines.append(f"{pad}- null")
            elif isinstance(item, bool):
                lines.append(f"{pad}- {'true' if item else 'false'}")
            else:
                lines.append(f"{pad}- {_fmt_scalar(item)}")
    else:
        lines.append(f"{pad}{_fmt_scalar(obj)}")


def _fmt_scalar(v):
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # 需要引号吗
    if any(ch in s for ch in [":", "#", "&", "*", "%", "@", "!", "{", "}", "[", "]", ","]):
        return f'"{s}"'
    if s != s.strip() or s == "":
        return f'"{s}"'
    return s
