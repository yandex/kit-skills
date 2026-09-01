#!/usr/bin/env python3
"""kit-custom-sections — офлайн-сборка и линт кастомных секций витрины Яндекс.Кит.

Единственная точка входа скила. Python 3.9+, только стандартная библиотека.
Сети нет вообще: ни одного HTTP-запроса, ни одного токена. Заливкой занимается
сосед kit-storefront-constructor — этот скрипт лишь печатает его команды.

Реализовано: doctor, patterns list/show/match, new, lint, ref, freeform
(свободная тройка html/css/schema под --confirm; те же гейты, что у new,
минус G11 — паттерна нет по определению).
Заглушки (честный exit 3): dryrun, identify, adopt, edit, check-settings,
defaults, diff, handoff, compose, gap-report.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seclib import assemble, catalog, gates, report  # noqa: E402
from seclib.common import (  # noqa: E402
    EXIT_FLAG_REQUIRED,
    EXIT_GATES,
    EXIT_OK,
    EXIT_PATTERN_NOT_FOUND,
    SectionError,
    dump_json,
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
        f"теги с докой атрибутов: {cov.get('tags_documented')}/{cov.get('tags_total')} "
        f"(attributes_source: {cov.get('attributes_source')})"
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


def cmd_patterns(args: argparse.Namespace) -> int:
    if args.patterns_cmd == "list":
        patterns = catalog.load_all()
        if not patterns:
            print("каталог паттернов пуст")
            return EXIT_GATES
        print(f"{'id':28} {'страницы':16} назначение")
        for p in patterns:
            print(f"{p.id:28} {p.meta.get('pages', '—'):16} {p.meta.get('purpose', '')}")
        return EXIT_OK
    if args.patterns_cmd == "show":
        p = catalog.get(args.id)
        print(f"id: {p.id}\nversion: {p.meta.get('version')}\ntitle: {p.meta.get('title')}")
        print(f"purpose: {p.meta.get('purpose')}")
        print(f"origin: {p.meta.get('origin')}")
        print(f"applied_patches: {p.meta.get('applied_patches') or '—'}")
        print(f"answers_required: {p.meta.get('answers_required')}")
        if args.params:
            print("--- answers.schema.json ---")
            print(dump_json(p.answers_schema), end="")
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
        if not scored:
            print(
                "подходящего паттерна нет. НЕ выдумывать html молча: честный путь — "
                "сказать пользователю и, если он подтвердил, собрать через "
                "freeform --confirm (гейты те же, что у new); "
                "gap-report для бэклога каталога — ещё заглушка."
            )
            return EXIT_PATTERN_NOT_FOUND
        for p, score in scored:
            print(f"{p.id:28} score={score}  {p.meta.get('purpose', '')}")
        print("скрипт только сузил кандидатов; выбор делает модель")
        return EXIT_OK
    raise SectionError(f"неизвестная подкоманда patterns {args.patterns_cmd!r}")


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


def _empty_model_fields(node: object, path: str = "") -> list[str]:
    """Пути пустых модельных заглушек: изображение без fileId, ссылка без link,
    источник товаров без id/slug."""
    found: list[str] = []
    if isinstance(node, dict):
        keys = set(node.keys())
        if {"fileId", "filePath"} <= keys and not node.get("fileId") and not node.get("filePath"):
            found.append(f"{path or '<root>'} — изображение (объект из kit.py media upload)")
            return found
        if {"link", "linkId"} <= keys and not node.get("link"):
            found.append(f"{path or '<root>'} — ссылка (объект {{link, linkId}})")
            return found
        if {"id", "slug", "type"} <= keys and not node.get("id") and not node.get("slug"):
            found.append(f"{path or '<root>'} — источник товаров (категория/подборка магазина)")
            return found
        for key, value in node.items():
            found += _empty_model_fields(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += _empty_model_fields(item, f"{path}[{i}]")
    return found


def cmd_new(args: argparse.Namespace) -> int:
    registry = load_registry()
    pattern = catalog.get(args.pattern)
    answers = read_json(Path(args.answers))
    if not isinstance(answers, dict):
        raise SectionError("answers.json должен быть JSON-объектом")

    settings, errors = assemble.build_settings(pattern, answers)
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
        "alias": "",
    }
    create = assemble.build_create_json(title, html, css, schema, mode="new")

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
    )
    errors_found = [f for f in findings if f["severity"] == "error"]
    if errors_found:
        report.print_findings(findings, as_json=args.json)
        print("new: гейты не прошли — ничего не записано")
        return EXIT_GATES

    out_dir = Path(args.out)
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
    print(f"new: секция из паттерна {pattern.id} собрана в {out_dir}")

    # Пустые модельные заглушки в settings — то, что обязан заполнить мерчант
    # (в форме кабинета) или агент (в answers объектом из kit.py media upload и
    # т.п.). Молчать о них нельзя: иначе страница соберётся без картинок и товаров.
    placeholders = _empty_model_fields(settings)
    if placeholders:
        print("заполнить мерчанту или через answers (сейчас пустые заглушки):")
        for path_str in placeholders:
            print(f"  - {path_str}")
    _print_handoff(out_dir)
    return EXIT_OK


def _print_handoff(out_dir: Path) -> None:
    print("залить может только kit-storefront-constructor; последовательность команд:")
    print("  # ВНИМАНИЕ: по умолчанию kit.py смотрит в ПРОД. Нужный контур —")
    print("  # либо --base-url <контур>, либо KIT_API_BASE_URL. Проверить: kit.py env")
    print(f"  kit.py templates create --file {out_dir}/create.json --dry-run")
    print(f"  kit.py templates create --file {out_dir}/create.json --confirm   # вернёт template_id")
    print(f"  kit.py content pull --out {out_dir}/work.json   # обязательно повторно после create")
    print(
        f"  kit.py section add --work {out_dir}/work.json --page <alias> "
        f"--widget YandexKit.Mystique --template-id <uuid> "
        f"--settings-file {out_dir}/settings.json"
    )
    print(f"  kit.py content diff --work {out_dir}/work.json")
    print(f"  kit.py content push --work {out_dir}/work.json --commit-message '<текст>' --dry-run")
    print(
        f"  kit.py content push --work {out_dir}/work.json --commit-message '<текст>' "
        f"--no-publish --confirm"
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
    meta = {"pattern": None, "mode": "freeform", "alias": ""}
    create = assemble.build_create_json(title, html, css, schema, mode="freeform")

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
    print(f"freeform: секция собрана в {out_dir} (mode=freeform — html написан моделью)")
    if settings:
        placeholders = _empty_model_fields(settings)
        if placeholders:
            print("заполнить мерчанту или через answers (сейчас пустые заглушки):")
            for path_str in placeholders:
                print(f"  - {path_str}")
    _print_handoff(out_dir)
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
        print(f"{name} ({group}); атрибуты по доке: {', '.join(info['attributes']) or 'таблицы в доке нет'}")
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
            print(f"{ref}: резервный тип — даёт контрол мерчанта")
            return EXIT_OK
        if ref in registry["service_refs"]:
            print(f"{ref}: служебный тип — работает, но инлайнится")
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
    pm = psub.add_parser("match")
    pm.add_argument("query")
    p.set_defaults(func=cmd_patterns)

    p = sub.add_parser("new", help="собрать секцию из паттерна")
    p.add_argument("--pattern", required=True)
    p.add_argument("--answers", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--json", action="store_true")
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
    p.set_defaults(func=cmd_freeform, strict=False)

    p = sub.add_parser("lint", help="офлайн-гейты над тройкой html+css+schema")
    p.add_argument("dir")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--against-settings", default=None, help="итерация 2 (G14)")
    p.add_argument("--allow-unknown-attrs", action="store_true")
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
