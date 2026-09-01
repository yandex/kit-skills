"""Пути, коды выхода, чтение registry.json и mini-YAML паттернов."""

from __future__ import annotations

import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
REFERENCES = SKILL_ROOT / "references"
GENERATED = REFERENCES / "generated"
PATTERNS_DIR = REFERENCES / "patterns"

# Коды выхода. 1 и 4 зарезервированы под шкалу kit-storefront-constructor
# (HTTP/API и «правка опубликована старым сервером») и здесь не возникают
# никогда — сети у скила нет. 6 зарезервирован под будущий исход
# «рендер выполнен, секция пуста» (уровень L2).
EXIT_OK = 0
EXIT_GATES = 2
EXIT_FLAG_REQUIRED = 3
EXIT_PATTERN_NOT_FOUND = 5


class SectionError(SystemExit):
    """Фатальная ошибка с внятным сообщением (exit-код задаёт вызывающий)."""

    def __init__(self, message: str, code: int = EXIT_GATES) -> None:
        super().__init__(code)
        self.message = message
        print(f"error: {message}")


def load_registry() -> dict:
    path = GENERATED / "registry.json"
    if not path.is_file():
        raise SectionError(
            f"registry.json отсутствует ({path}) — справочник не собран; "
            "это ошибка поставки скила, а не ваша",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest() -> dict | None:
    path = GENERATED / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_definitions() -> dict | None:
    path = GENERATED / "definitions.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def parse_flat_yaml(text: str) -> dict:
    """pattern.yaml: плоское подмножество YAML `key: "value"` / key: value."""
    data: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SectionError(f"{path.name}: не JSON: {exc}")


def dump_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
