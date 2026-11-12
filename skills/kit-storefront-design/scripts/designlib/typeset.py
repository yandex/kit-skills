"""Type on the page: title alignment against the theme, line length, the theme font, the page's H1.

`presets apply` and the lint share these decisions, so a plan that names a preset is judged
as `presets apply` will write it. Everything here reads settings only; the storefront rules
it mirrors are stated next to each function.
"""

from __future__ import annotations

import re

from designlib.common import CENTRED_MAX_CHARS, get_path

RT = "YandexKit.RichText"
IWT = "YandexKit.ImageWithText"
FP = "YandexKit.FeaturedProducts"
FC = "YandexKit.FeaturedCollections"
CL = "YandexKit.CollectionsListing"
FB = "YandexKit.FeaturedBlogs"
BLOG = "YandexKit.Blog"
BLOG_LISTING = "YandexKit.BlogListing"
FAQ = "YandexKit.Faq"
ES = "YandexKit.EmailSubscription"
PL = "YandexKit.ProductsListing"
MYSTIQUE = "YandexKit.Mystique"

# Where each widget keeps its section title and its alignment (`default` follows the theme).
TITLE_TEXT = {RT: "titleText", IWT: "titleText", FP: "titleText", FB: "titleText", FC: "title", CL: "title", BLOG_LISTING: "title", FAQ: "title"}
TITLE_ALIGN = {RT: "titleAlign", IWT: "titleAlign", FP: "titleAlign", FB: "titleAlign", FC: "alignTitle", CL: "alignTitle", BLOG_LISTING: "alignTitle"}

# A long text reads at 60-80 characters a line. In the default 1400 px box, RichText `columns` 6
# is about 650 px (about 75 characters at 15-16 px); 8 columns run past 110.
MEASURE_COLUMNS = 6
MEASURE_MAX_COLUMNS = 7
MEASURE_LONG_TEXT = 400
FAQ_COLUMNS = 8

# Desktop widths, CSS px. A section's rows are capped at the theme's `maxWidthDesktop` (1400 by
# default, side padding included) unless the section spans the screen: `section.isFullscreen`
# `enabled`, or `default` under a theme with `appearance.section.isFullscreen: true` (the
# storefront's SectionLayout sets `--ys-section-max-width: 100%`). A spanning section is as wide as
# the window, judged here at 1920 px, the common wide desktop; text presets write `disabled`.
DESKTOP_WINDOW = 1920
THEME_MAX_WIDTH = 1400
THEME_H_PADDING = "200"
SPACING_PX = {
    "0": 0, "25": 2, "50": 4, "75": 6, "100": 8, "150": 12, "200": 16, "250": 20, "300": 24, "350": 28,
    "400": 32, "450": 36, "500": 40, "600": 48, "700": 56, "800": 64, "900": 72, "1000": 80, "1100": 96,
    "1200": 112, "1300": 128, "1400": 144, "1500": 160, "1600": 176, "1700": 192, "1800": 208, "1900": 224,
    "2000": 240,
}
# The side paddings a section may write (`section.hPaddingDesktop`, SectionHPadding), narrowest last,
# and the fewest of the 12 grid columns RichText and Faq accept (`columns` 5-12).
SECTION_H_PADDINGS = ("0", "200", "400", "800", "1000", "1100", "1200", "1400", "1500", "1600")
MIN_COLUMNS = 5
IWT_GAP = 48  # ImageWithText: the gap between its two columns on desktop
STORY_NARROW = "1400"  # the smallest hPaddingDesktop that narrows a story split in the default box
STORY_PADDING = "1500"  # what `brand-story-split` writes
_DEFAULT_CONTENT = THEME_MAX_WIDTH - 2 * SPACING_PX[THEME_H_PADDING]
# The widest line each rule accepts, in px: what the default 1400 px box gives at the accepted
# setting. A wider theme box (`maxWidthDesktop` 1728 or 1920) keeps the limit, not the columns.
MEASURE_MAX_PX = _DEFAULT_CONTENT * MEASURE_MAX_COLUMNS / 12
FAQ_MAX_PX = _DEFAULT_CONTENT * FAQ_COLUMNS / 12
STORY_MAX_PX = (THEME_MAX_WIDTH - 2 * SPACING_PX[STORY_NARROW] - IWT_GAP) / 2
# The reading line the advice names: 6 columns of the default box; a story column in it at hPaddingDesktop 1500.
READING_PX = _DEFAULT_CONTENT * MEASURE_COLUMNS / 12
STORY_READING_PX = (THEME_MAX_WIDTH - 2 * SPACING_PX[STORY_PADDING] - IWT_GAP) / 2


def spans_screen(settings: "dict[str, object]", theme: "dict[str, object] | None") -> bool:
    """Whether the section runs as wide as the window: its own flag, else the theme's."""
    flag = get_path(settings, "section.isFullscreen")
    if flag in ("enabled", True):
        return True
    if flag in ("disabled", False):
        return False
    return get_path(theme, "appearance.section.isFullscreen") is True


def theme_box(theme: "dict[str, object] | None", window: int = DESKTOP_WINDOW) -> int:
    """The box of every section that does not span the screen: the theme's `maxWidthDesktop`
    (1400 by default, side padding included), no wider than the window."""
    try:
        max_width = int(str(get_path(theme, "appearance.section.maxWidthDesktop", THEME_MAX_WIDTH)))
    except ValueError:
        max_width = THEME_MAX_WIDTH
    return min(window, max_width)


def side_padding(settings: "dict[str, object]", theme: "dict[str, object] | None") -> str:
    """The section's desktop side padding as a spacing token: its own `hPaddingDesktop`, else the theme's, else `200`."""
    padding = get_path(settings, "section.hPaddingDesktop")
    if padding in (None, ""):
        padding = get_path(theme, "appearance.section.hPaddingDesktop")
    return str(padding) if str(padding) in SPACING_PX else THEME_H_PADDING


def content_width(settings: "dict[str, object]", theme: "dict[str, object] | None", window: int = DESKTOP_WINDOW) -> "tuple[float, bool]":
    """The width of the section's content row on a desktop window, and whether it spans the screen.

    The box is the window for a spanning section, else the theme's `maxWidthDesktop`; the side
    padding is the section's `hPaddingDesktop`, else the theme's (`200` by default)."""
    full = spans_screen(settings, theme)
    box = window if full else theme_box(theme, window)
    return box - 2 * SPACING_PX[side_padding(settings, theme)], full


def _unspan(settings: "dict[str, object]", theme: "dict[str, object] | None") -> "dict[str, object]":
    """`isFullscreen: disabled` for a section that spans the screen, when the theme's box is narrower than the window."""
    return {"isFullscreen": "disabled"} if spans_screen(settings, theme) and theme_box(theme) < DESKTOP_WINDOW else {}


def _wider_paddings(current: str) -> "list[str]":
    """The section paddings from the current one to the widest: what narrows the row, least first."""
    return [token for token in SECTION_H_PADDINGS if SPACING_PX[token] >= SPACING_PX[current]]


def fit_line(
    settings: "dict[str, object]", theme: "dict[str, object] | None", columns: int, prefer: int, max_px: float
) -> "dict[str, object] | None":
    """The least change that brings a line of `columns` of 12 within `max_px` on a desktop window.

    Keys of the result, only those that change: `isFullscreen` (`disabled`, for a section that
    spans the screen, unless the theme's box is as wide as the window), `columns`,
    `hPaddingDesktop`. The section's own columns first, then from `prefer` down to the grid's 5
    at its side padding; a wider padding only when 5 columns of the box are still too wide.
    Empty when the line fits; None when no setting of the section fits (only the theme's
    `maxWidthDesktop` would)."""
    changes = _unspan(settings, theme)
    box = theme_box(theme)
    current = side_padding(settings, theme)
    counts = [columns] + list(range(min(columns - 1, prefer), MIN_COLUMNS - 1, -1))
    for token in _wider_paddings(current):
        for count in counts:
            if (box - 2 * SPACING_PX[token]) * count / 12 <= max_px + 1:
                if count != columns:
                    changes["columns"] = count
                if token != current:
                    changes["hPaddingDesktop"] = token
                return changes
    return None


def story_column(width: float) -> float:
    """The text column of a 50/50 ImageWithText in a content row `width` px wide."""
    return (width - IWT_GAP) / 2


def fit_story(settings: "dict[str, object]", theme: "dict[str, object] | None") -> "dict[str, object] | None":
    """The least change that brings the text column of a 50/50 ImageWithText within a reading column.

    Keys as in `fit_line`: `isFullscreen` (`disabled`) for a spanning section, `hPaddingDesktop`
    (1500 as `brand-story-split` writes it, else the next one that fits). ImageWithText has no
    columns: the side padding is its only width. None when even the widest padding the section
    may write leaves too wide a column in the theme's box (1728 px and up)."""
    changes = _unspan(settings, theme)
    box = theme_box(theme)
    current = side_padding(settings, theme)
    if story_column(box - 2 * SPACING_PX[current]) <= STORY_MAX_PX + 1:
        return changes
    floor = SPACING_PX[STORY_PADDING]
    for token in _wider_paddings(current):
        if token != current and SPACING_PX[token] >= floor and story_column(box - 2 * SPACING_PX[token]) <= STORY_MAX_PX + 1:
            changes["hPaddingDesktop"] = token
            return changes
    return None


def narrowest_story_column(theme: "dict[str, object] | None") -> float:
    """The story column in the theme's box at the widest side padding a section may write."""
    return story_column(theme_box(theme) - 2 * SPACING_PX[SECTION_H_PADDINGS[-1]])


def fit_measure_to_theme(
    widget: str,
    preset_settings: "dict[str, object]",
    settings: "dict[str, object]",
    theme: "dict[str, object] | None",
    kept: "dict[str, object] | None" = None,
) -> "list[str]":
    """Keep a text preset's reading line in a theme box wider than the default 1400 px.

    The text presets hold the measure in the default box: RichText at 7 columns or fewer, Faq
    at 8 or fewer, an ImageWithText narrowed to hPaddingDesktop 1400 or more. A theme's
    `maxWidthDesktop` 1728 or 1920 widens every boxed section, and the same columns run
    850-950 px. Columns go down first (to 5, the grid's fewest), then the side padding grows.
    A section that spans the screen, or whose plan sets the key itself (`kept`, the plan's own
    settings), is left as it is: the lint reports it. A 50/50 ImageWithText has no setting
    that reaches a reading column in such a box: a note says so and nothing changes."""
    kept = kept if isinstance(kept, dict) else {}
    if not isinstance(theme, dict) or spans_screen(settings, theme):
        return []
    if any(get_path(kept, key) not in (None, "") for key in ("section.isFullscreen", "section.hPaddingDesktop")):
        return []
    name = widget.split(".", 1)[-1]
    box = theme_box(theme)
    if widget in (RT, FAQ):
        key = "columns" if widget == RT else "appearance.columns"
        limit, max_px = (MEASURE_MAX_COLUMNS, MEASURE_MAX_PX) if widget == RT else (FAQ_COLUMNS, FAQ_MAX_PX)
        columns = get_path(settings, key)
        ours = get_path(preset_settings, key)
        if not isinstance(ours, int) or ours > limit or columns != ours or get_path(kept, key) is not None:
            return []
        changes = fit_line(settings, theme, columns, columns, max_px)
        if not changes:
            return []
        notes = [f"{name}: the theme's box is {box} px (maxWidthDesktop), wider than the default 1400 px"]
        if "columns" in changes:
            _set_path(settings, key, changes["columns"])
            notes.append(f"{key} {columns} → {changes['columns']}")
        if "hPaddingDesktop" in changes:
            _set_path(settings, "section.hPaddingDesktop", changes["hPaddingDesktop"])
            notes.append(f"section.hPaddingDesktop → {changes['hPaddingDesktop']!r}")
        return [notes[0] + ": " + ", ".join(notes[1:]) + f", a line of about {round(max_px, -1):.0f} px at most"]
    if widget == IWT:
        ours = get_path(preset_settings, "section.hPaddingDesktop")
        if str(ours) not in SPACING_PX or SPACING_PX[str(ours)] < SPACING_PX[STORY_NARROW]:
            return []
        changes = fit_story(settings, theme)
        if changes is None:
            return [
                f"{name}: the theme's box is {box} px (maxWidthDesktop): even hPaddingDesktop "
                f"{SECTION_H_PADDINGS[-1]} leaves a text column of about {round(narrowest_story_column(theme))} px, where a "
                f"reading column is about {round(STORY_READING_PX, -1):.0f} px. Keep the story within {CENTRED_MAX_CHARS} characters "
                "or use the kit-custom-sections pattern `text-media-split`"
            ]
        if "hPaddingDesktop" in changes:
            _set_path(settings, "section.hPaddingDesktop", changes["hPaddingDesktop"])
            return [f"{name}: the theme's box is {box} px (maxWidthDesktop): section.hPaddingDesktop → {changes['hPaddingDesktop']!r} keeps a reading column"]
    return []


def _set_path(settings: "dict[str, object]", dotted: str, value: object) -> None:
    node = settings
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


ITALIC_WORDS = re.compile(r"italic|itallic|oblique|курсив|display|script|\bhand", re.I)


def theme_title_align(theme: "dict[str, object] | None") -> "str | None":
    """`typography.titleAlign` of the theme; the schema default `left` when the theme says
    nothing; None when the plan shows no theme at all."""
    if not isinstance(theme, dict):
        return None
    value = get_path(theme, "typography.titleAlign")
    return value if value in ("left", "center") else "left"


def section_title(widget: str, settings: "dict[str, object]") -> "str | None":
    """The section's own title text, or None when the settings do not carry the key."""
    key = TITLE_TEXT.get(widget)
    if key is None or key not in settings:
        return None
    value = settings.get(key)
    return value.strip() if isinstance(value, str) else ""


def title_align(widget: str, settings: "dict[str, object]", theme_align: "str | None") -> "str | None":
    """Where the storefront puts this section's title: left, center, or None when unknown.

    Faq reads only its own `appearance.align` (centred at `center`, left otherwise), never
    the theme; the listing's H1 is always on the left; `default` follows the theme."""
    if widget == FAQ:
        return "center" if get_path(settings, "appearance.align") == "center" else "left"
    if widget == PL:
        return "left"
    key = TITLE_ALIGN.get(widget)
    if key is None:
        return None
    value = settings.get(key)
    if value in ("left", "center"):
        return str(value)
    return theme_align


def fit_faq_to_theme(settings: "dict[str, object]", theme_align: "str | None") -> "list[str]":
    """Under a centred theme a titled left Faq is the one title on the left: centre the block.

    The Faq title follows only `appearance.align`, so the theme cannot centre it; `center`
    with 8 columns matches the other titles and keeps the questions to a readable width.
    An untitled Faq (a help-page chain, service rows) keeps its alignment."""
    if theme_align != "center" or settings.get("title") == "":
        return []
    appearance = settings.get("appearance")
    if not isinstance(appearance, dict) or appearance.get("align", "left") != "left":
        return []
    appearance["align"] = "center"
    notes = ["Faq: the theme centres titles (typography.titleAlign center) and a Faq title follows only appearance.align: wrote align center"]
    columns = appearance.get("columns")
    if isinstance(columns, int) and columns > FAQ_COLUMNS:
        appearance["columns"] = FAQ_COLUMNS
        notes.append(f"Faq: columns {columns} → {FAQ_COLUMNS}, a readable width for questions")
    return notes


def describe_font(theme: "dict[str, object] | None") -> "dict[str, object] | None":
    """The theme font as the storefront picks it: the selected custom font, else the predefined one.

    Returns kind, name, the uploaded files per style and weight, and `risk` when the text
    face is italic or decorative (by its name or by the names of its uploaded files)."""
    family = get_path(theme, "typography.fontFamily") if isinstance(theme, dict) else None
    if not isinstance(family, dict):
        return None
    custom = family.get("custom") if isinstance(family.get("custom"), dict) else {}
    predefined = family.get("predefined") if isinstance(family.get("predefined"), dict) else {}
    kind, chosen = ("custom", custom) if custom.get("isSelected") is True else ("predefined", predefined)  # type: ignore[union-attr]
    name = str(chosen.get("name") or "").strip()  # type: ignore[union-attr]
    files: "list[str]" = []
    italic_files = 0
    sources = chosen.get("sources") if isinstance(chosen.get("sources"), dict) else {}  # type: ignore[union-attr]
    for style, weights in (sources or {}).items():  # type: ignore[union-attr]
        for weight, media in (weights.items() if isinstance(weights, dict) else []):
            path = media.get("filePath") if isinstance(media, dict) else None
            if isinstance(path, str) and path.strip():
                files.append(f"{style} {weight}")
                if style == "italic" or ITALIC_WORDS.search(path.rsplit("/", 1)[-1]):
                    italic_files += 1
    risk = None
    if ITALIC_WORDS.search(name):
        risk = f"the font name {name!r} reads as an italic or decorative face"
    elif kind == "custom" and files and italic_files == len(files):
        risk = "every uploaded file of the custom font is an italic"
    return {"kind": kind, "name": name or "(unnamed)", "files": files, "risk": risk}


def font_line(font: "dict[str, object] | None") -> str:
    if not font:
        return "theme font: not in the workspace"
    files = ", ".join(font["files"]) if font["files"] else "platform files"  # type: ignore[arg-type]
    return f"theme font: {font['kind']} {font['name']!r} ({files})"


FONT_ADVICE = (
    "Body text set in an italic or decorative face is hard to read: ask the owner to choose an upright text font in the "
    "cabinet theme editor (fonts are uploaded per weight there; this skill never writes typography.fontFamily)."
)
