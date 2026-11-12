"""Условные поля паттерна: pattern.yaml `conditional_fields`.

Формат — `путь: условие & условие; путь: условие`. Путь — через точку от корня
settings, элементы массива — `[]` (`modules[].image`). Условие — `a.b=значение`,
`a.b!=значение`, `a.b` (истинно) или `!a.b` (ложно); `[]` в пути условия — тот же
элемент массива, что и у поля. Поле нужно, только когда выполнены все условия:
иначе new не пишет его пустую заглушку и не просит её заполнить.
"""

from __future__ import annotations

import re

_INDEX = re.compile(r"\[(\d+)\]")


def parse(spec: str) -> dict[str, list[tuple[str, str, object]]]:
    rules: dict[str, list[tuple[str, str, object]]] = {}
    for item in (spec or "").split(";"):
        path, sep, conds = item.partition(":")
        if not sep or not path.strip():
            continue
        parsed: list[tuple[str, str, object]] = []
        for cond in conds.split("&"):
            cond = cond.strip()
            if not cond:
                continue
            if "!=" in cond:
                left, _, right = cond.partition("!=")
                parsed.append((left.strip(), "ne", right.strip()))
            elif "=" in cond:
                left, _, right = cond.partition("=")
                parsed.append((left.strip(), "eq", right.strip()))
            elif cond.startswith("!"):
                parsed.append((cond[1:].strip(), "falsy", None))
            else:
                parsed.append((cond, "truthy", None))
        rules[path.strip()] = parsed
    return rules


def generic(path: str) -> str:
    """`modules[2].image` -> `modules[].image`."""
    return _INDEX.sub("[]", path)


def _get(settings: object, path: str) -> object:
    node = settings
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            index = int(part[1:-1])
            if not isinstance(node, list) or index >= len(node):
                return None
            node = node[index]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
    return node


def _concrete(cond_path: str, indices: list[str]) -> str:
    out = cond_path
    for index in indices:
        out = out.replace("[]", f"[{index}]", 1)
    return out


def applies(rules: dict, path: str) -> bool:
    return generic(path) in rules


def needed(rules: dict, settings: dict, path: str) -> bool:
    """Нужно ли поле по конкретному пути (`modules[1].image`) при этих настройках.
    Поле без правила нужно всегда."""
    conds = rules.get(generic(path))
    if not conds:
        return True
    indices = _INDEX.findall(path)
    for cond_path, op, value in conds:
        actual = _get(settings, _concrete(cond_path, indices))
        if op == "eq" and str(actual if not isinstance(actual, bool) else str(actual).lower()) != value:
            return False
        if op == "ne" and str(actual if not isinstance(actual, bool) else str(actual).lower()) == value:
            return False
        if op == "truthy" and not actual:
            return False
        if op == "falsy" and actual:
            return False
    return True
