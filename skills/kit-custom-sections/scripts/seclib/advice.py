"""Советы `new` по собранным настройкам: вкус и типовые промахи, а не синтаксис.

Гейты (gates.py) проверяют тройку и отказывают в сборке. Здесь — то, что видно
только по настройкам конкретной секции рядом с её соседями: ритм просторнее
нативного, второй h1, тупиковый блок без кнопки и т. п. Почти всё — `info`
или `warning`; `error` только там, где публикация гарантированно покажет
покупателю чужой текст (демотекст каталога в видимом поле первого экрана).
"""

from __future__ import annotations

import re

from .catalog import Pattern
from .gates import ERROR, WARNING, Finding

INFO = "info"

_RHYTHM_KEYS = (
    "verticalRhythm",
    "verticalRhythmTouch",
    "verticalRhythmBottom",
    "verticalRhythmBottomTouch",
)


def _f(code: str, severity: str, where: str, what: str, fix: str) -> Finding:
    return Finding(code, severity, where, what, fix)


def rhythm(settings: dict, *, page_rhythm: bool) -> list[Finding]:
    """Ритм не 0 без --page-rhythm: блок просторнее нативных соседей."""
    if page_rhythm:
        return []
    loud = {
        key: settings[key]
        for key in _RHYTHM_KEYS
        if isinstance(settings.get(key), str) and settings[key] not in ("", "0")
    }
    if not loud:
        return []
    shown = ", ".join(f"{k} {v}" for k, v in loud.items())
    return [
        _f(
            "page-rhythm",
            INFO,
            "settings.json",
            f"ритм не 0 ({shown}): к отступу секции добавится столько же с каждой "
            "стороны — рядом с нативной секцией выйдет пустая полоса, страница "
            "распадётся на плашки",
            "ритм как у соседей — \"0\"; 300–500 только как намеренный разрыв главы "
            "(смена палитры, закрывающий блок, лендинг целиком из кастома) — тогда "
            "подтвердить флагом --page-rhythm",
        )
    ]


def heading_level(pattern: Pattern, settings: dict) -> list[Finding]:
    """headingLevel h1 в настройках: на странице получится второй h1."""
    if settings.get("headingLevel") != "h1":
        return []
    return [
        _f(
            "second-h1",
            WARNING,
            "settings.headingLevel",
            "h1 в кастомной секции — почти всегда второй h1 на странице: витрина сама "
            "делает h1 заголовок главного нативного блока или первый заголовок секции "
            f"ниже, а ya-kit-text as=\"h1\" паттерна {pattern.id} в этот выбор не входит",
            "headingLevel h2 (по умолчанию); h1 — только по решению владельца, после "
            "публикации проверить код страницы",
        )
    ]


# Демотекст на месте, где его увидит каждый посетитель первого экрана: такая
# секция не публикуется вовсе.
_DEMO_COPY_ERRORS = {"hero-overlay": frozenset(("text",))}


def _strings(value: object, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings(item, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def demo_copy(pattern: Pattern, settings: dict) -> list[Finding]:
    """Строка settings, побайтно равная демотексту каталога в том же поле."""
    demo = pattern.demo_copy
    if not demo:
        return []
    errors = _DEMO_COPY_ERRORS.get(pattern.id, frozenset())
    out: list[Finding] = []
    for path, value in _strings(settings):
        generic = re.sub(r"\[\d+\]", "[]", path)
        if value and value in (demo.get(generic) or ()):
            out.append(
                _f(
                    "demo-copy",
                    ERROR if generic in errors else WARNING,
                    f"settings.{path}",
                    f"демотекст каталога «{value[:60]}» — покупатель увидит заготовку, "
                    "а не текст магазина",
                    "заменить своим текстом из answers или очистить поле",
                )
            )
    return out


# Плитка сетки 2 колонки на 390px — около 170px: длинное описание там рвётся
# на слоги через слово.
_TOUCH_GRID_TEXT = 60


def touch_grid(pattern: Pattern, settings: dict) -> list[Finding]:
    """advantages: layoutTouch grid с карточками по содержимому и длинными описаниями."""
    if pattern.id != "advantages":
        return []
    if settings.get("layoutTouch") != "grid" or settings.get("tileRatio") not in ("auto", None, ""):
        return []
    modules = settings.get("modules") if isinstance(settings.get("modules"), list) else []
    long_ = [
        i
        for i, m in enumerate(modules)
        if isinstance(m, dict) and len(str(m.get("description") or "")) > _TOUCH_GRID_TEXT
    ]
    if not long_:
        return []
    return [
        _f(
            "touch-grid",
            WARNING,
            "settings.layoutTouch",
            f"две колонки по ~170px на телефоне, а описания длиннее {_TOUCH_GRID_TEXT} знаков "
            f"(modules {', '.join(str(i) for i in long_)}): текст рвётся на слоги через слово",
            "layoutTouch list или scroll; grid — только плитки с фразой в 1–2 строки",
        )
    ]


def overlay_veil(pattern: Pattern, settings: dict) -> list[Finding]:
    """hero-overlay: ровная подложка на весь кадр плотнее 30%."""
    if pattern.id != "hero-overlay":
        return []
    overlay = settings.get("overlay") if isinstance(settings.get("overlay"), dict) else {}
    content = settings.get("content") if isinstance(settings.get("content"), dict) else {}
    kind = overlay.get("type")
    flat = kind == "tint" or (
        kind == "gradient" and content.get("valign") == "center" and content.get("align") == "center"
    )
    try:
        opacity = float(overlay.get("opacity") or 0)
    except (TypeError, ValueError):
        return []
    if not flat or opacity <= 30:
        return []
    tone = "белая" if overlay.get("tone") == "palette" else "тёмная"
    return [
        _f(
            "overlay-veil",
            WARNING,
            "settings.overlay",
            f"ровная {tone} подложка {opacity:g}% закрывает весь кадр: товар на фото "
            "выбеливается или темнеет, контраст фото падает вдвое",
            "текст сбоку (content.align start/end) — тогда градиент со стороны текста; "
            "или overlay.opacity до 30; tone palette на светлой палитре — заменить на inverse",
        )
    ]


# Кнопки со ссылкой вне поля-выхода: (контейнер, подпись, ссылка). Контейнер ""
# — корень settings, "modules[]" — каждый элемент modules, иначе вложенный объект.
# Шаблон выводит кнопку только при подписи и ссылке, а у button-row рисует её
# всегда. Задана только половина — кнопку явно задумали, а покупатель её не
# увидит (или увидит пустую, или ведущую в никуда у button-row): new называет обе
# половины — ссылку без подписи (cta-text) и подпись без ссылки (cta-link).
# Подпись без ссылки считается задуманной, только если она пришла из answers:
# стартовая метка интерфейса («Подробнее» у service-rows) без ссылки — не замысел.
_LINKED_BUTTONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "button-row": (("", "text", "link"),),
    "cta-band": (("", "buttonText", "buttonUrl"),),
    "goods-cover": (("cover", "buttonText", "url"),),
    "hero": (("", "buttonText", "buttonUrl"),),
    "hero-overlay": (("modules[]", "text", "link"),),
    "service-rows": (("modules[]", "buttonText", "buttonUrl"),),
    "text-media-split": (("", "buttonText", "buttonUrl"),),
}
_ALWAYS_DRAWN = frozenset(("button-row",))


def _has_link(node: dict, key: str) -> bool:
    link = node.get(key)
    return isinstance(link, dict) and bool(str(link.get("link") or "").strip())


def _button_nodes(settings: dict, answers: dict, container: str) -> list[tuple[str, dict, dict]]:
    """(путь для отчёта, объект settings с подписью и ссылкой, тот же объект answers)."""
    def obj(value: object) -> dict:
        return value if isinstance(value, dict) else {}

    if not container:
        return [("settings", settings, obj(answers))]
    if container.endswith("[]"):
        key = container[:-2]
        items = settings.get(key) if isinstance(settings.get(key), list) else []
        given = obj(answers).get(key) if isinstance(obj(answers).get(key), list) else []
        return [
            (f"settings.{key}[{i}]", m, obj(given[i]) if i < len(given) else {})
            for i, m in enumerate(items)
            if isinstance(m, dict)
        ]
    node = settings.get(container)
    return [(f"settings.{container}", node, obj(obj(answers).get(container)))] if isinstance(node, dict) else []


def _cta_text(pattern_id: str, where: str) -> Finding:
    what = (
        "у кнопки есть ссылка, но нет подписи — покупатель увидит пустую кнопку"
        if pattern_id in _ALWAYS_DRAWN
        else "у кнопки есть ссылка, но нет подписи — кнопка не выводится"
    )
    return _f(
        "cta-text",
        ERROR,
        where,
        what,
        "подпись, которая говорит, куда ведёт кнопка («Все условия доставки»)",
    )


def _cta_link(pattern_id: str, where: str, text: str) -> Finding:
    if pattern_id in _ALWAYS_DRAWN:
        severity = ERROR
        what = f"у кнопки «{text[:40]}» нет ссылки — покупатель увидит кнопку, которая никуда не ведёт"
    else:
        severity = WARNING
        what = f"у кнопки «{text[:40]}» есть подпись, но нет ссылки — кнопка не выводится"
    return _f(
        "cta-link",
        severity,
        where,
        what,
        "ссылка {link, linkId} — куда ведёт кнопка; кнопка не нужна — очистить подпись",
    )


def _has_text(node: dict, key: str) -> bool:
    return bool(str(node.get(key) or "").strip())


def exits(pattern: Pattern, settings: dict, answers: dict | None = None) -> list[Finding]:
    """Выход из информационного блока (exit_field в pattern.yaml) и обе половины
    каждой кнопки: подпись и ссылка."""
    out: list[Finding] = []
    answers = answers if isinstance(answers, dict) else {}
    field = pattern.meta.get("exit_field")
    if field:
        node = settings.get(field) if isinstance(settings.get(field), dict) else {}
        given = answers.get(field) if isinstance(answers.get(field), dict) else {}
        has_link = _has_link(node, "link")
        if has_link and not _has_text(node, "text"):
            out.append(_cta_text(pattern.id, f"settings.{field}.text"))
        elif not has_link and _has_text(given, "text"):
            out.append(_cta_link(pattern.id, f"settings.{field}.link", str(given.get("text")).strip()))
        elif not has_link:
            out.append(
                _f(
                    "no-exit",
                    INFO,
                    f"settings.{field}",
                    "информационный блок без выхода — тупик: дальше покупателю идти некуда",
                    f"{field}: {{text, link, style}} — кнопка или ссылка внутри блока (каталог, "
                    "условия, все отзывы), а не отдельной секцией button-row",
                )
            )
    for container, text_key, link_key in _LINKED_BUTTONS.get(pattern.id, ()):
        for where, node, given in _button_nodes(settings, answers, container):
            has_link = _has_link(node, link_key)
            if has_link and not _has_text(node, text_key):
                out.append(_cta_text(pattern.id, f"{where}.{text_key}"))
            elif not has_link and _has_text(given, text_key):
                out.append(_cta_link(pattern.id, f"{where}.{link_key}", str(given.get(text_key)).strip()))
    return out


def same_look(pattern: Pattern, look_name: str, siblings: list[str]) -> list[Finding]:
    """Тот же паттерн в том же виде уже собран в соседней папке: два таких блока
    на одной странице читаются копией."""
    if not siblings:
        return []
    others = [
        x.get("name")
        for x in pattern.looks.get("looks") or []
        if x.get("name") and x.get("name") != look_name
    ]
    shown = ", ".join(siblings[:3]) + ("…" if len(siblings) > 3 else "")
    return [
        _f(
            "same-look",
            INFO,
            "meta.json:look",
            f"{pattern.id} в виде {look_name} уже собран рядом ({shown}): если оба блока "
            "встанут на одну страницу, второй читается копией первого",
            f"второй блок — в другом виде: new --look {'|'.join(others) or '<вид>'} "
            f"(patterns show {pattern.id} --looks); на разных страницах — оставить",
        )
    ]


def run_advice(
    pattern: Pattern,
    settings: dict,
    answers: dict,
    *,
    page_rhythm: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    if not pattern.meta.get("slot"):
        findings += rhythm(settings, page_rhythm=page_rhythm)
    findings += heading_level(pattern, settings)
    findings += demo_copy(pattern, settings)
    findings += touch_grid(pattern, settings)
    findings += overlay_veil(pattern, settings)
    findings += exits(pattern, settings, answers)
    return findings


__all__ = ["INFO", "ERROR", "WARNING", "run_advice", "same_look"]
