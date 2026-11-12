#!/usr/bin/env python3
"""kit-custom-sections — офлайн-сборка и линт кастомных секций витрины Яндекс.Кит.

Единственная точка входа скила. Python 3.9+, только стандартная библиотека.
Сети нет вообще: ни одного HTTP-запроса, ни одного токена. Заливкой занимается
сосед kit-storefront-constructor — этот скрипт лишь печатает его команды.

Реализовано: doctor, patterns list/show/match, new, lint, ref, freeform
(свободная тройка html/css/schema под --confirm; те же гейты, что у new,
минус G11 — паттерна нет по определению). Режим слота `--slot header|footer`
у new/freeform (под --confirm-slot) собирает шаблон шапки или подвала всей
витрины, у lint — проверяет шаблон по правилам слота.
Заглушки (честный exit 3): dryrun, identify, adopt, edit, check-settings,
defaults, diff, handoff, compose, gap-report.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seclib import advice, assemble, catalog, conditions, gates, report  # noqa: E402
from seclib.common import (  # noqa: E402
    EXIT_FLAG_REQUIRED,
    EXIT_GATES,
    EXIT_OK,
    EXIT_PATTERN_NOT_FOUND,
    SLOT_ALIASES,
    SLOT_GEN,
    SLOT_INS,
    SLOT_RU,
    SectionError,
    dump_json,
    load_definitions,
    load_manifest,
    load_registry,
    read_json,
)

# Телеметрии у скила нет намеренно: по контракту репозитория (AGENTS.md,
# «Telemetry is headers on requests the skill was making anyway, and nothing
# else») телеметрия — это заголовки X-Skill* на HTTP-запросах клиента, а этот
# скил не делает ни одного запроса — вешать заголовки не на что.

FRESHNESS_WARN_DAYS = 45
FRESHNESS_HARD_DAYS = 120  # цифры не откалиброваны — см. references/gates.md


def _manifest_age_days() -> int | None:
    manifest = load_manifest()
    if manifest is None or not manifest.get("generated_at"):
        return None
    generated = _dt.date.fromisoformat(manifest["generated_at"])
    return (_dt.date.today() - generated).days


# --- commands -------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    if manifest is None:
        print("справочник НЕ собран: references/generated/manifest.json отсутствует")
        return EXIT_GATES
    age = _manifest_age_days()
    print(f"справочник собран: {manifest['generated_at']} (возраст {age} дн.)")
    counters = manifest.get("counters", {})
    print(
        "покрытие: {tags} тегов ({b}+{c}+{a}), {hi} инлайн-хелперов / {hf} функций, "
        "{hb} блочных, {sp} spacing-токенов, {rr} резервных $ref + {sr} служебных".format(
            tags=counters.get("tags_total"),
            b=counters.get("tags_builtin"),
            c=counters.get("tags_controllers"),
            a=counters.get("tags_atoms"),
            hi=counters.get("helpers_inline_names"),
            hf=counters.get("helpers_inline_functions"),
            hb=counters.get("helpers_block"),
            sp=counters.get("spacing_tokens"),
            rr=counters.get("reserved_refs"),
            sr=counters.get("service_refs"),
        )
    )
    cov = manifest.get("coverage", {})
    print(
        f"теги с разделом в доке: {cov.get('tags_documented')}/{cov.get('tags_total')} "
        f"(attributes_source: {cov.get('attributes_source')})"
    )
    if cov.get("tags_with_code_attributes") is not None:
        print(f"теги с атрибутами по коду движка: {cov.get('tags_with_code_attributes')}")
    if counters.get("icons") is not None:
        print(
            f"иконок ya-kit-icon: {counters.get('icons')}; "
            f"$ref только для формы кабинета: {counters.get('form_only_refs', 0)}"
        )
    for gap in manifest.get("manual_gaps", []):
        print(f"manual gap: {gap}")
    print("максимальный доступный уровень проверки: L0 (офлайн-линт); L1 dryrun — итерация 2")
    if age is not None and age > FRESHNESS_WARN_DAYS:
        print(f"ПРЕДУПРЕЖДЕНИЕ: справочнику {age} дн. (> {FRESHNESS_WARN_DAYS}) — знание могло устареть")
    if args.strict_freshness and age is not None and age > FRESHNESS_HARD_DAYS:
        print(f"строгий режим: возраст {age} дн. > {FRESHNESS_HARD_DAYS} — пересоберите справочник")
        return EXIT_GATES
    return EXIT_OK


# Запрос про шапку или подвал: секция страницы (hero и т. п.) их не заменяет.
_CHROME_QUERY = re.compile(r"шапк|подвал|хедер|футер|header|footer", re.I)
_CHROME_HINT = (
    "если нужна шапка или подвал всей витрины (меню, корзина, контакты на каждой "
    "странице) — это не секция страницы, а режим слота: паттерн с пометкой [слот …] "
    "или freeform --slot header|footer; alias ставится только с --confirm-slot после "
    "явного согласия пользователя (SKILL.md, правило 13)"
)


def cmd_patterns(args: argparse.Namespace) -> int:
    if args.patterns_cmd == "list":
        patterns = catalog.load_all()
        if not patterns:
            print("каталог паттернов пуст")
            return EXIT_GATES
        id_w = max(28, *(len(p.id) + 1 for p in patterns))
        pages_w = max(16, *(len(p.meta.get("pages", "—")) + 1 for p in patterns))
        print(f"{'id':{id_w}} {'страницы':{pages_w}} назначение")
        for p in patterns:
            slot = p.meta.get("slot")
            mark = f" [слот {slot}: только new --slot {slot} --confirm-slot]" if slot else ""
            print(f"{p.id:{id_w}} {p.meta.get('pages', '—'):{pages_w}} {p.meta.get('purpose', '')}{mark}")
        return EXIT_OK
    if args.patterns_cmd == "show":
        p = catalog.get(args.id)
        print(f"id: {p.id}\nversion: {p.meta.get('version')}\ntitle: {p.meta.get('title')}")
        print(f"purpose: {p.meta.get('purpose')}")
        print(f"origin: {p.meta.get('origin')}")
        print(f"applied_patches: {p.meta.get('applied_patches') or '—'}")
        print(f"answers_required: {p.meta.get('answers_required')}")
        alt_images: dict[str, list[str]] = {}
        for image_path, alt_path in p.alt_fields:
            alt_images.setdefault(alt_path, []).append(image_path)
        for alt_path, images in alt_images.items():
            print(
                f"alt_required: {alt_path} — непустой, если задано {' или '.join(images)} "
                "(без него new не соберёт секцию, гейт G21)"
            )
        if p.meta.get("slot"):
            slot = p.meta["slot"]
            print(
                f"slot: {slot} — {SLOT_RU.get(slot, slot)} всей витрины; собирается только "
                f"new --slot {slot} --confirm-slot"
            )
        if args.params:
            print("--- answers.schema.json ---")
            print(dump_json(p.answers_schema), end="")
        looks = p.looks
        if args.looks:
            _print_looks(p)
        elif looks:
            names = ", ".join(x["name"] for x in looks.get("looks") or [])
            print(f"виды: {names}; стартовый — {looks.get('start')} (--looks — подробно)")
        print("--- preview ---")
        print((p.dir / "preview.md").read_text(encoding="utf-8"))
        if args.with_source:
            print("--- template.hbs ---")
            print(p.html)
            print("--- styles.css ---")
            print(p.css)
        else:
            print("(тело шаблона намеренно не печатается; --with-source при осознанной необходимости)")
        return EXIT_OK
    if args.patterns_cmd == "match":
        scored = catalog.match(args.query)
        chrome_hint = _CHROME_QUERY.search(args.query) is not None
        if not scored:
            print(
                "подходящего паттерна нет. НЕ выдумывать html молча: честный путь — "
                "сказать пользователю и, если он подтвердил, собрать через "
                "freeform --confirm (гейты те же, что у new); "
                "gap-report для бэклога каталога — ещё заглушка."
            )
            if chrome_hint:
                print(_CHROME_HINT)
            return EXIT_PATTERN_NOT_FOUND
        for p, score in scored:
            mark = f" [слот {p.meta['slot']}]" if p.meta.get("slot") else ""
            print(f"{p.id:28} score={score}  {p.meta.get('purpose', '')}{mark}")
            _print_look_names(p)
        for p in catalog.alternatives([p for p, _ in scored]):
            print(f"{p.id:28} альтернатива  {p.meta.get('purpose', '')}")
            _print_look_names(p)
        print(
            "скрипт только сузил кандидатов; выбор делает модель — сравнить 2–3 блока "
            "и их виды, а не брать первого (SKILL.md, «Сочетания»)"
        )
        if chrome_hint:
            print(_CHROME_HINT)
        return EXIT_OK
    raise SectionError(f"неизвестная подкоманда patterns {args.patterns_cmd!r}")


def _print_look_names(p: catalog.Pattern) -> None:
    looks = p.looks.get("looks") or []
    if looks:
        print(f"{'':28} виды: {', '.join(x['name'] for x in looks)}")


def _print_looks(p: catalog.Pattern) -> None:
    spec = p.looks
    if not spec:
        print("--- виды ---\nу паттерна один вид: выбора оформления в настройках почти нет")
        return
    print("--- виды (new --look <имя>; без флага — стартовый) ---")
    for item in spec.get("looks") or []:
        start = " [стартовый]" if item["name"] == spec.get("start") else ""
        print(f"{item['name']}{start} — {item.get('title', '')}")
        if item.get("description"):
            print(f"    {item['description']}")
        settings = item.get("settings") or {}
        shown = json.dumps(settings, ensure_ascii=False) if settings else "{} — умолчания defaults.json"
        print(f"    настройки: {shown}")
    print(
        "картинок видов нет: офлайн рендера нет — вид смотреть в превью proposal-версии "
        "(kit.py content push --no-publish)"
    )


def _lint_dir(directory: Path, registry: dict, args: argparse.Namespace) -> tuple[list, dict]:
    # Два раскладки имён: своя (new) и раскладка `kit.py templates export`
    # соседа (template.html / template.css / template.schema.json / template.meta.json).
    html_path = next(
        (directory / n for n in ("template.html", "template.hbs") if (directory / n).is_file()),
        None,
    )
    if html_path is None:
        raise SectionError(f"{directory}: нет template.html / template.hbs")
    css_path = next(
        (directory / n for n in ("styles.css", "template.css") if (directory / n).is_file()),
        directory / "styles.css",
    )
    schema_path = next(
        (
            directory / n
            for n in ("schema.json", "template.schema.json")
            if (directory / n).is_file()
        ),
        None,
    )
    if schema_path is None:
        raise SectionError(f"{directory}: нет schema.json / template.schema.json")
    html = html_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
    schema = read_json(schema_path)

    meta = None
    meta_path = next(
        (
            directory / n
            for n in ("meta.json", "template.meta.json")
            if (directory / n).is_file()
        ),
        None,
    )
    if meta_path is not None:
        meta = read_json(meta_path)
    create_path = directory / "create.json"
    if meta is None and create_path.is_file():
        meta = read_json(create_path)

    pattern = None
    pattern_id = (meta or {}).get("pattern")
    if pattern_id:
        try:
            pattern = catalog.get(pattern_id)
        except SystemExit:
            pattern = None

    # Слот: флаг --slot, иначе meta.json вывода new/freeform --slot, иначе
    # pattern.yaml каталога (паттерн шапки/подвала линтуется по правилам слота).
    slot = getattr(args, "slot", None) or (meta or {}).get("slot") or None
    if slot is None and (directory / "pattern.yaml").is_file():
        slot = catalog.Pattern(directory).meta.get("slot") or None

    # G8: настройки против собственной схемы. В выводе new это settings.json,
    # в каталоге паттернов — defaults.json: невалидные настройки иначе
    # отклонял бы только конструктор при привязке секции.
    settings_obj = None
    settings_name = "settings.json"
    for candidate in ("settings.json", "defaults.json"):
        candidate_path = directory / candidate
        if candidate_path.is_file():
            loaded = read_json(candidate_path)
            if isinstance(loaded, dict):
                settings_obj = loaded
                settings_name = candidate
            break

    # Поле script: файл template.js из `kit.py templates export` или ключ script
    # в meta/create.json. Линт JS не проверяет, но о нём говорит (G7 WARNING).
    script = None
    script_path = directory / "template.js"
    if script_path.is_file():
        script = (script_path.name, script_path.read_text(encoding="utf-8"))
    elif isinstance((meta or {}).get("script"), str):
        script = (f"{meta_path.name if meta_path else 'create.json'}:script", meta["script"])

    mode = (meta or {}).get("mode", "lint")
    findings = gates.run_gates(
        html,
        css,
        schema,
        registry,
        mode=mode,
        meta=meta,
        pattern=pattern,
        strict=args.strict,
        allow_unknown_attrs=getattr(args, "allow_unknown_attrs", False),
        settings=settings_obj,
        settings_name=settings_name,
        script=script,
        slot=slot,
    )
    return findings, {"html": html, "css": css, "schema": schema}


def cmd_lint(args: argparse.Namespace) -> int:
    if args.against_settings:
        # G14 — итерация 2. Молча игнорировать флаг нельзя: зелёный lint читался бы
        # как «новая схема совместима с живыми настройками», а проверки нет вовсе.
        print(
            "--against-settings ещё не реализован: автоматической сверки новой схемы с "
            "настройками, которые уже заполнены у живой секции, здесь нет. Сверьте поля "
            "сами перед переездом на новый шаблон — поле, которому в новой схеме нет "
            "места, потеряется. Итерация 2 скила."
        )
        return EXIT_FLAG_REQUIRED
    registry = load_registry()
    findings, _ = _lint_dir(Path(args.dir), registry, args)
    return report.print_findings(findings, as_json=args.json)


_VIDEO_REF = "#/definitions/VideoModel"


def _empty_model_fields(node: object, path: str = "", schema: object = None) -> list[str]:
    """Пути пустых модельных заглушек: изображение или видео без fileId, ссылка
    без link, источник товаров без id/slug. Видео от изображения отличает схема
    секции ($ref VideoModel): ключи fileId/filePath у них одни и те же."""
    found: list[str] = []
    node_schema = schema if isinstance(schema, dict) else {}
    if isinstance(node, dict):
        keys = set(node.keys())
        if {"fileId", "filePath"} <= keys and not node.get("fileId") and not node.get("filePath"):
            if node_schema.get("$ref") == _VIDEO_REF:
                found.append(
                    f"{path or '<root>'} — видео (ролик выбирает мерчант в форме секции; "
                    "витрина загружает его по fileId)"
                )
            else:
                found.append(f"{path or '<root>'} — изображение (объект из kit.py media upload)")
            return found
        if {"link", "linkId"} <= keys and not node.get("link"):
            found.append(f"{path or '<root>'} — ссылка (объект {{link, linkId}})")
            return found
        if {"id", "slug", "type"} <= keys and not node.get("id") and not node.get("slug"):
            found.append(f"{path or '<root>'} — источник товаров (категория/подборка магазина)")
            return found
        props = node_schema.get("properties")
        for key, value in node.items():
            sub = props.get(key) if isinstance(props, dict) else None
            found += _empty_model_fields(value, f"{path}.{key}" if path else key, sub)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += _empty_model_fields(item, f"{path}[{i}]", node_schema.get("items"))
    return found


_MENU_REF = "#/definitions/MenuId"


def _empty_menu_fields(
    schema: object, settings: object, path: str = ""
) -> list[tuple[str, bool]]:
    """(путь, обязательно ли по схеме) полей `$ref MenuId` с пустым значением: меню
    не заводится из секции, берётся id уже существующего меню магазина."""
    found: list[tuple[str, bool]] = []
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return found
    values = settings if isinstance(settings, dict) else {}
    required = set(schema.get("required") or [])
    for key, node in schema["properties"].items():
        if not isinstance(node, dict):
            continue
        where = f"{path}.{key}" if path else key
        value = values.get(key)
        if node.get("$ref") == _MENU_REF:
            if not value:
                found.append((where, key in required))
        elif node.get("type") == "object":
            found += _empty_menu_fields(node, value, where)
        elif node.get("type") == "array" and isinstance(value, list):
            for i, item in enumerate(value):
                found += _empty_menu_fields(node.get("items"), item, f"{where}[{i}]")
    return found


_MENU_NOTE = (
    "меню: id существующего меню магазина. Меню из секции не "
    "заводится; id берутся из настроек нативного слота (kit.py section show "
    "--id <id слота>): у шапки modules.main.menu (в текущей версии шапки — "
    "modules.main.menu.menu) и modules.additional.menu, у подвала modules[].menu "
    "и legalInfo.menu"
)


def _print_placeholders(settings: dict, schema: dict, rules: dict | None = None) -> None:
    # Пустые модельные заглушки в settings — то, что обязан заполнить мерчант
    # (в форме кабинета) или агент (в answers объектом из kit.py media upload и
    # т.п.). Молчать о них нельзя: иначе страница соберётся без картинок и товаров.
    # Поле с правилом conditional_fields, которое при этих настройках не нужно,
    # печатается отдельно: заполнять его незачем.
    rules = rules or {}
    entries = [(p.split(" — ")[0], p, True) for p in _empty_model_fields(settings, schema=schema)]
    entries += [
        (p, f"{p} — {_MENU_NOTE}", required) for p, required in _empty_menu_fields(schema, settings)
    ]
    if not entries:
        return
    need: list[str] = []
    skip: list[str] = []
    for path, text, required in entries:
        if conditions.applies(rules, path):
            (need if conditions.needed(rules, settings, path) else skip).append(text)
        else:
            (need if required else skip).append(text)
    if need:
        print("обязательно заполнить мерчанту или через answers (сейчас пустые заглушки):")
        for text in need:
            print(f"  - {text}")
    if skip:
        print("не нужно при этих answers (поле необязательное или условное, пустым оставить можно):")
        for text in skip:
            print(f"  - {text}")


def _slot_warning(slot: str) -> str:
    """Что значит шаблон шапки/подвала для всей витрины — до сборки, а не после."""
    return (
        f"ВНИМАНИЕ: {SLOT_RU[slot].upper()} ВСЕЙ ВИТРИНЫ. Шаблон получит alias "
        f"{SLOT_ALIASES[slot]}. Поставленный в слот {SLOT_GEN[slot]}, он станет "
        f"{SLOT_INS[slot]} КАЖДОЙ страницы, включая страницы, которые сервер добавит "
        "сам позже. Скрыть секцию в слоте сервер позволяет, но витрина тогда "
        "останется без шапки или подвала: нативный блок сам не вернётся (по коду, "
        "живьём не проверено).\n"
        "Чего своя шапка или подвал не получают от витрины (по коду, визуально не "
        "проверялось): отступа контента под прозрачной липкой шапкой и отступов баннера "
        "cookie; страховка сервера, которая дорисовывает пропавший подвал, работает только "
        "от нативного подвала; тема оформления, применённая позже, может заменить только "
        "нативные шапку и подвал — витрина станет смешанной.\n"
        "Меню — только существующие, по id из настроек нативного слота. Шаблон нельзя "
        "поправить или удалить через API: каждая правка — новый шаблон.\n"
        f"Показать это пользователю и получить явное согласие именно на смену "
        f"{SLOT_GEN[slot]} всей витрины; только после этого повторить команду с "
        "--confirm-slot."
    )


def _check_slot_flags(
    args: argparse.Namespace, pattern_id: str | None = None, pattern_slot: str | None = None
) -> int | None:
    """None — можно собирать; иначе код выхода. alias шапки/подвала ставится только
    явным --slot и только после --confirm-slot (согласие на эффект для всей витрины)."""
    slot = args.slot
    if args.confirm_slot and not slot:
        print("--confirm-slot без --slot ничего не подтверждает: укажите --slot header|footer")
        return EXIT_FLAG_REQUIRED
    if pattern_id is not None and pattern_slot != slot:
        if pattern_slot and slot is None:
            print(
                f"паттерн {pattern_id} — {SLOT_RU.get(pattern_slot, pattern_slot)} всей "
                f"витрины, а не секция страницы: собирается только "
                f"new --slot {pattern_slot} --confirm-slot"
            )
            if pattern_slot in SLOT_ALIASES:
                print(_slot_warning(pattern_slot))
            return EXIT_FLAG_REQUIRED
        if pattern_slot:
            print(f"паттерн {pattern_id} собран под слот {pattern_slot}, а не {slot}")
            return EXIT_GATES
        print(
            f"паттерн {pattern_id} — секция страницы, в слот {slot} не годится: шаблон "
            f"слота целиком заменяет нативный блок {SLOT_GEN[slot]} с меню и контактами, "
            "и без них остались бы все страницы. Для слота — паттерн с пометкой "
            f"[слот {slot}] в patterns list или freeform --slot {slot}"
        )
        return EXIT_GATES
    if slot and not args.confirm_slot:
        print(_slot_warning(slot))
        return EXIT_FLAG_REQUIRED
    return None


# Папки секций одной страницы лежат рядом — у общего work.json (_handoff_paths).
# Соседей много не бывает; потолок — чтобы случайный --out в домашней папке не
# превратился в обход большого дерева.
_SIBLING_SCAN_LIMIT = 200


def _same_look_siblings(out_dir: Path, pattern_id: str, look_name: str) -> list[str]:
    """Соседние папки, где new уже собрал этот паттерн в этом виде (meta.json)."""
    here = Path(os.path.abspath(out_dir))
    try:
        entries = sorted(here.parent.iterdir())[:_SIBLING_SCAN_LIMIT]
    except OSError:
        return []
    hits = []
    for entry in entries:
        if entry == here or not (entry / "meta.json").is_file():
            continue
        try:
            meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("pattern") == pattern_id and meta.get("look") == look_name:
            hits.append(entry.name)
    return hits


def cmd_new(args: argparse.Namespace) -> int:
    registry = load_registry()
    pattern = catalog.get(args.pattern)
    blocked = _check_slot_flags(args, pattern.id, pattern.meta.get("slot") or None)
    if blocked is not None:
        return blocked
    slot = args.slot
    answers = read_json(Path(args.answers))
    if not isinstance(answers, dict):
        raise SectionError("answers.json должен быть JSON-объектом")

    look = pattern.look(args.look)
    if args.look and look is None:
        names = [x.get("name") for x in (pattern.looks.get("looks") or [])]
        print(
            f"вида {args.look!r} у паттерна {pattern.id} нет; виды: {', '.join(names) or '—'} "
            f"(patterns show {pattern.id} --looks)"
        )
        return EXIT_GATES
    settings, errors = assemble.build_settings(pattern, answers, look)
    if errors:
        for err in errors:
            print(f"answers: {err}")
        return EXIT_GATES
    if settings.get("modules") == []:
        print(
            "warn: modules пуст — секция соберётся, но на витрине будет голый "
            "заголовок без содержимого"
        )

    html, css, schema = pattern.html, pattern.css, pattern.schema
    title = str(answers.get("titleText") or pattern.meta.get("title") or pattern.id)
    meta = {
        "pattern": pattern.id,
        "pattern_version": pattern.meta.get("version"),
        "fingerprint": pattern.meta.get("fingerprint"),
        "mode": "new",
        "alias": SLOT_ALIASES[slot] if slot else "",
    }
    if slot:
        meta["slot"] = slot
    if look is not None:
        meta["look"] = look.get("name")
    create = assemble.build_create_json(title, html, css, schema, mode="new", slot=slot)

    findings = gates.run_gates(
        html,
        css,
        schema,
        registry,
        mode="new",
        meta=meta,
        pattern=pattern,
        settings=settings,
        settings_name="settings.json",
        slot=slot,
    )
    # Советы по настройкам (ритм, второй h1, демотекст, тупик без кнопки…):
    # info и warning не мешают сборке, error — мешает, как гейт.
    findings += advice.run_advice(
        pattern, settings, answers, page_rhythm=args.page_rhythm
    )
    out_dir = Path(args.out)
    if look is not None and not slot and not args.replace:
        siblings = _same_look_siblings(out_dir, pattern.id, look["name"])
        findings += advice.same_look(pattern, look["name"], siblings)
    errors_found = [f for f in findings if f["severity"] == "error"]
    if errors_found:
        report.print_findings(findings, as_json=args.json)
        print("new: гейты не прошли — ничего не записано")
        return EXIT_GATES

    tmp = Path(tempfile.mkdtemp(prefix="kit-section-"))
    try:
        (tmp / "template.html").write_text(html, encoding="utf-8")
        (tmp / "styles.css").write_text(css, encoding="utf-8")
        (tmp / "schema.json").write_text(dump_json(schema), encoding="utf-8")
        (tmp / "settings.json").write_text(dump_json(settings), encoding="utf-8")
        (tmp / "meta.json").write_text(dump_json(meta), encoding="utf-8")
        (tmp / "create.json").write_text(dump_json(create), encoding="utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)
        for item in tmp.iterdir():
            shutil.copy2(item, out_dir / item.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    report.print_findings(findings, as_json=args.json)
    what = f"шаблон {SLOT_GEN[slot]} витрины собран" if slot else "секция собрана"
    print(f"new: {what} из паттерна {pattern.id} в {out_dir}")
    if look is not None:
        others = [x["name"] for x in pattern.looks.get("looks") or [] if x["name"] != look["name"]]
        start = " (стартовый)" if not args.look else ""
        print(
            f"вид: {look['name']}{start} — {look.get('title', '')}; другие: "
            f"{', '.join(others) or '—'} (new --look <имя>, patterns show {pattern.id} --looks)"
        )
    _print_placeholders(settings, schema, pattern.conditional_fields)
    _print_handoff(out_dir, slot, args.replace)
    return EXIT_OK


# Рабочая копия страницы — один общий work.json рядом с папками секций, а не файл
# в папке каждой секции: pull в новый файл на каждую секцию терял несохранённые
# правки страницы, и их приходилось пересобирать скриптами после каждого нового
# шаблона. Пути печатаются абсолютными: шелл агента сбрасывает cwd между вызовами,
# и относительный путь из подсказки указал бы в другую папку.
_WORK = "work.json"

# pull читает активную версию: правки, сохранённые push --no-publish, лежат в
# предложении, а активной после него стоит копия прежней версии.
_PULL_WARNING = (
    "  # ВНИМАНИЕ: content pull перезаписывает {work} активной версией витрины — "
    "правки страницы, которых нет на витрине, пропадут.",
    "  #   Создайте все шаблоны до первой правки страницы. Правки уже есть — после pull "
    "повторите те же section add/set (settings-файлы на диске);",
    "  #   push --no-publish их не сохранит: активной после него стоит копия прежней "
    "версии, а правки остаются в предложении, которое pull не читает",
)


def _handoff_paths(out_dir: Path) -> tuple[str, str]:
    """(папка секции, общий work.json рядом с ней) — абсолютные, без разворота ссылок."""
    out = os.path.abspath(out_dir)
    return out, os.path.join(os.path.dirname(out), _WORK)


def _print_handoff(
    out_dir: Path, slot: str | None = None, replace: str | None = None
) -> None:
    if slot:
        _print_slot_handoff(out_dir, slot, replace)
        return
    out_dir, work = _handoff_paths(out_dir)
    print("залить может только kit-storefront-constructor; последовательность команд:")
    print("  # ВНИМАНИЕ: по умолчанию kit.py смотрит в ПРОД. Нужный контур —")
    print("  # либо --base-url <контур>, либо KIT_API_BASE_URL. Проверить: kit.py env")
    print(
        f"  kit.py templates create --file {out_dir}/create.json --dry-run"
        "   # показать пользователю сводку, не тело шаблона"
    )
    print(f"  kit.py templates create --file {out_dir}/create.json --confirm   # вернёт template_id")
    for line in _PULL_WARNING:
        print(line.format(work=work))
    print(
        f"  kit.py content pull --out {work}   # общий work.json страницы; заново после "
        "create — новый шаблон должен попасть в снимок"
    )
    add = (
        f"  kit.py section add --work {work} --page <alias> "
        f"--widget YandexKit.Mystique --template-id <uuid> "
        f"--settings-file {out_dir}/settings.json"
    )
    swap = (
        f"  kit.py section set --work {work} --id {replace or '<uuid секции>'} "
        f"--template-id <uuid> --settings-file {out_dir}/settings.json"
    )
    swap_note = (
        "  #   живой шаблон не правится — секция переезжает на новый (SKILL.md, правило 16): "
        "до команды сверить, что каждое заполненное поле живой секции есть в новой схеме"
    )
    if replace:
        print(f"  # замена шаблона у живой секции {replace}:")
        print(swap_note)
        print(swap)
    else:
        print("  # а) новая секция на странице:")
        print(add)
        print("  # б) замена шаблона у живой секции (или new --replace <uuid секции>):")
        print(swap_note)
        print(swap)
    print(f"  kit.py content diff --work {work}")
    print(f"  kit.py content push --work {work} --commit-message '<текст>' --dry-run")
    print(
        f"  kit.py content push --work {work} --commit-message '<текст>' "
        f"--no-publish --confirm"
    )


def _print_slot_handoff(out_dir: Path, slot: str, replace: str | None = None) -> None:
    """Шаблон слота: create только с --chrome-slot, в слот — не section add."""
    gen = SLOT_GEN[slot]
    out_dir, work = _handoff_paths(out_dir)
    print(
        f"залить может только kit-storefront-constructor; последовательность команд "
        f"({SLOT_RU[slot]} всей витрины):"
    )
    print("  # ВНИМАНИЕ: по умолчанию kit.py смотрит в ПРОД. Нужный контур —")
    print("  # либо --base-url <контур>, либо KIT_API_BASE_URL. Проверить: kit.py env")
    print(f"  kit.py templates create --file {out_dir}/create.json --chrome-slot {slot} --dry-run")
    print("  #   в плане store_wide_warning: показать пользователю и получить согласие на него")
    print(
        f"  kit.py templates create --file {out_dir}/create.json --chrome-slot {slot} "
        "--confirm   # вернёт template_id"
    )
    print("  # Шаблон пока нигде не стоит: после create витрину изменённой не называть.")
    print(f"  # Слот {gen} ещё нативный — переключает пользователь в конструкторе кабинета:")
    print(
        f"  #   {SLOT_RU[slot]} → «Заменить» → этот шаблон (в списке только шаблоны "
        f"с alias {SLOT_ALIASES[slot]}). Командой это не делается."
    )
    print(f"  # Слот {gen} уже на своём шаблоне (section show печатает chrome_slot: {slot}):")
    for line in _PULL_WARNING:
        print(line.format(work=work))
    print(f"  kit.py content pull --out {work}   # заново после create — шаблон должен попасть в снимок")
    print(
        f"  kit.py section set --work {work} --id {replace or '<id слота>'} "
        f"--template-id <uuid> --settings-file {out_dir}/settings.json"
    )
    print(f"  kit.py content diff --work {work}")
    print(f"  kit.py content push --work {work} --commit-message '<текст>' --dry-run")
    print(
        f"  kit.py content push --work {work} --commit-message '<текст>' "
        f"--no-publish --confirm"
    )
    print(
        f"  # section add для шаблона {gen} не годится: сосед его отвергает, а секция "
        "встала бы на одну страницу рядом с нативным слотом"
    )


def cmd_freeform(args: argparse.Namespace) -> int:
    """Единственная точка, где HTML написан моделью. Без --confirm — exit 3;
    в meta.json и commit_message проставляется mode=freeform. Гейты — те же, что у new
    (G6-хардкод цвета в freeform — WARNING, не ERROR); паттерна и G11 нет по определению."""
    if not args.confirm:
        print(
            "freeform — выход из детерминированного режима: html написан моделью, "
            "а не каталогом. Осознанный шаг подтверждается флагом --confirm. "
            "Сначала убедиться, что `patterns match` вернул пусто (exit 5)."
        )
        return EXIT_FLAG_REQUIRED
    blocked = _check_slot_flags(args)
    if blocked is not None:
        return blocked
    slot = args.slot
    registry = load_registry()
    html = Path(args.html).read_text(encoding="utf-8")
    css = Path(args.css).read_text(encoding="utf-8")
    schema = read_json(Path(args.schema))
    if not isinstance(schema, dict):
        raise SectionError("json_schema должен быть JSON-объектом")
    settings = None
    if args.settings:
        settings = read_json(Path(args.settings))
        if not isinstance(settings, dict):
            raise SectionError("settings.json должен быть JSON-объектом")
    else:
        print(
            "warn: --settings не задан — settings.json будет пустым объектом; "
            "G8 (валидность настроек против схемы) не проверялся"
        )

    title = args.title or str(schema.get("title") or "Freeform-секция")
    meta = {"pattern": None, "mode": "freeform", "alias": SLOT_ALIASES[slot] if slot else ""}
    if slot:
        meta["slot"] = slot
    create = assemble.build_create_json(title, html, css, schema, mode="freeform", slot=slot)

    findings = gates.run_gates(
        html,
        css,
        schema,
        registry,
        mode="freeform",
        meta=meta,
        pattern=None,
        settings=settings,
        settings_name="settings.json",
        slot=slot,
    )
    errors_found = [f for f in findings if f["severity"] == "error"]
    if errors_found:
        report.print_findings(findings, as_json=args.json)
        print("freeform: гейты не прошли — ничего не записано")
        return EXIT_GATES

    out_dir = Path(args.out)
    tmp = Path(tempfile.mkdtemp(prefix="kit-section-"))
    try:
        (tmp / "template.html").write_text(html, encoding="utf-8")
        (tmp / "styles.css").write_text(css, encoding="utf-8")
        (tmp / "schema.json").write_text(dump_json(schema), encoding="utf-8")
        (tmp / "settings.json").write_text(dump_json(settings or {}), encoding="utf-8")
        (tmp / "meta.json").write_text(dump_json(meta), encoding="utf-8")
        (tmp / "create.json").write_text(dump_json(create), encoding="utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)
        for item in tmp.iterdir():
            shutil.copy2(item, out_dir / item.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    report.print_findings(findings, as_json=args.json)
    what = f"шаблон {SLOT_GEN[slot]} витрины собран" if slot else "секция собрана"
    print(f"freeform: {what} в {out_dir} (mode=freeform — html написан моделью)")
    if settings:
        _print_placeholders(settings, schema)
    _print_handoff(out_dir, slot, args.replace)
    return EXIT_OK


def cmd_ref(args: argparse.Namespace) -> int:
    registry = load_registry()
    kind, name = args.kind, args.name
    if kind in ("tag", "atom", "controller"):
        info = registry["tag_attributes"].get(name)
        if info is None:
            print(f"{name}: НЕТ в реестре тегов движка — не использовать, секция молча опустеет")
            return EXIT_GATES
        group = (
            "builtin"
            if name in registry["tags"]["builtin"]
            else "controller"
            if name in registry["tags"]["controllers"]
            else "atom"
        )
        code = set(info.get("code_attributes") or [])
        required = set(info.get("required_attributes") or [])

        def _mark(attr: str) -> str:
            notes = (["код"] if attr in code else []) + (["обязателен"] if attr in required else [])
            return attr + (f" ({', '.join(notes)})" if notes else "")

        attrs = [_mark(a) for a in info["attributes"]]
        source = "по доке" if info["documented"] else "по коду движка" if code else "по доке"
        print(f"{name} ({group}); атрибуты {source}: {', '.join(attrs) or 'таблицы в доке нет'}")
        for attr, reason in (info.get("removed_attributes") or {}).items():
            print(f"  {attr}: не действует — {reason}")
        if name == "ya-kit-icon" and registry.get("icons"):
            styles = registry.get("icon_styles") or {}
            if styles:
                print(f"  имена иконок ({len(registry['icons'])}) по стилю SVG:")
                for style in ("outline", "solid", "hardcoded-color", "multicolor"):
                    names = [n for n in registry["icons"] if styles.get(n, "outline") == style]
                    if names:
                        print(f"    {style} ({len(names)}): {', '.join(names)}")
                print(
                    "  одна секция — один стиль: hardcoded-color и multicolor не следуют "
                    "палитре (G22); heart контуром — классом с fill: transparent и "
                    "--ys-color-stroke: currentColor"
                )
            else:
                print(f"  имена иконок ({len(registry['icons'])}): {', '.join(registry['icons'])}")
        return EXIT_OK
    if kind == "helper":
        for entry in registry["helpers"]["inline"]:
            if entry["name"] == name:
                print(f"{name}: инлайн-хелпер группы {entry['group']} (функция {entry['function']})")
                return EXIT_OK
        for d in registry["helpers"]["block"]:
            if d["helper"] == name:
                print(f"{name}: блочный хелпер, открывающий тег <{d['openTag']}>, атрибут {d['openAttr']}")
                return EXIT_OK
        print(f"{name}: такого хелпера НЕТ (ни инлайн, ни блочного) — не выдумывать")
        return EXIT_GATES
    if kind == "token":
        matches = [t for t in registry["color_tokens"] + registry["extra_css_vars"] if name in t]
        matches += [
            f"--ys-spacing-{k} ({v})"
            for k, v in registry["spacing_tokens"].items()
            if name in f"--ys-spacing-{k}"
        ]
        if not matches:
            print(f"{name}: токена нет в реестре темы")
            return EXIT_GATES
        print("\n".join(matches))
        return EXIT_OK
    if kind == "schema-ref":
        ref = name if name.startswith("#/") else f"#/definitions/{name}"
        if ref in registry["reserved_refs"]:
            bodyless = (load_definitions() or {}).get("bodyless_refs") or []
            if ref in bodyless:
                print(
                    f"{ref}: резервный тип — даёт контрол мерчанта; тела в схеме витрины "
                    "нет, поля значения линт не проверяет (у ProductVariant опираться "
                    "только на variantId)"
                )
            else:
                print(f"{ref}: резервный тип — даёт контрол мерчанта")
            return EXIT_OK
        if ref in registry["service_refs"]:
            print(f"{ref}: служебный тип — работает, но инлайнится")
            return EXIT_OK
        if ref in registry.get("form_only_refs", []):
            print(
                f"{ref}: только для формы кабинета — контрол формы есть, витрина тип не "
                "резолвит (тела нет); lint пропускает с WARNING, в шаблоне опираться только "
                "на variantId"
            )
            return EXIT_OK
        print(f"{ref}: вне белого списка $ref")
        return EXIT_GATES
    raise SectionError(f"неизвестный вид справки {kind!r}")


# --- wiring ---------------------------------------------------------------


STUB_COMMANDS = (
    "dryrun",
    "identify",
    "adopt",
    "edit",
    "check-settings",
    "defaults",
    "diff",
    "handoff",
    "compose",
    "gap-report",
)


def _add_replace_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--replace",
        metavar="SECTION_ID",
        default=None,
        help="секция уже стоит на странице: печатать только переезд section set на новый шаблон",
    )


def _add_slot_args(p: argparse.ArgumentParser, build: bool) -> None:
    p.add_argument(
        "--slot",
        choices=sorted(SLOT_ALIASES),
        default=None,
        help="шаблон шапки или подвала всей витрины (alias YandexKit.Header/Footer)"
        if build
        else "проверить шаблон по правилам слота шапки или подвала",
    )
    if build:
        p.add_argument(
            "--confirm-slot",
            action="store_true",
            help="пользователь подтвердил смену шапки/подвала всей витрины",
        )


def main(argv: list[str]) -> int:
    # Заглушки перехватываются до argparse: REMAINDER не спасает от флаговой
    # формы (`compose --blocks …` падал бы в argparse с exit 2 вместо контрактного 3).
    if argv and argv[0] in STUB_COMMANDS:
        print(
            f"команда {argv[0]!r} — итерация 2/3 скила, ещё не реализована. "
            "Доступно сейчас: doctor, patterns list/show/match, new, lint, ref, "
            "freeform (--confirm)."
        )
        return EXIT_FLAG_REQUIRED

    parser = argparse.ArgumentParser(prog="section.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="свежесть и покрытие справочника")
    p.add_argument("--strict-freshness", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("patterns", help="каталог паттернов")
    psub = p.add_subparsers(dest="patterns_cmd", required=True)
    psub.add_parser("list")
    ps = psub.add_parser("show")
    ps.add_argument("id")
    ps.add_argument("--params", action="store_true")
    ps.add_argument("--with-source", action="store_true")
    ps.add_argument("--looks", action="store_true", help="виды паттерна и их настройки")
    pm = psub.add_parser("match")
    pm.add_argument("query")
    p.set_defaults(func=cmd_patterns)

    p = sub.add_parser("new", help="собрать секцию из паттерна")
    p.add_argument("--pattern", required=True)
    p.add_argument("--answers", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--look",
        default=None,
        help="вид паттерна (patterns show <id> --looks); без флага — стартовый вид",
    )
    p.add_argument(
        "--page-rhythm",
        action="store_true",
        help="ритм не 0 задан намеренно (разрыв главы, лендинг из кастома) — не напоминать",
    )
    _add_replace_arg(p)
    _add_slot_args(p, build=True)
    p.set_defaults(func=cmd_new, strict=False)

    p = sub.add_parser(
        "freeform", help="секция из html/css/schema, написанных моделью (--confirm обязателен)"
    )
    p.add_argument("--html", required=True)
    p.add_argument("--css", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--settings")
    p.add_argument("--title")
    p.add_argument("--out", required=True)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--json", action="store_true")
    _add_replace_arg(p)
    _add_slot_args(p, build=True)
    p.set_defaults(func=cmd_freeform, strict=False)

    p = sub.add_parser("lint", help="офлайн-гейты над тройкой html+css+schema")
    p.add_argument("dir")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--against-settings", default=None, help="итерация 2 (G14)")
    p.add_argument("--allow-unknown-attrs", action="store_true")
    _add_slot_args(p, build=False)
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("ref", help="справочник DSL как данные")
    p.add_argument("kind", choices=["tag", "atom", "controller", "helper", "token", "schema-ref"])
    p.add_argument("name")
    p.add_argument("--grep", default=None)
    p.set_defaults(func=cmd_ref)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SectionError as exc:
        return exc.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
