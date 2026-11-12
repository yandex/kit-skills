"""Гейты валидации G1–G21. Единый формат находки: code/severity/where/what/fix.

Реализованы G1–G8, G10 (корни путей), G11, G12, G16–G21 (G3 — в мягкой форме,
WARNING). G9/G13/G14 и сверка payload в G10 — итерация 2, G15 — в doctor. Список и
статусы — references/gates.md.
"""

from __future__ import annotations

import re

from . import css as css_mod
from . import schema as schema_mod
from . import tmpl as tmpl_mod
from .catalog import Pattern
from .common import (
    SLOT_ALIASES,
    SLOT_BY_ALIAS,
    SLOT_GEN,
    SLOT_INS,
    SLOT_RU,
    load_definitions,
)

ERROR = "error"
WARNING = "warning"


class Finding(dict):
    def __init__(self, code: str, severity: str, where: str, what: str, fix: str) -> None:
        super().__init__(code=code, severity=severity, where=where, what=what, fix=fix)


def _f(code: str, severity: str, where: str, what: str, fix: str) -> Finding:
    return Finding(code, severity, where, what, fix)


def run_gates(
    html: str,
    css: str,
    schema: dict,
    registry: dict,
    *,
    mode: str = "lint",  # new | compose | freeform | lint
    meta: dict | None = None,
    pattern: Pattern | None = None,
    strict: bool = False,
    allow_unknown_attrs: bool = False,
    settings: dict | None = None,
    settings_name: str = "settings.json",
    script: tuple[str, str] | None = None,
    slot: str | None = None,  # header | footer — режим слота (G12)
) -> list[Finding]:
    findings: list[Finding] = []
    model = tmpl_mod.parse(html, registry)
    definitions = load_definitions()
    if slot is None:
        slot = (meta or {}).get("slot") or (
            pattern.meta.get("slot") if pattern is not None else None
        ) or None

    findings += g1_structure(model, registry)
    findings += g2_tag_whitelist(model, registry)
    findings += g3_attributes(model, registry, allow_unknown_attrs)
    findings += g4_bijection(model, schema)
    findings += g5_schema(schema, registry)
    has_drawer = any(
        not t.closing and t.name in ("ya-kit-drawer", "ya-kit-paranja") for t in model.tags
    )
    # Происхождение css из каталога (байт-в-байт с эталоном, это же сверяет G11)
    # смягчает запрет хардкода цвета: правило целится в модель, сочиняющую css,
    # а не в выверенный импортом паттерн (официальный пример before-after сам
    # содержит rgba — иначе `new` по нему был бы невозможен).
    catalog_css = pattern is not None and css == pattern.css
    findings += g6_css(css, registry, mode, has_drawer=has_drawer, catalog_css=catalog_css)
    findings += g7_sanitary(model)
    findings += g7_script_field(script)
    if pattern is not None:
        findings += g11_pattern_conformance(html, css, pattern, meta)
    findings += g12_alias(meta, mode, slot)
    if slot is not None:
        findings += g12_slot_rules(model, settings, settings_name, script, mode, slot)
    if isinstance(settings, dict):
        findings += g8_settings(schema, settings, settings_name, definitions)
    findings += g10_alias_roots(model)
    findings += g16_type_usage(model, schema, registry, definitions)
    findings += g17_each_alias(model)
    findings += g18_css_external(css, registry)
    findings += g19_headings(model, schema)
    findings += g20_atom_semantics(model)
    if pattern is not None and isinstance(settings, dict):
        findings += g21_image_alt(pattern, settings, settings_name, mode)
    findings += g22_icon_styles(model, schema, registry)
    if strict:
        findings = [
            _f(f["code"], ERROR, f["where"], f["what"], f["fix"]) if f["severity"] == WARNING else f
            for f in findings
        ]
    return findings


# --- G1 ------------------------------------------------------------------

# Непустые html-элементы, которые часто пишут самозакрывающимися по привычке.
# svg-содержимое (path, circle…) сюда не входит: внутри svg `/>` парсер уважает.
_HTML_NON_VOID = {
    "a", "article", "aside", "b", "button", "div", "em", "figcaption", "figure",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "i", "label", "li",
    "main", "nav", "ol", "p", "section", "span", "strong", "table", "tbody", "td",
    "th", "thead", "tr", "ul",
}


def g1_structure(model: tmpl_mod.TemplateModel, registry: dict) -> list[Finding]:
    out: list[Finding] = []
    for err in model.structure_errors:
        out.append(_f("G1", ERROR, "html", err, "исправить структуру блоков/скобок"))
    for warn in model.structure_warnings:
        out.append(_f("G1", WARNING, "html", warn, "убрать конструкцию"))
    for err in model.block_in_attr:
        out.append(_f("G1", ERROR, "html", err, "вынести блочный хелпер из атрибута"))
    for tag in model.tags:
        if tag.self_closing and tag.name in _HTML_NON_VOID:
            out.append(
                _f(
                    "G1",
                    WARNING,
                    f"<{tag.name} />",
                    "`/>` разворачивается в закрывающий тег только у ya-kit-* и yandex-*; "
                    "обычный непустой html-тег останется открытым и заберёт соседние узлы "
                    "себе в дети (по коду движка, живьём не проверено)",
                    f"написать парный тег: <{tag.name} …></{tag.name}>",
                )
            )
    rule = registry["fragment_name_rule"]
    for name in sorted(model.partial_refs):
        if len(name) > rule["max_length"] or not re.fullmatch(rule["regex"], name or " "):
            out.append(
                _f(
                    "G1",
                    ERROR,
                    "html",
                    f"имя партиала {name!r} нарушает правило {rule['regex']} (<= {rule['max_length']})",
                    "переименовать фрагмент",
                )
            )
    return out


# --- G2 ------------------------------------------------------------------


def g2_tag_whitelist(model: tmpl_mod.TemplateModel, registry: dict) -> list[Finding]:
    out: list[Finding] = []
    known = (
        set(registry["tags"]["builtin"])
        | set(registry["tags"]["controllers"])
        | set(registry["tags"]["atoms"])
    )
    block_open_tags = {d["openTag"] for d in registry["helpers"]["block"]}
    n_controllers = len(registry["tags"]["controllers"])
    n_tags = len(known)
    seen: set[str] = set()
    for tag in model.tags:
        if tag.closing or tag.name in seen:
            continue
        seen.add(tag.name)
        is_platform = tag.name.startswith(("ya-kit-", "yandex-pay-"))
        if not is_platform:
            continue
        if tag.name in known:
            continue
        if tag.name.endswith("-controller"):
            out.append(
                _f(
                    "G2",
                    ERROR,
                    f"<{tag.name}>",
                    f"контроллера нет в реестре ({n_controllers}) — данные не придут, "
                    "секция молча опустеет",
                    _closest(tag.name, known),
                )
            )
        elif tag.name in block_open_tags:
            continue
        else:
            out.append(
                _f(
                    "G2",
                    ERROR,
                    f"<{tag.name}>",
                    f"тега нет в реестре ({n_tags}) — движок молча пропустит, секция опустеет",
                    _closest(tag.name, known),
                )
            )
    return out


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _closest(name: str, known: set[str]) -> str:
    # Имена с не-ASCII (кириллические опечатки доки `is-сollapsable`, `on-сhange`,
    # типографский дефис U+2011) как подсказку не предлагаем никогда: совет
    # «замените верное on-change на on‑change» ломает секцию молча.
    candidates = {k for k in known if k.isascii()}
    if not candidates:
        return "см. references/generated/tags.md"
    best = min(candidates, key=lambda k: _levenshtein(name, k))
    if _levenshtein(name, best) <= 3:
        return f"возможно, имелся в виду `{best}`"
    return "см. references/generated/tags.md"


# --- G3 ------------------------------------------------------------------


def _icon_component(name: str) -> str:
    """Имя компонента иконки так же, как его строит атом ya-kit-icon: части по
    `-`, у каждой заглавная первая буква (`whats-app` и `whatsApp` -> IconWhatsApp)."""
    return "Icon" + "".join(part[:1].upper() + part[1:] for part in name.split("-"))


def g3_attributes(
    model: tmpl_mod.TemplateModel, registry: dict, allow_unknown: bool
) -> list[Finding]:
    out: list[Finding] = []
    tag_attrs = registry.get("tag_attributes", {})
    icons = set(registry.get("icons") or [])
    icon_components = {_icon_component(n) for n in icons}
    builtin = set(registry["tags"]["builtin"])
    for tag_name, attr, value in model.unquoted_mustache_attrs:
        out.append(
            _f(
                "G3",
                WARNING,
                f"<{tag_name} {attr}={value}>",
                "значение с {{…}} без кавычек, а внутри пробел: как движок разбирает "
                "такой атрибут, не проверено — значение может оборваться на пробеле",
                f'{attr}="{value}"',
            )
        )
    for tag in model.tags:
        if tag.closing or not tag.name.startswith(("ya-kit-", "yandex-pay-")):
            continue
        info = tag_attrs.get(tag.name)
        if info is None:
            continue
        removed = info.get("removed_attributes") or {}
        code_attrs = set(info.get("code_attributes") or [])
        documented = set(info["attributes"])
        # Список атрибутов есть, если у тега таблица в доке или ручной слой по коду.
        checkable = bool(documented) and (info["documented"] or bool(code_attrs))
        for attr, value in tag.attrs:
            if attr.startswith(("aria-", "data-")) or attr in ("class", "id", "style"):
                continue
            if attr != attr.lower():
                out.append(
                    _f(
                        "G3",
                        ERROR,
                        f"<{tag.name} {attr}>",
                        "атрибуты пишутся в kebab-case",
                        attr.lower(),
                    )
                )
                continue
            if attr in removed:
                out.append(
                    _f(
                        "G3",
                        WARNING,
                        f"<{tag.name} {attr}>",
                        f"атрибут не действует — {removed[attr]}",
                        "убрать атрибут",
                    )
                )
                continue
            if value is None and tag.name not in builtin:
                # Голый атрибут приходит пустой строкой, интерполяция даёт
                # undefined — булев флаг (`autoplay`) молча выключен.
                out.append(
                    _f(
                        "G3",
                        WARNING,
                        f"<{tag.name} {attr}>",
                        "атрибут без значения не включается: движок получит пустое значение",
                        f'{attr}="true"',
                    )
                )
            if (
                tag.name == "ya-kit-icon"
                and attr == "name"
                and icons
                and value is not None
                and "{{" not in value
                and _icon_component(value) not in icon_components
            ):
                out.append(
                    _f(
                        "G3",
                        WARNING,
                        f'<ya-kit-icon name="{value}">',
                        "такой иконки нет — атом отрисует пустоту без ошибки",
                        _closest(value, icons) + " (список — references/generated/tags.md)",
                    )
                )
            if not checkable:
                continue  # у тега нет списка атрибутов — не вина автора секции
            if attr not in documented:
                if allow_unknown:
                    continue
                # WARNING, не ERROR: таблицы доки доказуемо неполны — официальный
                # пример FAQ использует align у ya-kit-section-title, которого в
                # таблице нет. --strict поднимает до ERROR.
                source = (
                    "не документирован для этого тега (таблицы доки неполны)"
                    if info["documented"]
                    else "нет в пропсах компонента по коду движка (таблицы в доке нет)"
                )
                out.append(
                    _f(
                        "G3",
                        WARNING,
                        f"<{tag.name} {attr}>",
                        f"атрибут {source}",
                        _closest(attr, documented) if documented else "см. tags.md",
                    )
                )
    return out


# --- G4 ------------------------------------------------------------------


def g4_bijection(model: tmpl_mod.TemplateModel, schema: dict) -> list[Finding]:
    out: list[Finding] = []
    schema_paths = schema_mod.leaf_paths(schema)
    html_paths = {p for p in model.settings_paths if p}

    def covered(path: str) -> bool:
        if path in schema_paths:
            return True
        # module id инжектится платформой
        if path.endswith("[].id"):
            return path[: -len(".id")] in schema_paths or path[: -len("[].id")] in schema_paths
        # подполя $ref-модели платформы (например cover.url.link при url: LinkSource)
        # схема секции не описывает — это легально
        node, via_model = schema_mod.node_at(schema, path)
        if via_model or node is not None:
            return True
        return False

    for path in sorted(html_paths):
        if not covered(path):
            out.append(
                _f(
                    "G4",
                    ERROR,
                    f"{{{{settings.{path}}}}}",
                    "путь читается в html, но отсутствует в properties json_schema — "
                    "на витрине будет пусто",
                    "добавить свойство в схему или исправить путь в html",
                )
            )

    used_prefixes = set()
    # Пути, которые хелпер читает строковым ключом (`mapBy settings.modules
    # 'product.variantId'`, `get settings "url"`), считаются употреблёнными,
    # но в ERROR «нет в схеме» не идут: ключ — строка, не путь шаблона.
    for path in html_paths | {p for p in model.settings_key_paths if p}:
        parts = re.split(r"(?<=\])\.|\.", path)
        acc = ""
        for part in parts:
            acc = f"{acc}.{part}" if acc else part
            used_prefixes.add(acc)
    for path in sorted(schema_paths):
        node_used = path in used_prefixes or any(
            p.startswith(path + ".") or p.startswith(path + "[]") for p in used_prefixes
        )
        if not node_used:
            out.append(
                _f(
                    "G4",
                    WARNING,
                    f"json_schema:{path}",
                    "свойство объявлено в схеме, но html его не читает — мёртвая настройка",
                    "удалить из схемы или использовать в html",
                )
            )
    return out


# --- G5 ------------------------------------------------------------------


def g5_schema(schema: dict, registry: dict) -> list[Finding]:
    out: list[Finding] = []
    if not isinstance(schema, dict):
        return [_f("G5", ERROR, "json_schema", "схема не является JSON-объектом", "исправить")]

    allowed = schema_mod.allowed_keywords(registry)
    for keyword, places in schema_mod.collect_keywords(schema).items():
        if keyword in schema_mod.FORBIDDEN_KEYWORDS:
            out.append(
                _f(
                    "G5",
                    ERROR,
                    f"json_schema:{places[0]}",
                    f"ключевое слово `{keyword}` запрещено — секцию потом нельзя "
                    "редактировать через kit-storefront-constructor",
                    "переписать без комбинаторов",
                )
            )
        elif keyword not in allowed:
            out.append(
                _f(
                    "G5",
                    ERROR,
                    f"json_schema:{places[0]}",
                    f"ключевое слово `{keyword}` вне поддерживаемого подмножества draft-07",
                    "см. references/generated/schema-keywords.md",
                )
            )

    platform_refs = (
        set(registry["reserved_refs"])
        | set(registry["service_refs"])
        | set(registry.get("renamed_refs", {}))
    )
    form_only = set(registry.get("form_only_refs", []))
    local_refs = schema_mod.local_definition_refs(schema)
    kept_refs = set(registry["reserved_refs"]) | set(registry.get("renamed_refs", {}))
    for ref in sorted(local_refs & (platform_refs | form_only)):
        if ref in kept_refs:
            # Резервную (и переименованную в резервную) ссылку резолвер
            # редактора не разворачивает: контрол формы остаётся, тело секции
            # не читается никем.
            what = (
                f"собственное определение с именем платформенного типа {ref}: платформа "
                "держит эту ссылку за собой, тело из definitions секции не читается "
                "(по коду редактора) — описанные в нём поля не действуют"
            )
            fix = "убрать одноимённое определение; для своего объекта взять другое имя"
        elif ref in form_only:
            what = (
                f"собственное определение перекрывает тип {ref}: резолвер встроит тело и "
                "снимет $ref — форма кабинета нарисует поля тела вместо своего контрола"
            )
            fix = "убрать одноимённое определение из definitions схемы"
        else:
            what = (
                f"собственное определение перекрывает служебный тип {ref}: ссылка может "
                "разрешиться в тело секции вместо платформенного (порядок слияния живьём "
                "не проверен)"
            )
            fix = "убрать одноимённое определение из definitions схемы"
        out.append(
            _f("G5", WARNING, f"json_schema:definitions.{ref.rsplit('/', 1)[-1]}", what, fix)
        )
    ref_whitelist = platform_refs | local_refs
    for where, ref in schema_mod.collect_refs(schema):
        if ref.startswith("#/definitions/partials/"):
            continue
        if ref in form_only and ref not in local_refs:
            out.append(
                _f(
                    "G5",
                    WARNING,
                    f"json_schema:{where}",
                    f"$ref {ref} понимает только форма кабинета: витрина тип не резолвит, "
                    "тела нет в definitions.json, сквозной путь до витрины не проверен",
                    "в шаблоне опираться только на ключ variantId значения; проверить "
                    "в превью proposal-версии",
                )
            )
            continue
        if ref not in ref_whitelist:
            out.append(
                _f(
                    "G5",
                    ERROR,
                    f"json_schema:{where}",
                    f"$ref {ref} вне белого списка ({len(registry['reserved_refs'])} резервных + "
                    f"{len(registry['service_refs'])} служебных + partials + собственные определения)",
                    "см. schema-keywords.md, таблица $ref",
                )
            )

    for path, node in schema_mod.walk(schema):
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            if node["items"].get("type") == "object":
                last = path.split(".")[-1] if path else path
                if last != "modules":
                    out.append(
                        _f(
                            "G5",
                            ERROR,
                            f"json_schema:{path or '<root>'}",
                            "повторяемые блоки поддерживаются только под именем `modules`",
                            "переименовать массив в modules",
                        )
                    )

    # required ⊆ properties: имя в required без определения в properties —
    # прямой блокер записи (конструктор отклоняет settings при привязке секции);
    # обязан ловиться офлайн, а не на сервере.
    for path, node in schema_mod.walk(schema):
        req = node.get("required")
        props = node.get("properties")
        if not isinstance(req, list) or not isinstance(props, dict):
            continue
        where = f"json_schema:{path or '<root>'}"
        seen: set[str] = set()
        for name in req:
            if name not in props:
                out.append(
                    _f(
                        "G5",
                        ERROR,
                        where,
                        f"required содержит {name!r}, которого нет в properties — "
                        "конструктор отклонит настройки секции при привязке",
                        "убрать имя из required или объявить свойство",
                    )
                )
            if name in seen:
                out.append(
                    _f(
                        "G5",
                        WARNING,
                        where,
                        f"required содержит {name!r} дважды",
                        "убрать дубликат",
                    )
                )
            seen.add(name)
    return out


# --- G6 ------------------------------------------------------------------


def g6_css(
    css: str,
    registry: dict,
    mode: str,
    has_drawer: bool = False,
    catalog_css: bool = False,
) -> list[Finding]:
    out: list[Finding] = []
    scan = css_mod.scan(css, registry)
    # Официальный пример custom-snippets
    # сам использует :global(.drawer …) — контент <ya-kit-drawer>/<ya-kit-paranja>
    # рендерится порталом ВНЕ скоупа секции и иначе недостижим для её CSS.
    # Поэтому: в шаблоне есть drawer/paranja -> WARNING (глобальный селектор всё
    # равно делит неймспейс со всей витриной), нет -> ERROR как раньше.
    global_severity = WARNING if has_drawer else ERROR
    global_fix = (
        "убедиться, что селекторы затрагивают только контент вашего drawer "
        "(классы с уникальным префиксом): :global( делит неймспейс всей витрины"
        if has_drawer
        else "убрать :global( — это перезапись стилей всей витрины"
    )
    for what in scan["global_escape"]:
        out.append(_f("G6", global_severity, "css", what, global_fix))
    for what in scan["angle_bracket"]:
        out.append(_f("G6", ERROR, "css", what, "убрать символ '<'"))
    for what in scan["unknown_ys_vars"]:
        out.append(_f("G6", ERROR, "css", what, "см. references/generated/tokens.md"))
    for what in scan["undefined_local_vars"]:
        out.append(_f("G6", WARNING, "css", what, "определить переменную или взять токен темы"))
    color_severity = (
        ERROR if mode in ("new", "compose", "pattern") and not catalog_css else WARNING
    )
    for what in scan["hardcoded_colors"]:
        out.append(_f("G6", color_severity, "css", what, "заменить на var(--ys-*)"))
    return out


# --- G7 ------------------------------------------------------------------

_JS_HREF = re.compile(r"^\s*javascript:", re.I)


def g7_sanitary(model: tmpl_mod.TemplateModel) -> list[Finding]:
    out: list[Finding] = []
    for tag in model.tags:
        if tag.closing:
            continue
        if tag.name in ("script", "style"):
            out.append(
                _f(
                    "G7",
                    ERROR,
                    f"<{tag.name}>",
                    "вырезается платформой молча (ELEMENT_BLACKLIST); css — только в поле css",
                    "убрать тег",
                )
            )
        if tag.name in ("iframe", "object", "embed"):
            out.append(
                _f("G7", ERROR, f"<{tag.name}>", "встраиваемый контент запрещён", "убрать тег")
            )
        for attr, value in tag.attrs:
            if attr.lower().startswith("on") and value is not None and not value.startswith("{{"):
                out.append(
                    _f(
                        "G7",
                        ERROR,
                        f"<{tag.name} {attr}>",
                        "инлайновый обработчик со строковым значением: строковый JS "
                        "в атрибуте не исполняется",
                        "убрать; обработчики — только значениями контроллеров "
                        "(`{{controller.onX}}`); этот скил JS не генерирует (SKILL.md, правило 3)",
                    )
                )
            if attr.lower() == "href" and value and _JS_HREF.match(value):
                out.append(
                    _f("G7", ERROR, f"<{tag.name} href>", "javascript: в ссылке", "убрать")
                )
    return out


def g7_script_field(script: tuple[str, str] | None) -> list[Finding]:
    """Непустое поле `script` в экспорте: линт его не читает — сказать об этом,
    чтобы зелёный линт не читался как «шаблон проверен целиком»."""
    if not script or not script[1].strip():
        return []
    name, body = script
    return [
        _f(
            "G7",
            WARNING,
            name,
            f"у шаблона непустое поле script ({len(body.encode('utf-8'))} байт): линт JS не "
            "проверяет, у скрипта доступ к странице и сети (manual/rules.md)",
            "показать скрипт пользователю целиком; при переезде переносить вместе с "
            "шаблоном (SKILL.md, правило 16)",
        )
    ]


# --- G8 ------------------------------------------------------------------


def g8_settings(
    schema: dict, instance: dict, source_name: str, definitions: dict | None = None
) -> list[Finding]:
    """Настройки (settings.json / defaults.json) валидны против схемы секции.

    Без этого гейта невалидные настройки (отсутствующее required-поле, значение
    вне enum) отклонял бы только конструктор при привязке. Тела платформенных
    $ref резолвятся через definitions.json, поэтому enum внутри ссылочного типа
    (hPaddingDesktop: "250" при 10 разрешённых значениях) тоже ловится офлайн.
    anyOf-тела (Palette) не судятся.
    """
    out: list[Finding] = []
    for err in schema_mod.validate_instance(schema, instance, definitions=definitions):
        out.append(
            _f(
                "G8",
                ERROR,
                f"{source_name}:{err.split(':', 1)[0]}",
                f"настройки не проходят собственную схему секции: {err}",
                "исправить настройки или схему — конструктор отклонит такую секцию",
            )
        )
    return out


# --- G10 -----------------------------------------------------------------


def g10_alias_roots(model: tmpl_mod.TemplateModel) -> list[Finding]:
    """Корень пути, которого нет в скоупе, — ERROR.

    В скоупе семь корней контекста (`settings`, `globals`, `route`, `platform`,
    `clientType`, `helpers`, `isLCP`), алиасы контроллеров (`as`, по умолчанию
    `controller`) внутри их тега и локали блоков (`as |m i|`, `index` у each,
    счётчик repeat/times). Всё остальное движок разрешает в undefined: ветка
    `{{#if}}` молча не срабатывает, текст печатается пустым, линт без этого
    гейта зелёный. Поля объекта из `{{#with x}}` без `as` не судятся.

    Имя на месте хелпера (`{{modifier "x" y}}`, `(fn …)`), которого нет среди
    инлайн-хелперов справочника, получает свой совет: контроллер с таким `as`
    тут ни при чём. Корень `section` — тоже: объект убран из контекста.
    """
    out: list[Finding] = []
    for root, lines in sorted(model.unresolved_roots.items()):
        where = ", ".join(str(n) for n in lines[:5]) + ("…" if len(lines) > 5 else "")
        fields = sorted(p for p in model.alias_paths.get(f"?{root}", set()) if p)
        sample = f" ({root}.{fields[0]})" if fields else ""
        if root == "section":
            what = (
                "объекта `section` в контексте шаблона нет (убран 27.10.2026): движок "
                "отдаст undefined, ветка или текст молча опустеют"
            )
            fix = (
                "флаг первого экрана — корень `isLCP` (`{{#if isLCP}}`), остальное — "
                "settings/globals/route (data-context.md)"
            )
        elif root in model.unknown_call_heads:
            what = (
                f"`{root}` стоит на месте хелпера, но такого инлайн-хелпера нет в "
                "справочнике: движок прочитает его как путь и отдаст undefined"
            )
            fix = (
                "взять хелпер из references/generated/helpers.md; если хелпер есть в "
                "свежей Mystique — справочник устарел (`section.py doctor`), иначе опечатка"
            )
        else:
            what = (
                f"корень `{root}` не объявлен в этом месте шаблона: нет контроллера с "
                f'as="{root}" вокруг, это не локаль блока и не корень контекста — движок '
                "отдаст undefined, ветка или текст молча опустеют"
            )
            fix = (
                f'обернуть в тег контроллера с as="{root}", взять алиас объявленного '
                "контроллера или settings/globals/route (data-context.md); фрагмент, "
                "который родитель вкладывает внутрь своего контроллера, проверять в превью"
            )
        out.append(_f("G10", ERROR, f"html: {root}{sample}, строка {where}", what, fix))
    return out


# --- G11 -----------------------------------------------------------------


def g11_pattern_conformance(
    html: str, css: str, pattern: Pattern, meta: dict | None = None
) -> list[Finding]:
    out: list[Finding] = []
    built = (meta or {}).get("pattern_version")
    current = pattern.meta.get("version")
    if built is not None and current is not None and str(built) != str(current):
        # Собрано из прошлой версии паттерна: побайтная сверка с текущим эталоном
        # обвинила бы автора в правке, которой не было.
        return [
            _f(
                "G11",
                WARNING,
                "meta.json:pattern_version",
                f"собрано из версии {built} паттерна {pattern.id}, в каталоге версия {current} — "
                "с эталоном не сверялось; в новой версии есть исправления",
                f"пересобрать через new --pattern {pattern.id} с теми же answers",
            )
        ]
    if html != pattern.html:
        out.append(
            _f(
                "G11",
                ERROR,
                "template.html",
                f"собранный html не совпадает побайтно с эталоном паттерна {pattern.id}",
                "не править разметку после сборки; правки паттернов — только через генератор",
            )
        )
    if css != pattern.css:
        out.append(
            _f(
                "G11",
                ERROR,
                "styles.css",
                f"css не совпадает побайтно с эталоном паттерна {pattern.id}",
                "не править css после сборки",
            )
        )
    return out


# --- G12 -----------------------------------------------------------------


def g12_alias(meta: dict | None, mode: str = "lint", slot: str | None = None) -> list[Finding]:
    """alias шаблона: пуст у секции страницы, равен слоту в режиме слота.

    alias ничего не подменяет сам по себе — он маркирует шаблон. Значения
    `YandexKit.Header` / `YandexKit.Footer` делают шаблон шапкой или подвалом
    витрины: сервер отдаёт их в слот каждой страницы, включая страницы, которые
    добавит сам, а витрина выносит такую секцию из `<main>` в область шапки или
    подвала. Скрывать секцию на таком шаблоне сервер с 28.09.2026 не запрещает —
    витрина тогда остаётся без шапки или подвала. Поэтому такой alias ставит
    только режим слота (`--slot`, подтверждённый `--confirm-slot`).

    Вне режима слота строгость по режиму (та же развилка, что у хардкода цветов
    G6): в наших детерминированных режимах непустой alias — ERROR; при линте
    чужого экспорта — WARNING: на витринах шапка или подвал из шаблона бывают
    осознанным решением владельца.
    """
    out: list[Finding] = []
    alias = (meta or {}).get("alias") or ""
    if slot is not None:
        expected = SLOT_ALIASES.get(slot)
        if expected is None:
            out.append(
                _f(
                    "G12",
                    ERROR,
                    "slot",
                    f"неизвестный слот {slot!r}",
                    "слотов два: header и footer",
                )
            )
        elif meta is not None and alias != expected:
            # Без meta alias неизвестен (сырая тройка до сборки) — его поставит сборка.
            out.append(
                _f(
                    "G12",
                    ERROR,
                    "alias",
                    f"режим слота {slot}: alias обязан быть {expected}, "
                    f"в шаблоне {alias or 'пусто'}",
                    f"собрать через new/freeform --slot {slot} --confirm-slot — alias "
                    "проставит сборка; для секции страницы проверять без --slot",
                )
            )
        return out
    if not alias:
        return out
    severity = ERROR if mode in ("new", "compose", "pattern") else WARNING
    alias_slot = SLOT_BY_ALIAS.get(alias)
    if alias_slot is not None:
        out.append(
            _f(
                "G12",
                severity,
                "alias",
                f"alias={alias}: это {SLOT_RU[alias_slot]} всей витрины, а не секция "
                "страницы — сервер ставит шаблон в слот каждой страницы, витрина "
                "выносит секцию из контента страницы в область "
                f"{SLOT_GEN[alias_slot]}",
                f"секции страницы — пустой alias; {SLOT_RU[alias_slot]} витрины — только "
                f"режим слота: new/freeform --slot {alias_slot} --confirm-slot, для "
                f"чужого экспорта lint --slot {alias_slot}",
            )
        )
    else:
        out.append(
            _f(
                "G12",
                severity,
                "alias",
                f"alias={alias!r} непуст: сервер хранит строку без проверки; alias с "
                "именем нативного виджета выводит шаблон в «Заменить» этого виджета в "
                "кабинете, а `YandexKit.AnnouncementBar` ещё и переносит секцию над "
                "контентом страницы, в область шапки",
                "у кастомной секции alias пуст",
            )
        )
    return out


# Ширина слота не проверяется: фон секции SectionLayout рисует во всю ширину при
# любом isFullscreen, а содержимое по ширине темы — обычный вид нативных шапки и
# подвала (у подвала — в 27 из 39 магазинов корпуса).
_SLOT_NO_PADDING = {"paddingTop": "none", "paddingBottom": "none"}
_SLOT_NO_RHYTHM = ("verticalRhythm", "verticalRhythmTouch")


def g12_slot_rules(
    model: tmpl_mod.TemplateModel,
    settings: dict | None,
    settings_name: str,
    script: tuple[str, str] | None,
    mode: str,
    slot: str,
) -> list[Finding]:
    """Правила слота шапки/подвала (references/manual/rules.md, «Слот шапки и подвала»).

    Шаблон в слоте рисуется на каждой странице витрины, поэтому: без h1 (у
    страницы свой), без JS в поле script, секция без вертикальных отступов.
    Ширину содержимого (isFullscreen) выбирает шаблон: фон во всю ширину при любом
    значении. Порядок в слоте (1 у шапки, 9999 у подвала) держит сам слот —
    это не свойство шаблона, и здесь не проверяется.
    """
    if slot not in SLOT_ALIASES:
        return []
    out: list[Finding] = []
    name = SLOT_RU[slot]
    h1 = [t for t in model.tags if not t.closing and t.name == "h1"]
    h1 += [
        t
        for t in model.tags
        if not t.closing and t.name == "ya-kit-text" and dict(t.attrs).get("as") == "h1"
    ]
    if h1:
        out.append(
            _f(
                "G12",
                ERROR,
                "html",
                f"h1 в шаблоне слота: {name} стоит на каждой странице, и у каждой "
                "страницы окажется второй h1 рядом с её собственным",
                "понизить до h2/h3 или вынести текст в обычный элемент",
            )
        )
    if script and script[1].strip():
        out.append(
            _f(
                "G12",
                ERROR,
                script[0],
                f"script в шаблоне слота: {name} витрины собирается без JS — на адресе "
                "*.yandex.ru и в превью конструктора скрипт не исполняется, а офлайн "
                "его поведение не проверить",
                "убрать script; поведение — контроллерами (toggle, value, menu)",
            )
        )
    severity = ERROR if mode in ("new", "compose", "pattern", "freeform") else WARNING
    for tag in model.tags:
        if tag.closing or tag.name != "ya-kit-section-layout":
            continue
        for attr, value in tag.attrs:
            if attr in ("padding-top", "padding-bottom") and value and "{{" not in value:
                if value != "none":
                    out.append(
                        _f(
                            "G12",
                            severity,
                            f"<ya-kit-section-layout {attr}>",
                            f"{attr}={value!r}: у {SLOT_GEN[slot]} витрины вертикальных "
                            "отступов секции нет",
                            f'{attr}="none" или значение из настроек section',
                        )
                    )
    if isinstance(settings, dict):
        section = settings.get("section")
        if isinstance(section, dict):
            for key, expected in _SLOT_NO_PADDING.items():
                if key in section and section[key] != expected:
                    out.append(
                        _f(
                            "G12",
                            severity,
                            f"{settings_name}:section.{key}",
                            f"section.{key}={section[key]!r}: {name} в слоте — секция "
                            "без вертикальных отступов",
                            f"section.{key}={expected!r}",
                        )
                    )
        for key in _SLOT_NO_RHYTHM:
            if key in settings and settings[key] != "0":
                out.append(
                    _f(
                        "G12",
                        severity,
                        f"{settings_name}:{key}",
                        f"{key}={settings[key]!r}: ритм-отступ над и под {SLOT_INS[slot]} "
                        "даёт полосу пустоты на каждой странице",
                        f"{key}='0'",
                    )
                )
    return out


# --- G17 -----------------------------------------------------------------


def g17_each_alias(model: tmpl_mod.TemplateModel) -> list[Finding]:
    """`each` обязан объявлять явный алиас (`as |m|` / атрибут as).

    `{{#each settings.modules}}` c `{{name}}` внутри проходит линт, но витрина
    отрисовывает пустые карточки (DOM без текста и src) — ещё один канал
    «зелёный линт -> пустота». Все doc-примеры пишут алиас явно.
    """
    return [
        _f(
            "G17",
            ERROR,
            "html",
            f"{where}: поля внутри без алиаса не читаются — витрина отрисует "
            "пустые значения без единой ошибки",
            "добавить `as |m|` (или атрибут as) и читать поля через m.…",
        )
        for where in model.each_without_alias
    ]


# --- G18 -----------------------------------------------------------------


def g18_css_external(css: str, registry: dict) -> list[Finding]:
    """`//` и @import в css секции запрещены.

    CSSIsolator платформы вырезает `//` до конца строки без учёта строк и url —
    `url(https://…)` одного @font-face оставляет незакрытую скобку и отравляет
    каскад всей страницы. Следствие:
    никакой внешний ресурс (включая собственный шрифт) из css секции подключить
    нельзя; фоновые картинки — только через <img>/атомы. Обходить экранированием
    (\\2f) нельзя — это обход санитайзера платформы.
    """
    out: list[Finding] = []
    scan = css_mod.scan(css, registry)
    for what in scan["double_slash"]:
        out.append(
            _f(
                "G18",
                ERROR,
                "css",
                what,
                "внешние url из css секции невозможны (в т.ч. @font-face со своим "
                "шрифтом); картинки — через <img>/атомы, шрифт — только тот, что "
                "уже есть у темы",
            )
        )
    for what in scan["at_import"]:
        out.append(_f("G18", ERROR, "css", what, "убрать @import"))
    return out


# --- G19 -----------------------------------------------------------------

_HEADING_TAG = re.compile(r"^h([1-6])$")
_BRANCH_KINDS = ("block-open", "block-close", "else", "elseif")
# Блочные хелперы в форме тегов: открывающий и закрывающий тег — граница блока,
# ya-kit-else / ya-kit-elseif внутри — граница ветки (как {{else}} у усов).
_BLOCK_TAGS = ("ya-kit-if", "ya-kit-unless", "ya-kit-each", "ya-kit-with", "ya-kit-repeat")
_BRANCH_TAGS = ("ya-kit-else", "ya-kit-elseif")
_SETTINGS_TO = re.compile(r"^\{\{\s*settings\.([A-Za-z0-9_.]+)\s*\}\}$")


def _to_may_be_empty(value: str, schema: dict | None) -> bool:
    """Динамический `to` может оказаться пустым, и тогда атом рендерит h2.

    Ссылкой (не заголовком) считается только поле settings, которое схема
    обязывает заполнить и у которого непустой default. Путь не из settings
    (алиас each, хелпер) офлайн не разрешается — тоже «может быть пустым».
    """
    m = _SETTINGS_TO.match(value.strip())
    if not m or not isinstance(schema, dict):
        return True
    node: object = schema
    parts = m.group(1).split(".")
    for part in parts:
        if not isinstance(node, dict):
            return True
        props = node.get("properties")
        if not isinstance(props, dict) or part not in props:
            return True
        required = node.get("required") or []
        child = props[part]
        if part not in required:
            return True
        node = child
    if not isinstance(node, dict):
        return True
    default = node.get("default")
    return not (isinstance(default, str) and default.strip())


def _heading_level(tag: tmpl_mod.TagUse, schema: dict | None = None) -> int | None:
    """Уровень заголовка, который тег даст в разметке, или None."""
    m = _HEADING_TAG.match(tag.name)
    if m:
        return int(m.group(1))
    attrs = dict(tag.attrs)
    if tag.name == "ya-kit-section-title":
        # С непустым `to` атом рендерит ссылку, а не заголовок, и в выборе h1
        # страницы не участвует. Динамический `to={{settings.x}}` — заголовок h2,
        # если поле может быть пустым: не обязательно в схеме или default пуст.
        to = (attrs.get("to") or "").strip()
        if to and ("{{" not in to or not _to_may_be_empty(to, schema)):
            return None
        # `as` атом перекрывает уровнем, который выбирает платформа: h2,
        # а у первого заголовка секции на странице — h1 (решает страница)
        return 2
    if tag.name == "ya-kit-text":
        m2 = _HEADING_TAG.match(attrs.get("as") or "")
        if m2:
            return int(m2.group(1))
    return None


def _headings_in_order(
    model: tmpl_mod.TemplateModel, schema: dict | None = None
) -> list[tuple[int, tuple]]:
    """Заголовки в порядке разметки с путём ветвления: ((id блока, № ветки), …).

    Ветки одного блока (`if`/`else`/`elseif`, тело `each` и его `else`) —
    и в усах, и в тегах (`ya-kit-if` … `ya-kit-elseif` … `ya-kit-else` …
    `</ya-kit-if>`) — взаимоисключающие: их заголовки в рантайме вместе не
    встречаются.
    """
    events: list[tuple[int, int, str, int]] = []
    for m in model.mustaches:
        if m.kind in _BRANCH_KINDS:
            events.append((m.pos, 0, m.kind, 0))
    for tag in model.tags:
        if tag.name in _BLOCK_TAGS:
            if tag.closing:
                events.append((tag.pos, 0, "block-close", 0))
            elif not tag.self_closing:
                events.append((tag.pos, 0, "block-open", 0))
            continue
        if tag.name in _BRANCH_TAGS:
            if not tag.closing:
                events.append((tag.pos, 0, "else", 0))
            continue
        if tag.closing:
            continue
        level = _heading_level(tag, schema)
        if level is not None:
            events.append((tag.pos, 1, "heading", level))
    events.sort(key=lambda e: (e[0], e[1]))
    stack: list[list[int]] = []
    next_id = 0
    out: list[tuple[int, tuple]] = []
    for _pos, _order, kind, level in events:
        if kind == "block-open":
            stack.append([next_id, 0])
            next_id += 1
        elif kind in ("else", "elseif"):
            if stack:
                stack[-1][1] += 1
        elif kind == "block-close":
            if stack:
                stack.pop()
        else:
            out.append((level, tuple((b, i) for b, i in stack)))
    return out


def _exclusive(path_a: tuple, path_b: tuple) -> bool:
    """Заголовки в разных ветках одного блока в рантайме вместе не бывают."""
    branches = dict(path_a)
    return any(block in branches and branches[block] != branch for block, branch in path_b)


def g19_headings(model: tmpl_mod.TemplateModel, schema: dict | None = None) -> list[Finding]:
    """Дисциплина заголовков шаблона: не больше одного h1, уровни без дыр.

    `ya-kit-section-title` рендерит h2 (с 16.10.2026, раньше h3), а первый
    заголовок секции на странице витрина делает h1 (с 26.10.2026 и в секциях
    Mystique). Гейт считает его h2: какой из них станет h1, решает страница, а
    не шаблон. С непустым `to` атом рендерит ссылку — такой тег не заголовок;
    динамический `to={{settings.x}}` — заголовок h2, если поле может быть пустым
    (не обязательно в схеме или default пуст).
    Ветки блоков в форме тегов (`ya-kit-if`/`ya-kit-elseif`/`ya-kit-else`)
    взаимоисключающие, как ветки усов.
    Правило каталога: заголовок секции — уровень платформенного атома
    (h2), подзаголовки плиток под ним — h3; h1 — только первый блок страницы
    (hero, hero-overlay, section-intro) или паттерн, заменяющий главный нативный
    блок (custom-product-card, listing-grid).
    Уровень виден только литеральным `as="hN"`: динамический `as={{…}}` здесь не
    считается, поэтому уровень из настроек пишется ветками if/elseif.

    Дыры ищутся двумя способами: в наборе уровней шаблона и по порядку разметки —
    каждый заголовок сравнивается с ближайшим предыдущим, который может стоять с
    ним в одном рендере (другая ветка того же if/else не в счёт). Второй способ
    ловит `h2 -> h4`, когда h3 в шаблоне есть, но в другой ветке или ниже
    (шторка, ряд без заголовка секции).
    """
    headings = _headings_in_order(model, schema)
    levels: list[int] = [level for level, _path in headings]
    out: list[Finding] = []
    if levels.count(1) > 1:
        # Официальный пример кастомной карточки товара держит два h1 во
        # взаимоисключающих ветках if/else (скелетон против данных) — в рантайме
        # h1 один. Статически ветвление не доказывается, поэтому при наличии
        # условных блоков — WARNING, безусловный дубль h1 — ERROR.
        has_conditionals = any(
            m.kind == "block-open" and m.body.lstrip("#").split()[0] in ("if", "unless")
            for m in model.mustaches
            if m.body.strip()
        ) or any(not t.closing and t.name in ("ya-kit-if", "ya-kit-unless") for t in model.tags)
        out.append(
            _f(
                "G19",
                WARNING if has_conditionals else ERROR,
                "html",
                f"в шаблоне {levels.count(1)} элемента h1 — h1 на странице один "
                "(и обычно это hero)"
                + (
                    "; если это взаимоисключающие ветки if/else (скелетон) — ок"
                    if has_conditionals
                    else ""
                ),
                "оставить один h1, остальные понизить",
            )
        )
    jumps: list[tuple[int, int]] = []
    unique = sorted(set(levels))
    for prev, cur in zip(unique, unique[1:]):
        if cur - prev > 1:
            jumps.append((prev, cur))
    for index, (level, path) in enumerate(headings):
        prev_level = next(
            (lvl for lvl, prev_path in reversed(headings[:index]) if not _exclusive(prev_path, path)),
            None,
        )
        if prev_level is not None and level - prev_level > 1 and (prev_level, level) not in jumps:
            jumps.append((prev_level, level))
    for prev, cur in jumps:
        out.append(
            _f(
                "G19",
                WARNING,
                "html",
                f"уровни заголовков шаблона прыгают: h{prev} -> h{cur} без h{prev + 1}",
                "выровнять лестницу уровней (заголовок секции h2, плитки h3)",
            )
        )
    return out


# --- G16 -----------------------------------------------------------------

_MODEL_TOKEN = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")


def _attr_type_class(type_raw: str | None) -> str:
    """Класс документированного типа атрибута: scalar | model | unknown.

    Коллекция (`string[]`, `Array<…>`) — не скаляр: массив из настроек в такой
    атрибут — ровно то, что атом ждёт (`variant-ids` у products-group).
    """
    if not type_raw:
        return "unknown"
    raw = type_raw.strip()
    if raw.endswith("[]") or raw.startswith("Array<"):
        return "model"
    return "model" if _MODEL_TOKEN.search(raw) else "scalar"


def g16_type_usage(
    model: tmpl_mod.TemplateModel,
    schema: dict,
    registry: dict,
    definitions: dict | None,
) -> list[Finding]:
    """Совместимость типа схемы со способом употребления пути в html.

    Типовой пропуск без этого гейта: `titleText` объявлен `$ref ImageModel`,
    html подставляет его в скалярный `text="{{…}}"` — линт зелёный, а витрина
    отрисовывает пустоту (движок бросает, ErrorBoundary возвращает null).
    Судим только там, где тип известен с обеих сторон; anyOf (Palette) и
    недокументированные атрибуты жёстко не судятся.
    """
    out: list[Finding] = []
    tag_attrs = registry.get("tag_attributes", {})
    seen: set[tuple] = set()
    for path, use in model.settings_uses:
        key = (path, use)
        if key in seen:
            continue
        seen.add(key)
        node, via_model = schema_mod.node_at(schema, path)
        if via_model or node is None:
            continue  # подполе модели или отсутствие пути (это дело G4)
        kind = schema_mod.kind_of(node, schema, definitions)
        if kind in ("mixed", "unknown"):
            continue
        ref_name = node.get("$ref", "").rsplit("/", 1)[-1] if isinstance(node.get("$ref"), str) else None
        type_label = f"{kind}" + (f" ({ref_name})" if ref_name else "")

        if use[0] == "count":
            # repeat/times: `{{#repeat x}}` движок переписывает в <ya-kit-repeat of=…>,
            # а атом Repeat рисует блок, только если значение — число
            # (код движка: `!count || typeof count !== 'number'` -> ничего).
            # Объект/массив — NaN, строковый тип схемы — тоже пусто.
            if kind in ("object", "array") or node.get("type") not in (None, "number", "integer"):
                out.append(
                    _f(
                        "G16",
                        ERROR,
                        f"{{{{settings.{path}}}}}",
                        f"число повторов repeat/times, а по схеме это "
                        f"{node.get('type') or type_label} — атом Repeat рисует блок "
                        "только для числа, блок не отрисуется",
                        "объявить свойство числом (type: number) или повторять по длине: (length …)",
                    )
                )
        elif use[0] == "iterated":
            if kind != "array":
                out.append(
                    _f(
                        "G16",
                        ERROR,
                        f"{{{{settings.{path}}}}}",
                        f"путь итерируется (#each), но по схеме это {type_label}",
                        "объявить свойство массивом или убрать итерацию",
                    )
                )
        elif use[0] in ("text", "attr-str"):
            if kind in ("object", "array"):
                where = (
                    f"{{{{settings.{path}}}}} в тексте"
                    if use[0] == "text"
                    else f"{{{{settings.{path}}}}} внутри строки атрибута {use[2]}"
                )
                out.append(
                    _f(
                        "G16",
                        ERROR,
                        where,
                        f"по схеме это {type_label} — в скалярной позиции движок уронит "
                        "секцию, витрина отрисует пустоту без ошибки",
                        "использовать подполя модели или исправить тип в схеме",
                    )
                )
        elif use[0] == "attr":
            _, tag_name, attr_name = use
            type_raw = (tag_attrs.get(tag_name) or {}).get("attribute_types", {}).get(attr_name)
            attr_class = _attr_type_class(type_raw)
            if kind in ("object", "array"):
                if attr_class == "scalar":
                    out.append(
                        _f(
                            "G16",
                            ERROR,
                            f"<{tag_name} {attr_name}={{{{settings.{path}}}}}>",
                            f"по схеме это {type_label}, а атрибут документирован как "
                            f"`{type_raw}` — движок уронит секцию, витрина отрисует "
                            "пустоту без ошибки",
                            "передавать подполя модели или исправить тип в схеме",
                        )
                    )
                elif attr_class == "unknown":
                    out.append(
                        _f(
                            "G16",
                            WARNING,
                            f"<{tag_name} {attr_name}={{{{settings.{path}}}}}>",
                            f"по схеме это {type_label}, тип атрибута в доке не описан — "
                            "офлайн проверить нельзя",
                            "проверить в превью proposal-версии",
                        )
                    )
        elif use[0] == "with":
            if kind == "scalar":
                out.append(
                    _f(
                        "G16",
                        WARNING,
                        f"{{{{#with settings.{path}}}}}",
                        "with над скаляром отработает как пустой блок",
                        "передать объект или убрать with",
                    )
                )
    return out


# --- G20 -----------------------------------------------------------------

_VOID_HTML = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
_FEEDBACK_LISTING = "ya-kit-product-feedback-listing-controller"
_FEEDBACK_CHILDREN = (
    "ya-kit-product-feedback-media-list-controller",
    "ya-kit-product-feedback-viewer-controller",
)


def _tag_spans(model: tmpl_mod.TemplateModel) -> list[dict]:
    """Открывающие теги с позицией закрытия и предками (по вложенности тегов)."""
    spans: list[dict] = []
    stack: list[dict] = []
    for tag in model.tags:
        if tag.closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["tag"].name == tag.name:
                    stack[i]["end"] = tag.pos
                    del stack[i:]
                    break
            continue
        span = {
            "tag": tag,
            "start": tag.pos,
            "end": None,
            "ancestors": [s["tag"].name for s in stack],
        }
        spans.append(span)
        if not tag.self_closing and tag.name not in _VOID_HTML:
            stack.append(span)
    return spans


def _truthy_literal(value: str | None) -> bool:
    if value is None:
        return False
    v = value.strip()
    return v not in ("", "false", "{{false}}", "0", "{{0}}")


def g20_atom_semantics(model: tmpl_mod.TemplateModel) -> list[Finding]:
    """Семантика атомов, которую не видно по именам тегов и атрибутов.

    * feedback media-list / viewer вне listing-контроллера — атом бросает
      исключение, секция в проде исчезает целиком (ERROR);
    * `ya-kit-video` с `is-skinless`, но без `skin-variant="default"` — атом по
      умолчанию рисует компактный скин, а он `is-skinless` игнорирует: у фонового
      видео появятся контролы (WARNING);
    * литеральный `count` у `ya-kit-carousel`, внутри которой цикл, — карусель
      сама считает фактические слайды, явный `count` приоритетнее и при
      несовпадении с длиной ломает прокрутку и автоплей (WARNING).
    """
    out: list[Finding] = []
    spans = _tag_spans(model)
    loops = [m.pos for m in model.mustaches if m.kind == "block-open" and m.body[1:].split()[:1] == ["each"]]
    loops += [t.pos for t in model.tags if not t.closing and t.name == "ya-kit-each"]
    for span in spans:
        tag = span["tag"]
        attrs = dict(tag.attrs)
        if tag.name in _FEEDBACK_CHILDREN and _FEEDBACK_LISTING not in span["ancestors"]:
            out.append(
                _f(
                    "G20",
                    ERROR,
                    f"<{tag.name}>",
                    f"работает только внутри <{_FEEDBACK_LISTING}>: вне его атом бросает "
                    "исключение, и секция в проде исчезает целиком",
                    f"вложить тег внутрь <{_FEEDBACK_LISTING}>",
                )
            )
        if (
            tag.name == "ya-kit-video"
            and "is-skinless" in attrs
            and _truthy_literal(attrs.get("is-skinless"))
            and (attrs.get("skin-variant") or "").strip() != "default"
        ):
            out.append(
                _f(
                    "G20",
                    WARNING,
                    "<ya-kit-video is-skinless>",
                    "по умолчанию у атома компактный скин, а он игнорирует is-skinless "
                    "(и hidden-controls): у фонового видео появятся кнопки и прогресс",
                    'добавить skin-variant="default"',
                )
            )
        if tag.name == "ya-kit-carousel":
            count = attrs.get("count")
            if count is not None and "{{" not in count:
                end = span["end"] if span["end"] is not None else float("inf")
                if any(span["start"] < pos < end for pos in loops):
                    out.append(
                        _f(
                            "G20",
                            WARNING,
                            f'<ya-kit-carousel count="{count}">',
                            "слайды внутри генерирует цикл, а count задан числом: карусель "
                            "сама считает фактические слайды, явный count приоритетнее и при "
                            "несовпадении ломает прокрутку и автоплей",
                            "убрать count или задать длиной массива: count=\"{{length …}}\"",
                        )
                    )
    return out


# --- G21 -----------------------------------------------------------------


def _settings_value(settings: dict, path: str) -> object:
    node: object = settings
    for key in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _alt_pairs(settings: dict, image_path: str, alt_path: str):
    """Пары (изображение, где, alt, где) для пары путей из alt_fields.

    Путь с `[]` (`modules[].image=modules[].imageAlt`) раскрывается по элементам
    массива: у каждого элемента своё изображение и свой alt.
    """
    if "[]." not in image_path:
        yield (
            _settings_value(settings, image_path),
            image_path,
            _settings_value(settings, alt_path),
            alt_path,
        )
        return
    array, _, image_rest = image_path.partition("[].")
    _, _, alt_rest = alt_path.partition("[].")
    items = _settings_value(settings, array)
    if not isinstance(items, list):
        return
    for i, item in enumerate(items):
        if isinstance(item, dict):
            yield (
                _settings_value(item, image_rest),
                f"{array}[{i}].{image_rest}",
                _settings_value(item, alt_rest),
                f"{array}[{i}].{alt_rest}",
            )


def g21_image_alt(
    pattern: Pattern, settings: dict, settings_name: str = "settings.json", mode: str = "lint"
) -> list[Finding]:
    """Изображение паттерна задано — его alt не пуст.

    Пары «изображение → поле alt» объявляет сам паттерн (`alt_fields` в
    pattern.yaml), в том числе у элементов `modules` (`modules[].image=
    modules[].imageAlt`). Запасной alt шаблона (часто заголовок блока) картинку
    не описывает: экранный диктор прочтёт заголовок второй раз. ERROR при сборке
    (`new`/`compose`) и при линте её вывода, WARNING у чужого экспорта: живая
    секция со старыми настройками без поля alt рендерится, но без описания.
    """
    by_alt: dict[str, list[str]] = {}
    alt_values: dict[str, object] = {}
    for image_path, alt_path in pattern.alt_fields:
        for image, image_where, alt, alt_where in _alt_pairs(settings, image_path, alt_path):
            file_path = image.get("filePath") if isinstance(image, dict) else None
            if isinstance(file_path, str) and file_path.strip():
                by_alt.setdefault(alt_where, []).append(image_where)
                alt_values[alt_where] = alt
    severity = ERROR if mode in ("new", "compose") else WARNING
    out: list[Finding] = []
    for alt_path, images in by_alt.items():
        alt = alt_values[alt_path]
        if isinstance(alt, str) and alt.strip():
            continue
        out.append(
            _f(
                "G21",
                severity,
                f"{settings_name}:{alt_path}",
                f"задано изображение ({', '.join(images)}), а {alt_path} пуст: у картинки нет "
                "описания для экранного диктора и поиска (запасной alt шаблона её не описывает)",
                f"заполнить {alt_path} одной фразой: что на изображении",
            )
        )
    return out


# --- G22 -----------------------------------------------------------------

_FIXED_COLOR_STYLES = ("hardcoded-color", "multicolor")


def _icon_enums(node: object, path: str, icons: set[str]):
    """(путь, значения) у enum схемы, все значения которого — имена иконок."""
    if isinstance(node, dict):
        enum = node.get("enum")
        if isinstance(enum, list):
            names = [v for v in enum if isinstance(v, str) and v not in ("", "none", "auto")]
            if len(names) >= 2 and all(v in icons for v in names):
                yield path or "<root>", names
        for key in ("properties",):
            props = node.get(key)
            if isinstance(props, dict):
                for name, sub in props.items():
                    yield from _icon_enums(sub, f"{path}.{name}" if path else name, icons)
        if isinstance(node.get("items"), dict):
            yield from _icon_enums(node["items"], f"{path}[]", icons)


def g22_icon_styles(model: tmpl_mod.TemplateModel, schema: dict, registry: dict) -> list[Finding]:
    """Иконки разного стиля в одной секции: зашитый цвет (hardcoded-color,
    multicolor) рядом с иконками цвета текста. Зашитая иконка не следует палитре —
    на тёмной палитре тёмная не видна, а синяя печать выглядит чужой рядом с
    контурными. Стиль — по SVG иконки (registry icon_styles, tags.md)."""
    styles = registry.get("icon_styles") or {}
    if not styles:
        return []
    used: dict[str, str] = {}
    for tag in model.tags:
        if tag.closing or tag.name != "ya-kit-icon":
            continue
        name = (dict(tag.attrs).get("name") or "").strip()
        if name and "{{" not in name:
            used.setdefault(name, "html")
    for where, names in _icon_enums(schema, "", set(styles)):
        for name in names:
            used.setdefault(name, f"json_schema:{where}")
    fixed = sorted(n for n in used if styles.get(n) in _FIXED_COLOR_STYLES)
    rest = sorted(n for n in used if n in styles and styles.get(n) not in _FIXED_COLOR_STYLES)
    if not fixed or not rest:
        return []
    return [
        _f(
            "G22",
            WARNING,
            ", ".join(sorted({used[n] for n in fixed})),
            "иконки разного стиля: "
            + ", ".join(f"{n} ({styles[n]})" for n in fixed)
            + f" рядом с иконками цвета текста ({', '.join(rest[:4])}{'…' if len(rest) > 4 else ''})"
            + " — зашитый цвет не следует палитре",
            "взять иконку цвета текста (outline или solid в tags.md) или убрать зашитую из enum",
        )
    ]

