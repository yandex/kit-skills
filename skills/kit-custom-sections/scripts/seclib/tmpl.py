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
ATTR = re.compile(
    r"([a-zA-Z_@:][\w:.@-]*)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?"
)
KEYWORDS = {"this", "true", "false", "null", "undefined", "else"}
CONTEXT_ROOTS = {"globals", "route", "helpers"}
BLOCK_ALIAS = re.compile(r"\bas\s+\|([^|]+)\|")


@dataclass
class Mustache:
    pos: int
    body: str
    kind: str  # expr | block-open | block-close | else | comment | partial | raw-error


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
    # Пары (путь, способ употребления) для G16. Способы:
    #   ("text",)                — текстовый узел, значение строкуется
    #   ("attr", тег, атрибут)   — весь атрибут = один путь
    #   ("attr-str", тег, атрибут) — путь вклеен в строку атрибута
    #   ("iterated",)            — база each/times
    #   ("with",)                — база with
    #   ("helper",)              — аргумент хелпера, семантика неизвестна
    settings_uses: list[tuple[str, tuple]] = field(default_factory=list)
    alias_paths: dict[str, set[str]] = field(default_factory=dict)  # алиас контроллера -> пути
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
    """Пути данных из выражения мустачи (без # и /)."""
    expr = expr.replace("(", " ").replace(")", " ")
    tokens = [t for t in re.split(r"\s+", expr.strip()) if t]
    paths: list[str] = []
    for i, tok in enumerate(tokens):
        if tok.startswith(("'", '"')) or re.fullmatch(r"-?\d+(\.\d+)?", tok):
            continue
        if "=" in tok:  # hash-аргумент helper key=value
            tok = tok.split("=", 1)[1]
            if tok.startswith(("'", '"')) or re.fullmatch(r"-?\d+(\.\d+)?", tok):
                continue
        if not re.fullmatch(r"[A-Za-z_$][\w$]*(\.[\w$]+)*", tok):
            continue
        if i == 0 and tok in inline_helpers:
            continue
        root = tok.split(".", 1)[0]
        if root in KEYWORDS:
            continue
        paths.append(tok)
    return paths


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
                attrs.append((am.group(1), _strip_quotes(am.group(2))))
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
    scope_stack: list[dict] = []  # {kind, name, aliases:{alias: base}}
    controller_stack: list[dict] = []  # {tag, alias, depth}
    tag_depth: dict[str, int] = {}
    open_tag_stack: list[str] = []

    def resolve(path: str, use: tuple = ("helper",)) -> None:
        root, _, rest = path.partition(".")
        for scope in reversed(scope_stack):
            aliases = scope["aliases"]
            if root in aliases:
                base = aliases[root]
                if base is None:  # index-алиас
                    return
                resolve(base + ("." + rest if rest else ""), use)
                return
        if root == "settings":
            if rest:
                model.settings_paths.add(rest)
                model.settings_uses.append((rest, use))
            return
        for ctl in reversed(controller_stack):
            if root == ctl["alias"]:
                model.alias_paths.setdefault(ctl["tag"], set()).add(rest)
                return
        if root in CONTEXT_ROOTS:
            return
        # Неизвестный корень: контроллер с дефолтным алиасом или опечатка —
        # решает гейт G10, здесь только собираем.
        model.alias_paths.setdefault(f"?{root}", set()).add(rest)

    def _in_tag_span(pos: int) -> bool:
        return any(start <= pos < end for start, end in tag_spans)

    def _resolve_expr(expr: str, scalar_use: tuple) -> None:
        """Выражение мустачи: голый путь -> scalar_use, вызов хелпера -> helper."""
        tokens = [t for t in re.split(r"\s+", expr.strip()) if t]
        paths = _extract_paths(expr, inline_helpers)
        if len(tokens) == 1 and len(paths) == 1 and paths[0] == tokens[0]:
            resolve(paths[0], scalar_use)
        else:
            for p in paths:
                resolve(p, ("helper",))

    for pos, kind, obj in events:
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
                arg_paths = _extract_paths(
                    re.sub(BLOCK_ALIAS, " ", body[len(name):]), inline_helpers
                )
                for i, p in enumerate(arg_paths):
                    if i == 0 and name in ("each", "times", "repeat"):
                        resolve(p, ("iterated",))
                    elif i == 0 and name == "with":
                        resolve(p, ("with",))
                    else:
                        resolve(p, ("helper",))
                alias_m = BLOCK_ALIAS.search(body)
                base = None
                if arg_paths:
                    first = arg_paths[0]
                    base = _rebase(first, scope_stack)
                if alias_m:
                    parts = alias_m.group(1).split()
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
                scope_stack.append({"kind": "mustache", "name": name, "aliases": aliases})
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
                    if body2.startswith(("#", "/", "!", ">")):
                        continue
                    if attr_name == iter_attr:
                        use: tuple = ("iterated",)
                        _resolve_expr(body2, use)
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
                        cand = _extract_paths(inner.group(1), inline_helpers)
                        if cand:
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
                scope_stack.append(
                    {
                        "kind": "tag",
                        "tag": tag.name,
                        "depth": tag_depth.get(tag.name, 0),
                        "name": helper["helper"],
                        "aliases": aliases,
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
