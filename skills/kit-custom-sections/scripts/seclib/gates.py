"""Гейты валидации G1–G15. Единый формат находки: code/severity/where/what/fix.

Итерация 1 реализует G1, G2, G4, G5, G6, G7, G11, G12 (+G3 в мягкой форме).
G8/G10/G14/G15 — итерация 2 (adopt/check-settings/freshness), заглушки честно
говорят об этом в отчёте doctor.
"""

from __future__ import annotations

import re

from . import css as css_mod
from . import schema as schema_mod
from . import tmpl as tmpl_mod
from .catalog import Pattern
from .common import load_definitions

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
) -> list[Finding]:
    findings: list[Finding] = []
    model = tmpl_mod.parse(html, registry)
    definitions = load_definitions()

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
    if pattern is not None:
        findings += g11_pattern_conformance(html, css, pattern)
    findings += g12_alias(meta, mode)
    if isinstance(settings, dict):
        findings += g8_settings(schema, settings, settings_name, definitions)
    findings += g16_type_usage(model, schema, registry, definitions)
    findings += g17_each_alias(model)
    findings += g18_css_external(css, registry)
    findings += g19_headings(model)
    if strict:
        findings = [
            _f(f["code"], ERROR, f["where"], f["what"], f["fix"]) if f["severity"] == WARNING else f
            for f in findings
        ]
    return findings


# --- G1 ------------------------------------------------------------------


def g1_structure(model: tmpl_mod.TemplateModel, registry: dict) -> list[Finding]:
    out: list[Finding] = []
    for err in model.structure_errors:
        out.append(_f("G1", ERROR, "html", err, "исправить структуру блоков/скобок"))
    for warn in model.structure_warnings:
        out.append(_f("G1", WARNING, "html", warn, "убрать конструкцию"))
    for err in model.block_in_attr:
        out.append(_f("G1", ERROR, "html", err, "вынести блочный хелпер из атрибута"))
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
                    "контроллера нет в реестре 36 — данные не придут, секция молча опустеет",
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
                    "тега нет в реестре 94 — движок молча пропустит, секция опустеет",
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


def g3_attributes(
    model: tmpl_mod.TemplateModel, registry: dict, allow_unknown: bool
) -> list[Finding]:
    out: list[Finding] = []
    tag_attrs = registry.get("tag_attributes", {})
    for tag in model.tags:
        if tag.closing or not tag.name.startswith(("ya-kit-", "yandex-pay-")):
            continue
        info = tag_attrs.get(tag.name)
        if info is None:
            continue
        documented = set(info["attributes"])
        for attr, _value in tag.attrs:
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
            if not documented or not info["documented"]:
                continue  # у тега нет таблицы в доке — не вина автора секции
            if attr not in documented:
                if allow_unknown:
                    continue
                # WARNING, не ERROR: таблицы доки доказуемо неполны — официальный
                # пример FAQ использует align у ya-kit-section-title, которого в
                # таблице нет. --strict поднимает до ERROR.
                out.append(
                    _f(
                        "G3",
                        WARNING,
                        f"<{tag.name} {attr}>",
                        "атрибут не документирован для этого тега (таблицы доки неполны)",
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
    for path in html_paths:
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

    ref_whitelist = (
        set(registry["reserved_refs"])
        | set(registry["service_refs"])
        | set(registry.get("renamed_refs", {}))
        | schema_mod.local_definition_refs(schema)
    )
    for where, ref in schema_mod.collect_refs(schema):
        if ref.startswith("#/definitions/partials/"):
            continue
        if ref not in ref_whitelist:
            out.append(
                _f(
                    "G5",
                    ERROR,
                    f"json_schema:{where}",
                    f"$ref {ref} вне белого списка (20 резервных + 5 служебных + partials + "
                    "собственные определения)",
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
                        "инлайновый обработчик со строковым значением",
                        "убрать: JS в кастомной секции не исполняется",
                    )
                )
            if attr.lower() == "href" and value and _JS_HREF.match(value):
                out.append(
                    _f("G7", ERROR, f"<{tag.name} href>", "javascript: в ссылке", "убрать")
                )
    return out


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


# --- G11 -----------------------------------------------------------------


def g11_pattern_conformance(html: str, css: str, pattern: Pattern) -> list[Finding]:
    out: list[Finding] = []
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


def g12_alias(meta: dict | None, mode: str = "lint") -> list[Finding]:
    # Строгость по режиму (та же развилка, что у хардкода цветов G6): в наших
    # детерминированных режимах непустой alias — ERROR; при линте чужого экспорта —
    # WARNING: на витринах alias у системных шаблонов бывает проставлен осознанно
    # (подмена шапки/подвала/карточки — решение владельца, не ошибка автора секции).
    out: list[Finding] = []
    if not meta:
        return out
    severity = ERROR if mode in ("new", "compose", "pattern") else WARNING
    alias = meta.get("alias", "")
    if alias in ("YandexKit.Header", "YandexKit.Footer"):
        out.append(
            _f(
                "G12",
                severity,
                "alias",
                f"alias={alias} подменяет шапку/подвал всей витрины",
                "для новой кастомной секции alias всегда пуст",
            )
        )
    elif alias:
        out.append(
            _f(
                "G12",
                severity,
                "alias",
                f"alias={alias!r} непуст — это подмена системного блока витрины",
                "для новой кастомной секции alias всегда пуст",
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


def g19_headings(model: tmpl_mod.TemplateModel) -> list[Finding]:
    """Дисциплина заголовков шаблона: не больше одного h1, уровни без дыр.

    `ya-kit-section-title` рендерит h3; если паттерны берут другие уровни,
    дерево заголовков страницы прыгает (h1-h3-h2-h3). Правило каталога: заголовки секций — уровень платформенного атома (h3),
    подзаголовки плиток — h4; h1 — только hero.
    """
    levels: list[int] = []
    for tag in model.tags:
        if tag.closing:
            continue
        m = _HEADING_TAG.match(tag.name)
        if m:
            levels.append(int(m.group(1)))
            continue
        if tag.name == "ya-kit-section-title":
            levels.append(3)  # платформенный атом рендерит h3
        elif tag.name == "ya-kit-text":
            as_value = dict(tag.attrs).get("as") or ""
            m2 = _HEADING_TAG.match(as_value)
            if m2:
                levels.append(int(m2.group(1)))
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
    unique = sorted(set(levels))
    for prev, cur in zip(unique, unique[1:]):
        if cur - prev > 1:
            out.append(
                _f(
                    "G19",
                    WARNING,
                    "html",
                    f"уровни заголовков шаблона прыгают: h{prev} -> h{cur} без h{prev + 1}",
                    "выровнять лестницу уровней (секция h3, плитки h4)",
                )
            )
    return out


# --- G16 -----------------------------------------------------------------

_MODEL_TOKEN = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")


def _attr_type_class(type_raw: str | None) -> str:
    """Класс документированного типа атрибута: scalar | model | unknown."""
    if not type_raw:
        return "unknown"
    return "model" if _MODEL_TOKEN.search(type_raw) else "scalar"


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

        if use[0] == "iterated":
            if kind != "array":
                out.append(
                    _f(
                        "G16",
                        ERROR,
                        f"{{{{settings.{path}}}}}",
                        f"путь итерируется (#each/times), но по схеме это {type_label}",
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
