"""Estimate how tall a page is on a phone, from section settings alone.

Native sections have no touch-specific height, padding or type size: the phone layout
follows from the desktop settings. This model turns those settings into pixels at a
390 × 664 CSS px viewport (an iPhone-sized screen with the browser chrome taken off): one
«screen» here is 664 px, not the 844 px of a full-height 390 × 844 capture. Against
full-page phone captures of the reference sample's home pages it tracks the real length with
a correlation of about 0.93; it is an estimate, not a measurement, and not a bound in either
direction. What it had to assume (a catalog's category count, a custom section's height) is
listed by `assumptions`.
"""

from __future__ import annotations

import math

from designlib.common import get_path

VIEWPORT_W = 390
VIEWPORT_H = 664
HEADER_H = 56
BAR_H = 32
FOOTER_H = 450
CAPTION_H = 30
CARD_TEXT_H = 75
CARD_BUTTON_H = 45
TITLE_H = 50
PADDING_FACTOR = {"none": 0, "single": 1, "double": 2}
MOBILE_PAIR = {"2:1": "4:3", "5:2": "4:3", "5:1": "5:2", "10:1": "7:1", "7:2": "7:3", "fit-screen": "fit-screen"}
CATALOG_TILES_ASSUMED = 8
CUSTOM_H = 500
# Phone heights of kit-custom-sections patterns at 390 px, from each pattern's own description of
# its phone layout (preview.md there) with typical content; a custom section of another pattern,
# or one pulled without its pattern, is CUSTOM_H.
PATTERN_H = {
    "ticker-band": 90,
    "running-line": 90,
    "type-band": 150,
    "stats-band": 300,
    "testimonial-spotlight": 380,
    "hero-overlay": 390,
    "tile-mosaic": 600,
    "video-reels": 600,
    "text-media-split": 650,
    "site-header": HEADER_H,
    "site-footer": FOOTER_H,
}


def _ratio(value: object, default: float) -> "float | None":
    """Width / height; None for fit-screen (the viewport height)."""
    if not isinstance(value, str) or not value or value == "default":
        return default
    if value == "fit-screen":
        return None
    try:
        width, height = value.split(":")
        return float(width) / float(height)
    except ValueError:
        return default


def _token(value: object, default: float) -> float:
    """Spacing token → px: 100 is 8 px."""
    try:
        return int(str(value)) * 8 / 100
    except ValueError:
        return default


class Theme:
    """The theme values the phone layout depends on; platform defaults when unknown."""

    def __init__(self, settings: "dict[str, object] | None") -> None:
        settings = settings if isinstance(settings, dict) else {}
        self.gutter = _token(get_path(settings, "appearance.section.hPaddingMobile"), 16)
        v_padding = get_path(settings, "appearance.section.vPadding")
        try:
            v_padding = int(str(v_padding))
        except ValueError:
            v_padding = 400
        # One `single` side on a phone: about three quarters of the desktop value.
        self.single = v_padding * 0.04 * 0.75
        self.tile_ratio = _ratio(get_path(settings, "collectionSnippet.aspectRatio"), 0.75) or 0.75
        self.photo_ratio = _ratio(get_path(settings, "productSnippet.photo.aspectRatio"), 0.75) or 0.75
        buttons = get_path(settings, "productSnippet.buttons.buttonColorInFeaturedProducts")
        self.shelf_buttons = buttons not in (None, "none")
        self.shelf_buttons_known = buttons is not None


def section_height(widget: str, settings: "dict[str, object]", theme: Theme, pattern: "str | None" = None) -> float:
    """Estimated phone height of one section in CSS px, paddings included."""
    short = widget.split(".", 1)[-1]
    section = settings.get("section") if isinstance(settings.get("section"), dict) else {}
    edge = section.get("isFullscreen") in ("enabled", True) and str(section.get("hPaddingMobile")) == "0"  # type: ignore[union-attr]
    width = VIEWPORT_W if edge else VIEWPORT_W - 2 * theme.gutter
    padding = (
        PADDING_FACTOR.get(str(section.get("paddingTop", "single")), 1)  # type: ignore[union-attr]
        + PADDING_FACTOR.get(str(section.get("paddingBottom", "single")), 1)  # type: ignore[union-attr]
    ) * theme.single
    if short == "AnnouncementBar":
        return BAR_H if settings.get("modules") else 0
    if short == "Header":
        return HEADER_H
    if short == "Footer":
        return FOOTER_H
    if short == "Mystique":
        return PATTERN_H.get(pattern or "", CUSTOM_H) + padding
    if short == "BannersCarousel":
        ratios = settings.get("aspectRatios")
        if isinstance(ratios, dict):
            mobile = ratios.get("mobile")
        else:
            mobile = MOBILE_PAIR.get(str(settings.get("aspectRatio")), "4:3")
        ratio = _ratio(mobile, 4 / 3)
        return (VIEWPORT_H - HEADER_H if ratio is None else width / ratio) + padding
    if short in ("FeaturedCollections", "CollectionsListing"):
        return _tiles_height(settings, theme, width) + padding
    if short == "FeaturedProducts":
        return _shelf_height(settings, theme, width) + padding
    if short == "RichText":
        text = str(settings.get("content") or "")
        return 30 + math.ceil(len(text) / 40) * 22 + padding
    if short == "ImageWithText":
        text = str(settings.get("content") or "")
        ratio = _ratio(get_path(settings, "image.aspectRatio"), 1.0) or 1.0
        return width / ratio + 40 + math.ceil(len(text) / 40) * 22 + padding
    if short == "EmailSubscription":
        return 420 + padding
    if short == "Faq":
        return 400 + padding
    return 300 + padding


def _tiles_height(settings: "dict[str, object]", theme: Theme, width: float) -> float:
    modules = settings.get("modules")
    count = len(modules) if isinstance(modules, list) else 0
    if count == 0 and settings.get("source") is None:
        return 0
    count = count or CATALOG_TILES_ASSUMED  # a catalog grid lists the store's categories, which the settings do not show
    ratio = _ratio(get_path(settings, "snippet.aspectRatio"), theme.tile_ratio) or theme.tile_ratio
    columns = get_path(settings, "snippets.columns") or 4
    two_up = get_path(settings, "snippets.enableTwoColumnsOnTouch") is not False  # schema default: true
    layout = get_path(settings, "snippets.layout") or "grid"
    caption = CAPTION_H if get_path(settings, "text.enableTitle") and get_path(settings, "text.vAlign") == "underImage" else 0
    if layout == "carousel":
        tile = width / 2.2 if two_up else width * 0.85
        height = tile / ratio + caption
    else:
        per_row = 2 if two_up and isinstance(columns, int) and columns >= 2 else 1
        height = math.ceil(count / per_row) * ((width / per_row) / ratio + caption + 8)
    if str(settings.get("title") or "").strip():
        height += TITLE_H
    return height


def _shelf_height(settings: "dict[str, object]", theme: Theme, width: float) -> float:
    count = get_path(settings, "snippets.count") or 8
    two_up = get_path(settings, "snippets.enableTwoColumnsOnTouch") is not False  # schema default: true
    layout = get_path(settings, "snippets.layout") or "grid"
    button = get_path(settings, "snippet.enableButton") or "default"
    text = CARD_TEXT_H + (CARD_BUTTON_H if button == "enabled" or (button == "default" and theme.shelf_buttons) else 0)
    if layout == "carousel":
        card = width / 2.2 if two_up else width * 0.85
        height = card / theme.photo_ratio + text
    else:
        per_row = 2 if two_up else 1
        rows = math.ceil(int(count) / per_row) if isinstance(count, int) else 4
        height = rows * ((width / per_row) / theme.photo_ratio + text + 12)
    if str(settings.get("titleText") or "").strip():
        height += TITLE_H
    return height


def assumption(widget: str, settings: "dict[str, object]", pattern: "str | None" = None) -> "str | None":
    """What the estimate of this section had to assume, or None when the settings say it all."""
    short = widget.split(".", 1)[-1]
    modules = settings.get("modules")
    if short in ("FeaturedCollections", "CollectionsListing") and not (isinstance(modules, list) and modules) and settings.get("source") is not None:
        return f"{short} lists the store's categories: {CATALOG_TILES_ASSUMED} assumed"
    if short == "Mystique" and (pattern or "") not in PATTERN_H:
        return f"custom section{' (pattern ' + pattern + ')' if pattern else ''}: {CUSTOM_H} px assumed"
    return None


def touch_rows(settings: "dict[str, object]") -> "int | None":
    """Rows a product grid takes on a phone; None for a carousel or a shelf without a count."""
    if get_path(settings, "snippets.layout") != "grid":
        return None
    count = get_path(settings, "snippets.count")
    if not isinstance(count, int):
        return None
    return math.ceil(count / (1 if get_path(settings, "snippets.enableTwoColumnsOnTouch") is False else 2))


def screens(pixels: float) -> float:
    return pixels / VIEWPORT_H


def banner_height(ratio: str) -> int:
    """Height in CSS px of a full-width banner image at the 390 px viewport."""
    value = _ratio(ratio, 4 / 3)
    return VIEWPORT_H if value is None else round(VIEWPORT_W / value)
