"""TOON (Token-Oriented Object Notation) encoder/decoder for the AXI envelope."""

from __future__ import annotations

import re

_TOON_BARE_KEY = re.compile(r"^[A-Za-z_][\w.\-]*$")
_TOON_NEEDS_QUOTE_RE = re.compile(r"^(true|false|null)$|^-?\d")
_TOON_VALUE_STRUCTURAL = set('":{}#\n\r\t\\')
_TOON_CELL_STRUCTURAL = set('":{}#,\n\r\t\\')


def _toon_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _toon_quote_value(s: str) -> str:
    if s == "":
        return '""'
    if _TOON_NEEDS_QUOTE_RE.match(s):
        return f'"{_toon_escape(s)}"'
    if any(c in _TOON_VALUE_STRUCTURAL for c in s):
        return f'"{_toon_escape(s)}"'
    return s


def _toon_quote_cell(s: str) -> str:
    if s == "":
        return '""'
    if _TOON_NEEDS_QUOTE_RE.match(s):
        return f'"{_toon_escape(s)}"'
    if any(c in _TOON_CELL_STRUCTURAL for c in s):
        return f'"{_toon_escape(s)}"'
    return s


def _toon_is_uniform_object_list(items):
    if not items or not all(isinstance(x, dict) for x in items):
        return False
    keys = list(items[0].keys())
    if not keys or not all(_TOON_BARE_KEY.match(k) for k in keys):
        return False
    for x in items:
        if list(x.keys()) != keys:
            return False
        for v in x.values():
            if not (isinstance(v, (str, int, float, bool)) or v is None):
                return False
    return True


def _toon_primitive(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return _toon_quote_value(str(v))


def _toon_encode(obj, indent=2):
    lines = []

    def key_str(k):
        s = str(k)
        return s if _TOON_BARE_KEY.match(s) else _toon_quote_value(s)

    def emit(value, depth):
        prefix = " " * (indent * depth)
        if isinstance(value, dict):
            if not value:
                return
            for k, v in value.items():
                kk = key_str(k)
                if isinstance(v, dict):
                    if not v:
                        lines.append(f"{prefix}{kk}: {{}}")
                    else:
                        lines.append(f"{prefix}{kk}:")
                        emit(v, depth + 1)
                elif isinstance(v, list):
                    if not v:
                        lines.append(f"{prefix}{kk}: []")
                    elif _toon_is_uniform_object_list(v):
                        keys = list(v[0].keys())
                        header = ",".join(keys)
                        lines.append(f"{prefix}{kk}[{len(v)},]{{{header}}}:")
                        row_prefix = prefix + " " * indent
                        for row in v:
                            cells = []
                            for k in keys:
                                rv = row[k]
                                if rv is None:
                                    cells.append("null")
                                elif isinstance(rv, bool):
                                    cells.append("true" if rv else "false")
                                elif isinstance(rv, (int, float)):
                                    cells.append(str(rv))
                                else:
                                    cells.append(_toon_quote_cell(str(rv)))
                            lines.append(row_prefix + ",".join(cells))
                    elif all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                        cells = []
                        for x in v:
                            if x is None:
                                cells.append("null")
                            elif isinstance(x, bool):
                                cells.append("true" if x else "false")
                            elif isinstance(x, (int, float)):
                                cells.append(str(x))
                            else:
                                cells.append(_toon_quote_cell(str(x)))
                        lines.append(f"{prefix}{kk}[{len(v)}]: {','.join(cells)}")
                    else:
                        lines.append(f"{prefix}{kk}[{len(v)}]:")
                        for item in v:
                            if isinstance(item, dict):
                                lines.append(f"{prefix}{' ' * indent}- ")
                                inner = prefix + " " * indent + "  "
                                for kk2, vv in item.items():
                                    kk3 = key_str(kk2)
                                    if isinstance(vv, (str, int, float, bool)) or vv is None:
                                        lines[-1] = lines[-1] + f"{kk3}: {_toon_primitive(vv)} "
                                    else:
                                        lines.append(f"{inner}{kk3}:")
                                        emit(vv, depth + 2)
                            else:
                                lines.append(f"{prefix}{' ' * indent}- {_toon_primitive(item)}")
                else:
                    lines.append(f"{prefix}{kk}: {_toon_primitive(v)}")
        else:
            lines.append(prefix + _toon_primitive(value))

    emit(obj, 0)
    return "\n".join(lines) + "\n"


def toon_dump(obj):
    """Serialize `obj` for the AXI on-the-wire path as TOON."""
    return _toon_encode(obj)


_TOON_KEY_ARRAY = re.compile(r"^(.+?)\[(\d+),?\](?:\{([^}]*)\})?$")


def toon_load(text):
    """Decode a TOON string produced by :func:`toon_dump`."""
    if not text.strip():
        return None
    raw_lines = text.split("\n")
    lines = [ln for ln in raw_lines if ln.strip()]
    pos = [0]

    def line_indent(line):
        return len(line) - len(line.lstrip(" "))

    def split_csv(s):
        out = []
        i = 0
        n = len(s)
        while i < n:
            if s[i].isspace():
                i += 1
                continue
            if s[i] == ",":
                i += 1
                continue
            if s[i] == '"':
                j = i + 1
                buf = []
                while j < n:
                    if s[j] == "\\" and j + 1 < n:
                        esc = s[j + 1]
                        buf.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}.get(esc, esc))
                        j += 2
                    elif s[j] == '"':
                        break
                    else:
                        buf.append(s[j])
                        j += 1
                out.append("".join(buf))
                i = j + 1
            else:
                j = i
                while j < n and s[j] != ",":
                    j += 1
                out.append(s[i:j].strip())
                i = j
        return out

    def parse_primitive(s):
        s = s.strip()
        if s == "" or s == "null":
            return None
        if s == "true":
            return True
        if s == "false":
            return False
        if s.startswith('"') and s.endswith('"'):
            inner = s[1:-1]
            i = 0
            res = []
            while i < len(inner):
                if inner[i] == "\\" and i + 1 < len(inner):
                    c = inner[i + 1]
                    res.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(c, c))
                    i += 2
                else:
                    res.append(inner[i])
                    i += 1
            return "".join(res)
        if s == "{}":
            return {}
        if s == "[]":
            return []
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    def parse_inline_array(rest):
        return [parse_primitive(c) for c in split_csv(rest)]

    def parse_key(raw_key):
        m = _TOON_KEY_ARRAY.match(raw_key)
        if not m:
            return raw_key, None, None
        key, length_str, fields_str = m.group(1), m.group(2), m.group(3)
        length = int(length_str) if length_str else None
        fields = [f.strip() for f in fields_str.split(",")] if fields_str else None
        return key, length, fields

    def read_block(depth):
        out = {}
        while pos[0] < len(lines):
            line = lines[pos[0]]
            ind = line_indent(line)
            if ind < depth:
                break
            if ind > depth:
                raise ValueError(f"unexpected indent at line {pos[0] + 1}: {line!r}")
            content = line.strip()
            if ":" not in content:
                raise ValueError(f"expected ':' at line {pos[0] + 1}: {line!r}")
            colon = content.index(":")
            raw_key = content[:colon].strip()
            if raw_key.startswith('"') and raw_key.endswith('"'):
                raw_key = parse_primitive(raw_key)
            key, length, fields = parse_key(raw_key)
            rest = content[colon + 1 :].lstrip()
            pos[0] += 1
            if length is not None and fields is not None:
                rows = []
                while pos[0] < len(lines) and line_indent(lines[pos[0]]) > depth:
                    cells = split_csv(lines[pos[0]].strip())
                    if not cells or all(c == "" for c in cells):
                        pos[0] += 1
                        continue
                    rows.append({f: parse_primitive(c) for f, c in zip(fields, cells)})
                    pos[0] += 1
                out[key] = rows
                continue
            if length is not None:
                out[key] = parse_inline_array(rest) if rest else []
                continue
            if not rest:
                if pos[0] < len(lines) and line_indent(lines[pos[0]]) > depth:
                    out[key] = read_block(line_indent(lines[pos[0]]))
                else:
                    out[key] = {}
                continue
            out[key] = parse_primitive(rest)
        return out

    return read_block(0)


__all__ = ["toon_dump", "toon_load"]
