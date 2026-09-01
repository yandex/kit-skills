# Сценарии и передача соседу

## Новая секция

1. `section.py doctor` — свежесть знания.
2. `patterns match "<запрос>"` → выбрать паттерн (или честный отказ при exit 5).
3. `patterns show <id> --params` → собрать `answers.json` по answers-схеме.
   Модельные поля передаются объектами: изображение — из вывода `kit.py media
   upload --confirm` (`{fileId, filePath}`), ссылка — `{link, linkId}`, источник
   товаров — `{id, slug, type}` категории магазина. Без них `new` честно печатает
   список пустых заглушек «заполнить мерчанту или через answers».
   Вертикальный ритм секции — `verticalRhythm`/`verticalRhythmTouch`
   (spacing-токены; дефолт 500/300 даёт 80px/48px между секциями).
4. `new --pattern <id> --answers answers.json --out DIR` — гейты гоняются сразу; при провале ничего не пишется.
5. Передать соседу файлом, не вызовом — команды печатает `new`:

```
# КОНТУР: по умолчанию kit.py смотрит в ПРОД. Дев/тест — только явным
# --base-url <контур> или KIT_API_BASE_URL. Проверить перед записью: kit.py env
kit.py templates create --file DIR/create.json --dry-run
kit.py templates create --file DIR/create.json --confirm      # вернёт template_id
kit.py content pull --out DIR/work.json  # ОБЯЗАТЕЛЬНО повторно: свежесозданный шаблон
                                         # ищется в снимке базы воркспейса
kit.py section add --work DIR/work.json --page <alias> \
       --widget YandexKit.Mystique --template-id <uuid> \
       --settings-file DIR/settings.json          # settings.json из `new` — не забыть
kit.py content diff --work DIR/work.json
kit.py content push --work DIR/work.json --commit-message '<текст>' --dry-run
kit.py content push --work DIR/work.json --commit-message '<текст>' --no-publish --confirm
kit.py versions content --id <version_id>
```

`--work`, `--settings-file` и `--commit-message` у соседа **обязательные** — без них команда не запускается вовсе.
`--confirm` не подставлять и чужой скрипт не запускать — решение за пользователем.
В `commit_message` уже проставлен маркер происхождения от скила: это единственный канал маркировки (автор версии на бэке восстанавливается из владельца токена и от человека неотличим).

## Свободная секция (freeform)

Только когда `patterns match` вернул пусто и пользователь подтвердил свободную
сборку: `freeform --html s.html --css s.css --schema s.json [--settings s.json]
[--title "…"] --out DIR --confirm`. Без `--confirm` — exit 3. Гейты те же, что у
`new` (хардкод цвета — WARNING, паттерна и G11 нет); `--settings` дополнительно
гоняет G8 и печатает пустые модельные заглушки. В `meta.json` и `commit_message`
проставляется `mode=freeform`. Помнить про G17 (each только с `as |m|`) и G18
(`//` и внешние url в css запрещены — свой шрифт из секции не подключить).

## Проверка чужого шаблона

`kit.py templates export --id <uuid> --out DIR` (+ `kit.py section show` для живых настроек) → `section.py lint DIR [--json]`. Линт читает обе раскладки имён: свою (`template.html` / `styles.css` / `schema.json` / `meta.json`) и раскладку экспорта соседа (`template.html` / `template.css` / `template.schema.json` / `template.meta.json`).

Осторожно с G12 на чужом экспорте: живой шаблон витрины может иметь непустой `alias` (`YandexKit.Header`/`Footer` и т. п.) осознанно. Для чужой секции это факт «шаблон подменяет системный блок», а не обязательно дефект автора.

## Правка залитой секции (итерация 2: adopt/edit)

Правка = **новый шаблон + rebind**, никогда не правка живого (см. manual/rules.md).
Путь после реализации adopt: export → `adopt` → правки → `lint --against-settings живые-settings.json` → `handoff` → create + `section set template-id`. До реализации adopt — честно сказать, что автоматизированной правки ещё нет.
