"""Посимвольный сканер CSS без парсера: `:global(`, `<`, var(--*), хардкод цветов.

Механика платформы: CSS скоупится автоматически `#ya-kit-<templateId>`; символ
`<` вырезается изолятором вместе с хвостом до `>`; keyframes/font-face не
скоупятся; `:global(` — единственный способ выйти за скоуп (запрещён всегда).
"""

from __future__ import annotations

import re

VAR_USE = re.compile(r"var\(\s*(--[\w-]+)\s*(,)?")
VAR_DEF = re.compile(r"(--[\w-]+)\s*:")
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
FUNC_COLOR = re.compile(r"\b(rgb|rgba|hsl|hsla)\(")


def _strip_comments_and_strings(css: str) -> str:
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        ch = css[i]
        if ch == "/" and i + 1 < n and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in "\"'":
            quote = ch
            j = i + 1
            while j < n and css[j] != quote:
                j += 2 if css[j] == "\\" else 1
            out.append('""')
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def scan(css: str, registry: dict) -> dict:
    """Структура находок для G6; серьёзность хардкода цвета решает гейт по режиму."""
    clean = _strip_comments_and_strings(css)
    findings: dict[str, list[str]] = {
        "global_escape": [],
        "angle_bracket": [],
        "unknown_ys_vars": [],
        "undefined_local_vars": [],
        "hardcoded_colors": [],
        "double_slash": [],
        "at_import": [],
    }

    # G18: CSSIsolator платформы вырезает
    # `//` до конца строки БЕЗ учёта строк и url — один @font-face с
    # url(https://…) превратился в незакрытую скобку и отравил весь каскад
    # (страница обесцветилась). Блочные комментарии платформа снимает раньше,
    # поэтому их содержимое безопасно; всё остальное (включая строки и url) — нет.
    no_block_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for i, line in enumerate(no_block_comments.splitlines(), 1):
        if "//" in line:
            findings["double_slash"].append(
                f"строка {i}: `//` — изолятор платформы вырежет остаток строки как "
                f"комментарий, не разбирая строк и url ({line.strip()[:80]!r})"
            )
        if "@import" in line:
            findings["at_import"].append(f"строка {i}: @import в css секции не работает")

    for m in re.finditer(r":global\s*\(", clean):
        findings["global_escape"].append(f"позиция {m.start()}: :global( — выход за скоуп секции")

    for m in re.finditer(r"<", clean):
        findings["angle_bracket"].append(
            f"позиция {m.start()}: символ '<' — изолятор вырежет его вместе с хвостом до '>'"
        )

    allowed_vars = (
        {f"--ys-spacing-{k}" for k in registry["spacing_tokens"]}
        | set(registry["color_tokens"])
        | set(registry["extra_css_vars"])
    )
    defined_local = set(VAR_DEF.findall(clean))
    seen_vars: dict[str, bool] = {}
    for name, comma in VAR_USE.findall(clean):
        has_fallback = bool(comma)
        seen_vars[name] = seen_vars.get(name, False) or has_fallback
    for name in sorted(seen_vars):
        has_fallback = seen_vars[name]
        if name.startswith("--ys-"):
            if name not in allowed_vars and not has_fallback:
                findings["unknown_ys_vars"].append(
                    f"var({name}): нет в реестре токенов темы — на витрине будет пусто"
                )
        elif name not in defined_local and not has_fallback:
            findings["undefined_local_vars"].append(
                f"var({name}): переменная нигде в этом css не определена"
            )

    for m in HEX_COLOR.finditer(clean):
        findings["hardcoded_colors"].append(f"{m.group(0)}: хардкод цвета перебивает тему магазина")
    for m in FUNC_COLOR.finditer(clean):
        findings["hardcoded_colors"].append(
            f"{m.group(1)}(...): хардкод цвета перебивает тему магазина"
        )
    return findings
