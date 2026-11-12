"""Сборка тройки из паттерна + answers. Детерминизм — техническое свойство:
html и css копируются из каталога байт-в-байт (гейт G11 это гарантирует),
answers заполняют только settings.json.
"""

from __future__ import annotations

import copy
import json

from . import conditions
from .catalog import Pattern
from .common import SLOT_ALIASES, load_definitions
from .schema import validate_instance

_MISSING = object()


def is_empty_model(value: object) -> bool:
    """Пустая модель: изображение или видео без файла, ссылка без адреса."""
    if not isinstance(value, dict):
        return False
    keys = set(value)
    if {"fileId", "filePath"} <= keys:
        return not value.get("fileId") and not value.get("filePath")
    if {"link", "linkId"} <= keys:
        return not value.get("link")
    return False


def prune_optional_models(settings: dict, schema: dict, answers: dict, rules: dict) -> None:
    """Убрать из settings пустые модели (изображение, видео, ссылку), которых нет в
    required родителя и которые не пришли из answers. Шаблон и так проверяет
    `{{#if x.filePath}}`, а пустой объект new называл заглушкой «заполнить», и
    plan lint дизайн-скила считал его пропавшим медиа. Поле с правилом
    conditional_fields остаётся, если при этих настройках оно нужно."""

    def walk(value: object, node: object, ans: object, path: str) -> None:
        node = node if isinstance(node, dict) else {}
        if isinstance(value, dict):
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            required = set(node.get("required") or [])
            for key in list(value):
                where = f"{path}.{key}" if path else key
                given = ans.get(key, _MISSING) if isinstance(ans, dict) else _MISSING
                item = value[key]
                if (
                    is_empty_model(item)
                    and key not in required
                    and given is _MISSING
                    and not (conditions.applies(rules, where) and conditions.needed(rules, settings, where))
                ):
                    del value[key]
                    continue
                walk(item, props.get(key), None if given is _MISSING else given, where)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                sub = ans[i] if isinstance(ans, list) and i < len(ans) else None
                walk(item, node.get("items"), sub, f"{path}[{i}]")

    walk(settings, schema, answers, "")


def _merge(settings: dict, values: dict) -> None:
    """Слить значения поверх settings: объекты — по верхнему уровню, остальное
    заменяется (так же сливаются answers)."""
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(settings.get(key), dict):
            merged = copy.deepcopy(settings[key])
            merged.update(copy.deepcopy(value))
            settings[key] = merged
        else:
            settings[key] = copy.deepcopy(value)


def build_settings(
    pattern: Pattern, answers: dict, look: dict | None = None
) -> tuple[dict, list[str]]:
    """settings = defaults ← вид ← answers (одноимённые ключи); modules получают id m1..mN.

    Вид (looks.json паттерна) — стартовое оформление новой секции: defaults.json
    держит умолчания прошлых версий для переезда, а новая секция начинается с
    вида, а не с них.

    Ключи вне answers-схемы — ошибка (additionalProperties: false): лишний ключ
    уехал бы на бэк, но форма мерчанта его не показывает и теряет при сохранении.
    """
    errors = validate_instance(
        pattern.answers_schema,
        answers,
        forbid_additional=True,
        definitions=load_definitions(),
    )
    if errors:
        return {}, errors

    settings = copy.deepcopy(pattern.defaults)
    if look:
        _merge(settings, look.get("settings") or {})
    for key, value in answers.items():
        if key == "modules" and isinstance(value, list):
            # Прототип модуля — первый модуль defaults: заполняет поля, которые
            # answers не обязаны давать (rating=5, caption="" и т.п.). id — свои,
            # детерминированные.
            proto = {}
            default_modules = pattern.defaults.get("modules")
            if isinstance(default_modules, list) and default_modules:
                proto = {k: v for k, v in default_modules[0].items() if k != "id"}
            modules = []
            for i, item in enumerate(value):
                module = dict(copy.deepcopy(proto))
                module["id"] = f"m{i + 1}"
                if isinstance(item, dict):
                    module.update(copy.deepcopy(item))
                modules.append(module)
            settings["modules"] = modules
        elif isinstance(value, dict) and isinstance(settings.get(key), dict):
            merged = copy.deepcopy(settings[key])
            merged.update(copy.deepcopy(value))
            settings[key] = merged
        else:
            settings[key] = copy.deepcopy(value)
    prune_optional_models(settings, pattern.schema, answers, pattern.conditional_fields)
    return settings, []


def build_create_json(
    title: str, html: str, css: str, schema: dict, mode: str, slot: str | None = None
) -> dict:
    """Форма validate_template_create соседа: json_schema — строкой.

    alias пуст у любой секции страницы. В режиме слота (`--slot header|footer`,
    подтверждён `--confirm-slot`) — `YandexKit.Header` / `YandexKit.Footer`:
    сосед принимает такой файл только с тем же `--chrome-slot`.
    """
    commit = f"kit-custom-sections: assembled section template (mode={mode}"
    commit += f", slot={slot})" if slot else ")"
    return {
        "title": title,
        "alias": SLOT_ALIASES[slot] if slot else "",
        "html": html,
        "css": css,
        "json_schema": json.dumps(schema, ensure_ascii=False),
        "commit_message": commit,
    }
