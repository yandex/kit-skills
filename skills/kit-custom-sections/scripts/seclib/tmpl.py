"""Структурный разбор шаблона секции: теги, мустачи, блоки, пути settings.

Это НЕ порт движка (~1900 строк TS у платформы): собственный токенайзер, чьё
расхождение с движком контролируется мета-гейтом G0 на официальных примерах.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MUSTACHE = re.compile(r"\{\{(.*?)\}\}", re.S)
TAG = re.compile(
    r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\"[^\"]*\"|'[^']*'|\{\{.*?\}\}|[^<>\"'])*?)(/?)>",
    re.S,
)
# Значение без кавычек, начатое мустачей, читается до её `}}` целиком
# (`to={{get settings "url"}}`), иначе пробел внутри мустачи порождал
# фантомные голые атрибуты `settings` и `"url"}}`.
ATTR = re.compile(
    r"([a-zA-Z_@:][\w:.@-]*)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|\{\{.*?\}\}[^\s>]*|[^\s>]+))?",
    re.S,
)
KEYWORDS = {"this", "true", "false", "null", "undefined", "else"}
# Корни контекста шаблона, кроме settings (он разбирается отдельно).
# isLCP — булев флаг на корне контекста (с 27.10.2026, раньше section.isLCP).
CONTEXT_ROOTS = {"globals", "route", "helpers", "platform", "clientType", "isLCP"}
BLOCK_ALIAS = re.compile(r"\bas\s+\|([^|]+)\|")
# Части `as |item, index|` движок режет по пробелам И запятым.
ALIAS_PARTS = re.compile(r"[\s,]+")
# `{{elseif cond}}` и `{{else if cond}}` — одна и та же ветка elseif движка.
ELSEIF = re.compile(r"^else\s*if(?:\s+|$)")
# Имя локали по умолчанию: индекс у each, счётчик у repeat/times без `as`.
DEFAULT_INDEX = "index"
# Токены выражения: скобки, строки в кавычках (целиком, с пробелами), прочее.
EXPR_TOKEN = re.compile(r"\(|\)|\"[^\"]*\"|'[^']*'|[^\s()]+")
# Хелперы, которые возвращают подмножество элементов того же массива: у
# `{{#each (filterBy settings.modules …) as |m|}}` поля `m.*` — это поля
# `settings.modules[]`. Остальные хелперы меняют форму элемента (mapBy, split,
# filterImages…), и поля алиаса со схемой секции не сверяются.
SHAPE_PRESERVING_HELPERS = {"filterBy", "slice"}
# Хелперы, которые читают поле по строковому ключу: `mapBy settings.modules
# 'product.variantId'` употребляет settings.modules[].product.variantId.
# Значение — как ключ приклеивается к пути аргумента.
KEY_PATH_HELPERS = {"get": ".", "mapBy": "[].", "filterBy": "[].", "findBy": "[]."}
PATH_TOKEN = re.compile(r"[A-Za-z_$][\w$]*(\.[\w$]+)*")


@dataclass
class Mustache:
    pos: int
    body: str
    kind: str  # expr | block-open | block-close | else | elseif | comment | partial | raw-error


@dataclass
class TagUse:
    pos: int
    name: str
    attrs: list[tuple[str, str | None]]
    closing: bool
    self_closing: bool


@dataclass
class TemplateModel:
    mustaches: list[Mustache] = field(default_factory=list)
    tags: list[TagUse] = field(default_factory=list)
    settings_paths: set[str] = field(default_factory=set)
    # Пути settings, прочитанные хелпером по строковому ключу (KEY_PATH_HELPERS):
    # для G4 это употребление, но не путь шаблона.
    settings_key_paths: set[str] = field(default_factory=set)
    # (тег, атрибут, значение): значение-мустача без кавычек с пробелом внутри.
    unquoted_mustache_attrs: list[tuple[str, str, str]] = field(default_factory=list)
    # Пары (путь, способ употребления) для G16. Способы:
    #   ("text",)                — текстовый узел, значение строкуется
    #   ("attr", тег, атрибут)   — весь атрибут = один путь
    #   ("attr-str", тег, атрибут) — путь вклеен в строку атрибута
    #   ("iterated",)            — база each
    #   ("count",)               — число повторов repeat/times
    #   ("with",)                — база with
    #   ("helper",)              — аргумент хелпера, семантика неизвестна
    settings_uses: list[tuple[str, tuple]] = field(default_factory=list)
    # тег контроллера -> пути под его алиасом; ключ `?<корень>` — корень, который
    # не объявлен ни контекстом, ни алиасом (гейт G10)
    alias_paths: dict[str, set[str]] = field(default_factory=dict)
    # корень -> номера строк, где он прочитан без объявления (гейт G10)
    unresolved_roots: dict[str, list[int]] = field(default_factory=dict)
    # имена на месте хелпера (с аргументами), которых нет среди инлайн-хелперов
    # справочника: движок разрешит их как путь (гейт G10 говорит про хелпер)
    unknown_call_heads: set[str] = field(default_factory=set)
    partial_refs: set[str] = field(default_factory=set)
    structure_errors: list[str] = field(default_factory=list)
    structure_warnings: list[str] = field(default_factory=list)
    triple_stache: bool = False
    block_in_attr: list[str] = field(default_factory=list)
    each_without_alias: list[str] = field(default_factory=list)  # G17


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _strip_quotes(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _extract_paths(expr: str, inline_helpers: set[str]) -> list[str]:
    """Пути данных из выражения мустачи (без # и /).

    Имя хелпера — это не путь: и в начале выражения, и сразу после `(`
    подвыражения (`{{when (isEmpty x) "…" x}}`).
    """
    paths: list[str] = []
    call_position = True  # начало выражения или сразу после "("
    for tok in EXPR_TOKEN.findall(expr):
        if tok == "(":
            call_position = True
            continue
        if tok == ")":
            call_position = False
            continue
        at_call = call_position
        call_position = False
        if tok.startswith(("'", '"')) or re.fullmatch(r"-?\d+(\.\d+)?", tok):
            continue
        if "=" in tok:  # hash-аргумент helper key=value
            tok = tok.split("=", 1)[1]
            if tok.startswith(("'", '"')) or re.fullmatch(r"-?\d+(\.\d+)?", tok):
                continue
        if not re.fullmatch(r"[A-Za-z_$][\w$]*(\.[\w$]+)*", tok):
            continue
        if at_call and tok in inline_helpers:
            continue
        root = tok.split(".", 1)[0]
        if root in KEYWORDS:
            continue
        paths.append(tok)
    return paths


def _unknown_call_heads(expr: str, inline_helpers: set[str], skip_first: bool = False) -> set[str]:
    """Простые имена на месте вызова хелпера (начало выражения или сразу после
    `(`) с хотя бы одним аргументом, которых нет среди инлайн-хелперов.

    `skip_first` — для аргументов блочного хелпера: первый токен там путь.
    """
    toks = EXPR_TOKEN.findall(expr)
    out: set[str] = set()
    for i, tok in enumerate(toks):
        at_call = (i == 0 and not skip_first) or (i > 0 and toks[i - 1] == "(")
        if not at_call or tok in inline_helpers or tok in KEYWORDS:
            continue
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", tok):
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is None or nxt == ")":
            continue
        out.add(tok)
    return out


def _key_path_uses(expr: str, inline_helpers: set[str]) -> list[str]:
    """`helper path 'key'` для KEY_PATH_HELPERS -> ['path<склейка>key', …]."""
    toks = EXPR_TOKEN.findall(expr)
    out: list[str] = []
    for i, tok in enumerate(toks[:-2]):
        glue = KEY_PATH_HELPERS.get(tok)
        if glue is None or tok not in inline_helpers or (i > 0 and toks[i - 1] != "("):
            continue
        path, key = toks[i + 1], toks[i + 2]
        if not PATH_TOKEN.fullmatch(path) or key[:1] not in ("'", '"'):
            continue
        key = key[1:-1].strip()
        if key and PATH_TOKEN.fullmatch(key):
            out.append(path + glue + key)
    return out


def _leading_helper(expr: str, inline_helpers: set[str]) -> str | None:
    """Имя хелпера, если выражение — вызов хелпера (`f a b` или `(f a b)`)."""
    for tok in EXPR_TOKEN.findall(expr):
        if tok == "(":
            continue
        return tok if tok in inline_helpers else None
    return None


def parse(text: str, registry: dict) -> TemplateModel:
    model = TemplateModel()
    inline_helpers = {e["name"] for e in registry["helpers"]["inline"]}
    block_helpers = {d["helper"]: d for d in registry["helpers"]["block"]}
    each_like_tags = {d["openTag"]: d for d in registry["helpers"]["block"]}

    if "{{{" in text:
        model.triple_stache = True
        model.structure_errors.append(
            f"строка {_line(text, text.index('{{{'))}: тройные скобки {{{{{{ }}}}}} "
            "не поддерживаются движком"
        )

    events: list[tuple[int, str, object]] = []

    for m in MUSTACHE.finditer(text):
        body = m.group(1).strip()
        if body.startswith("!"):
            kind = "comment"
            model.structure_warnings.append(
                f"строка {_line(text, m.start())}: комментарий {{{{!-- --}}}} "
                "печатается на витрине как текст — удалить"
            )
        elif body.startswith("#"):
            kind = "block-open"
        elif body.startswith("/"):
            kind = "block-close"
        elif ELSEIF.match(body):
            kind = "elseif"
        elif body == "else" or body.startswith("else "):
            kind = "else"
        elif body.startswith(">"):
            kind = "partial"
            name = body[1:].strip().split()[0] if body[1:].strip() else ""
            model.partial_refs.add(name)
        else:
            kind = "expr"
        mus = Mustache(pos=m.start(), body=body, kind=kind)
        model.mustaches.append(mus)
        events.append((m.start(), "mustache", mus))

    tag_spans: list[tuple[int, int]] = []
    for m in TAG.finditer(text):
        closing = m.group(1) == "/"
        attrs = []
        if not closing:
            for am in ATTR.finditer(m.group(3)):
                raw = am.group(2)
                if raw and raw.startswith("{{") and re.search(r"\s", raw):
                    model.unquoted_mustache_attrs.append((m.group(2), am.group(1), raw))
                attrs.append((am.group(1), _strip_quotes(raw)))
        tag = TagUse(
            pos=m.start(),
            name=m.group(2),
            attrs=attrs,
            closing=closing,
            self_closing=m.group(4) == "/",
        )
        model.tags.append(tag)
        tag_spans.append((m.start(), m.end()))
        events.append((m.start(), "tag", tag))
        if not closing:
            for attr_name, attr_value in attrs:
                if attr_value and "{{#" in attr_value:
                    model.block_in_attr.append(
                        f"строка {_line(text, m.start())}: блочный хелпер внутри значения "
                        f"атрибута {attr_name} тега <{tag.name}> не работает"
                    )

    events.sort(key=lambda e: e[0])

    # Линейный проход: скоупы блоков ({{#each}} и теговая форма <ya-kit-each as=…>)
    # и скоупы алиасов контроллеров (<…-controller as="alias">).
    # {kind, name, aliases:{alias: base}, spread: bool}. spread — `with` без `as`:
    # движок раскладывает поля объекта в скоуп, их имена офлайн неизвестны.
    scope_stack: list[dict] = []
    controller_stack: list[dict] = []  # {tag, alias, depth}
    tag_depth: dict[str, int] = {}
    open_tag_stack: list[str] = []
    current_pos = 0  # позиция разбираемого события — для строк находок G10

    def resolve(path: str, use: tuple = ("helper",), key_only: bool = False) -> None:
        root, _, rest = path.partition(".")
        for scope in reversed(scope_stack):
            aliases = scope["aliases"]
            if root in aliases:
                base = aliases[root]
                if base is None:  # index-алиас
                    return
                resolve(base + ("." + rest if rest else ""), use, key_only)
                return
        if root == "settings":
            if key_only:
                if rest:
                    model.settings_key_paths.add(rest)
                return
            if rest:
                model.settings_paths.add(rest)
                model.settings_uses.append((rest, use))
            return
        if key_only:
            return
        for ctl in reversed(controller_stack):
            if root == ctl["alias"]:
                model.alias_paths.setdefault(ctl["tag"], set()).add(rest)
                return
        if root in CONTEXT_ROOTS:
            return
        spread = next((scope for scope in reversed(scope_stack) if scope.get("spread")), None)
        if spread is not None:
            # `{{#with x}}` без `as` раскладывает поля x в скоуп: у x из settings
            # это путь x.<поле>, у прочих объектов имя поля офлайн не проверить
            if spread.get("base"):
                resolve(spread["base"] + "." + path, use, key_only)
            return
        # Неизвестный корень: алиас, которого нет в скоупе, или опечатка —
        # решает гейт G10, здесь только собираем.
        model.alias_paths.setdefault(f"?{root}", set()).add(rest)
        lines = model.unresolved_roots.setdefault(root, [])
        line = _line(text, current_pos)
        if line not in lines:
            lines.append(line)

    def _in_tag_span(pos: int) -> bool:
        return any(start <= pos < end for start, end in tag_spans)

    def _resolve_expr(expr: str, scalar_use: tuple) -> None:
        """Выражение мустачи: голый путь -> scalar_use, вызов хелпера -> helper."""
        model.unknown_call_heads |= _unknown_call_heads(expr, inline_helpers)
        for key_path in _key_path_uses(expr, inline_helpers):
            resolve(key_path, key_only=True)
        tokens = [t for t in re.split(r"\s+", expr.strip()) if t]
        paths = _extract_paths(expr, inline_helpers)
        if len(tokens) == 1 and len(paths) == 1 and paths[0] == tokens[0]:
            resolve(paths[0], scalar_use)
        else:
            for p in paths:
                resolve(p, ("helper",))

    for pos, kind, obj in events:
        current_pos = pos
        if kind == "mustache":
            mus = obj
            if mus.kind == "block-open":
                body = mus.body[1:].strip()
                name = body.split()[0] if body.split() else ""
                helper = block_helpers.get(name)
                if helper is None:
                    model.structure_errors.append(
                        f"строка {_line(text, pos)}: неизвестный блочный хелпер "
                        f"{{{{#{name}}}}} — движок бросит исключение, секция упадёт"
                    )
                aliases: dict[str, str | None] = {}
                arg_expr = re.sub(BLOCK_ALIAS, " ", body[len(name):])
                arg_paths = _extract_paths(arg_expr, inline_helpers)
                model.unknown_call_heads |= _unknown_call_heads(
                    arg_expr, inline_helpers, skip_first=True
                )
                for key_path in _key_path_uses(arg_expr, inline_helpers):
                    resolve(key_path, key_only=True)
                # Вызов хелпера вместо пути (`#each (filterBy settings.x …)`):
                # итерируется результат хелпера, а не его аргумент.
                call = _leading_helper(arg_expr, inline_helpers)
                keeps_shape = call is None or call in SHAPE_PRESERVING_HELPERS
                for i, p in enumerate(arg_paths):
                    if i == 0 and keeps_shape and name == "each":
                        resolve(p, ("iterated",))
                    elif i == 0 and call is None and name in ("times", "repeat"):
                        resolve(p, ("count",))
                    elif i == 0 and call is None and name == "with":
                        resolve(p, ("with",))
                    else:
                        resolve(p, ("helper",))
                alias_m = BLOCK_ALIAS.search(body)
                base = None
                if arg_paths and keeps_shape and not (call and name == "with"):
                    first = arg_paths[0]
                    base = _rebase(first, scope_stack)
                parts = [p for p in ALIAS_PARTS.split(alias_m.group(1)) if p] if alias_m else []
                spread = False
                if parts:
                    if name in ("each",):
                        aliases[parts[0]] = (base + "[]") if base else None
                    elif name in ("with",):
                        aliases[parts[0]] = base
                    else:  # repeat/times: алиас — индекс
                        aliases[parts[0]] = None
                    if len(parts) > 1:
                        aliases[parts[1]] = None
                elif name == "each":
                    aliases["this"] = (base + "[]") if base else None
                    model.each_without_alias.append(
                        f"строка {_line(text, pos)}: {{{{#each …}}}} без `as |m|`"
                    )
                elif name == "with":
                    spread = True
                if name == "each" and len(parts) < 2:
                    aliases.setdefault(DEFAULT_INDEX, None)
                elif name in ("repeat", "times") and not parts:
                    aliases[DEFAULT_INDEX] = None
                scope_stack.append(
                    {
                        "kind": "mustache",
                        "name": name,
                        "aliases": aliases,
                        "spread": spread,
                        "base": base if spread else None,
                    }
                )
            elif mus.kind == "block-close":
                name = mus.body[1:].strip()
                if not scope_stack or scope_stack[-1]["kind"] != "mustache":
                    model.structure_errors.append(
                        f"строка {_line(text, pos)}: закрытие {{{{/{name}}}}} без открытия"
                    )
                elif scope_stack[-1]["name"] != name:
                    model.structure_errors.append(
                        f"строка {_line(text, pos)}: {{{{/{name}}}}} закрывает "
                        f"{{{{#{scope_stack[-1]['name']}}}}} — блоки перепутаны"
                    )
                    scope_stack.pop()
                else:
                    scope_stack.pop()
            elif mus.kind == "else":
                encl = next(
                    (s for s in reversed(scope_stack) if s["kind"] == "mustache"), None
                )
                if encl is None:
                    model.structure_errors.append(
                        f"строка {_line(text, pos)}: {{{{else}}}} вне блочного хелпера"
                    )
                else:
                    helper = block_helpers.get(encl["name"])
                    if helper is not None and not helper.get("supportsElse"):
                        model.structure_errors.append(
                            f"строка {_line(text, pos)}: {{{{else}}}} внутри "
                            f"{{{{#{encl['name']}}}}}, который else не поддерживает"
                        )
            elif mus.kind == "elseif":
                # Условие ветки читается как аргумент хелпера, так же как у
                # `{{#if cond}}`: иначе путь только из else-ветки выглядел бы мёртвой
                # настройкой, а опечатка в нём проходила бы G4. Слово elseif — не путь.
                cond = ELSEIF.sub("", mus.body, count=1)
                for key_path in _key_path_uses(cond, inline_helpers):
                    resolve(key_path, key_only=True)
                for p in _extract_paths(cond, inline_helpers):
                    resolve(p, ("helper",))
            elif mus.kind == "expr":
                if not _in_tag_span(pos):  # мустачи в атрибутах обрабатывает ветка тегов
                    _resolve_expr(mus.body, ("text",))
        else:
            tag = obj
            if tag.closing:
                if open_tag_stack and open_tag_stack[-1] == tag.name:
                    open_tag_stack.pop()
                if scope_stack and scope_stack[-1].get("tag") == tag.name and (
                    tag_depth.get(tag.name, 0) == scope_stack[-1].get("depth")
                ):
                    scope_stack.pop()
                if controller_stack and controller_stack[-1]["tag"] == tag.name and (
                    tag_depth.get(tag.name, 0) == controller_stack[-1]["depth"]
                ):
                    controller_stack.pop()
                tag_depth[tag.name] = max(0, tag_depth.get(tag.name, 0) - 1)
                continue

            attrs = dict(tag.attrs)
            helper_def = each_like_tags.get(tag.name)
            iter_attr = helper_def.get("openAttr") if helper_def and helper_def["helper"] in ("each", "times", "repeat") else None
            iter_use: tuple = (
                ("iterated",) if helper_def and helper_def["helper"] == "each" else ("count",)
            )
            with_attr = helper_def.get("openAttr") if helper_def and helper_def["helper"] == "with" else None
            for attr_name, attr_value in tag.attrs:
                if not attr_value:
                    continue
                inner = list(MUSTACHE.finditer(attr_value))
                whole = (
                    len(inner) == 1
                    and inner[0].start() == 0
                    and inner[0].end() == len(attr_value)
                )
                for m2 in inner:
                    body2 = m2.group(1).strip()
                    if body2.startswith(("#", "/", "!", ">")) or ELSEIF.match(body2):
                        continue
                    if attr_name == iter_attr:
                        _resolve_expr(body2, iter_use)
                    elif attr_name == with_attr:
                        _resolve_expr(body2, ("with",))
                    elif whole:
                        _resolve_expr(body2, ("attr", tag.name, attr_name))
                    else:
                        _resolve_expr(body2, ("attr-str", tag.name, attr_name))

            if not tag.self_closing:
                tag_depth[tag.name] = tag_depth.get(tag.name, 0) + 1
                open_tag_stack.append(tag.name)

            if tag.name in each_like_tags and not tag.self_closing:
                helper = each_like_tags[tag.name]
                if helper["helper"] == "each" and not attrs.get(helper.get("aliasAttr") or "as"):
                    model.each_without_alias.append(
                        f"строка {_line(text, pos)}: <{tag.name}> без атрибута "
                        f"{helper.get('aliasAttr') or 'as'}"
                    )
                of_value = attrs.get(helper.get("openAttr", "of"))
                base = None
                if of_value:
                    inner = MUSTACHE.search(of_value)
                    if inner:
                        call = _leading_helper(inner.group(1), inline_helpers)
                        keeps_shape = call is None or (
                            call in SHAPE_PRESERVING_HELPERS and helper["helper"] != "with"
                        )
                        cand = _extract_paths(inner.group(1), inline_helpers)
                        if cand and keeps_shape:
                            base = _rebase(cand[0], scope_stack)
                alias = attrs.get(helper.get("aliasAttr") or "as")
                aliases = {}
                if alias:
                    if helper["helper"] == "each":
                        aliases[alias] = (base + "[]") if base else None
                    elif helper["helper"] == "with":
                        aliases[alias] = base
                    else:
                        aliases[alias] = None
                index_alias = attrs.get(helper.get("indexAttr") or "index")
                if index_alias:
                    aliases[index_alias] = None
                if helper["helper"] == "each" and not index_alias:
                    aliases.setdefault(DEFAULT_INDEX, None)
                elif helper["helper"] in ("repeat", "times") and not alias:
                    aliases[DEFAULT_INDEX] = None
                scope_stack.append(
                    {
                        "kind": "tag",
                        "tag": tag.name,
                        "depth": tag_depth.get(tag.name, 0),
                        "name": helper["helper"],
                        "aliases": aliases,
                        "spread": helper["helper"] == "with" and not alias,
                        "base": base if helper["helper"] == "with" and not alias else None,
                    }
                )

            if tag.name.endswith("-controller") and not tag.self_closing:
                alias = attrs.get("as") or "controller"
                controller_stack.append(
                    {"tag": tag.name, "alias": alias, "depth": tag_depth.get(tag.name, 0)}
                )

    for scope in scope_stack:
        if scope["kind"] == "mustache":
            model.structure_errors.append(
                f"блок {{{{#{scope['name']}}}}} не закрыт"
            )
    return model


def _rebase(path: str, scope_stack: list[dict]) -> str | None:
    """Полный settings-путь (с префиксом `settings.`) для базы блока, или None."""
    root, _, rest = path.partition(".")
    for scope in reversed(scope_stack):
        if root in scope["aliases"]:
            base = scope["aliases"][root]
            if base is None:
                return None
            return base + ("." + rest if rest else "")
    if root == "settings":
        return path
    return None
