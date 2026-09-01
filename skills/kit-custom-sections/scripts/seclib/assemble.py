"""Сборка тройки из паттерна + answers. Детерминизм — техническое свойство:
html и css копируются из каталога байт-в-байт (гейт G11 это гарантирует),
answers заполняют только settings.json.
"""

from __future__ import annotations

import copy
import json

from .catalog import Pattern
from .common import load_definitions
from .schema import validate_instance


def build_settings(pattern: Pattern, answers: dict) -> tuple[dict, list[str]]:
    """settings = defaults ← answers (одноимённые ключи); modules получают id m1..mN.

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
    return settings, []


def build_create_json(title: str, html: str, css: str, schema: dict, mode: str) -> dict:
    """Форма validate_template_create соседа: json_schema — строкой, alias пуст."""
    return {
        "title": title,
        "alias": "",
        "html": html,
        "css": css,
        "json_schema": json.dumps(schema, ensure_ascii=False),
        "commit_message": f"kit-custom-sections: assembled section template (mode={mode})",
    }
