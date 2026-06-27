"""
泰拉瑞亚 Wiki 查询插件（离线版）
==================================
指令: 泰拉查询 <物品名称>（无需 / 前缀）
功能: 从本地离线数据库查询物品，以图片卡片展示属性与合成配方
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime

from croniter import croniter
from PIL import Image, ImageDraw, ImageFont

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig, logger

from .prepare_data import (
    COIN_SPECS,
    RARITY_LABELS,
    description_text_to_rich,
    drops_display_block,
    normalize_stat_for_display,
    resolve_bool_icon,
    update_wiki_data,
    merge_en_recipe,
)

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_PLUGIN_DIR, "assets", "fonts")
_COIN_DIR = os.path.join(_PLUGIN_DIR, "assets", "coins")
_ICON_DIR = os.path.join(_PLUGIN_DIR, "assets", "icons")
_COIN_ICON_SIZE = (18, 18)
_BOOL_ICON_SIZE = (20, 20)
_INLINE_ICON_SIZE = (18, 16)
_BUNDLED_FONT_CANDIDATES = (
    os.path.join(_FONT_DIR, "NotoSansSC-Bold.otf"),
    os.path.join(_FONT_DIR, "NotoSansSC-Regular.otf"),
    os.path.join(_FONT_DIR, "NotoSansSC-Regular.ttf"),
    os.path.join(_FONT_DIR, "wqy-microhei.ttc"),
)
_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_RESOLVED_FONT_PATH: str | None = None


def _resolve_font_path() -> str | None:
    global _RESOLVED_FONT_PATH
    if _RESOLVED_FONT_PATH is not None:
        return _RESOLVED_FONT_PATH or None

    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        *list(_BUNDLED_FONT_CANDIDATES),
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, 16)
                _RESOLVED_FONT_PATH = path
                return path
            except Exception:
                continue

    logger.warning("未找到可用中文字体，卡片中文可能显示为方框")
    _RESOLVED_FONT_PATH = ""
    return None


def _try_get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]

    font_path = _resolve_font_path()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, size)
            _FONT_CACHE[size] = font
            return font
        except Exception as e:
            logger.warning(f"加载字体失败 ({font_path}): {e}")

    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def _resolve_data_dir() -> str:
    candidates = [
        os.path.join(_PLUGIN_DIR, "data", "terraria_query"),
        os.path.join(os.getcwd(), "data", "terraria_query"),
    ]
    for d in candidates:
        if os.path.exists(os.path.join(d, "items.json")):
            return d
    return candidates[0]


DATA_DIR = _resolve_data_dir()
ITEMS_JSON = os.path.join(DATA_DIR, "items.json")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
CARDS_DIR = os.path.join(DATA_DIR, "cards")

CARD_WIDTH = 600
CARD_PADDING = 20
ROW_HEIGHT = 32
STAT_LINE_HEIGHT = 22
STAT_MIN_ROW = 28
STAT_LABEL_LEFT = CARD_PADDING + 20
STAT_LABEL_VALUE_GAP = 10
DROP_ROW_HEIGHT = 44
DROP_MODE_HEADER = 22
DROP_TABLE_HEADER = 22
DROP_COL_QTY = 300
DROP_COL_CHANCE = 480
ITEM_ICON_SLOT = (48, 48)
ING_ICON_SLOT = (28, 28)
COLORS = {
    "bg": (30, 30, 35, 230),
    "header_bg": (45, 45, 55, 255),
    "text": (220, 220, 220),
    "title": (255, 215, 0),
    "label": (180, 180, 200),
    "value": (255, 255, 255),
    "accent": (100, 180, 255),
    "separator": (60, 60, 70),
    "key_bg": (55, 55, 70, 255),
    "key_border": (120, 120, 140),
}
DESC_LINE_HEIGHT = 18
KEY_BADGE_PAD_X = 5
KEY_BADGE_PAD_Y = 2


def _ensure_dirs() -> None:
    for d in (DATA_DIR, IMAGES_DIR, CARDS_DIR):
        os.makedirs(d, exist_ok=True)


def _image_path(filename: str) -> str:
    if not filename:
        return ""
    return os.path.join(IMAGES_DIR, filename)


def _fit_image(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    scale = min(max_w / w, max_h / h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    if (new_w, new_h) == (w, h):
        return img
    resample = Image.NEAREST if scale > 1 else Image.LANCZOS
    return img.resize((new_w, new_h), resample)


def _paste_in_slot(
    card: Image.Image,
    img: Image.Image | None,
    x: int,
    y: int,
    slot_w: int,
    slot_h: int,
) -> None:
    if not img:
        return
    ox = x + max(0, (slot_w - img.width) // 2)
    oy = y + max(0, (slot_h - img.height) // 2)
    card.paste(img, (ox, oy), img)


def _load_image(path: str, size: tuple[int, int] | None = None) -> Image.Image | None:
    if not path or not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
        if size:
            img = _fit_image(img, size[0], size[1])
        return img
    except Exception:
        return None


def _load_coin_icon(coin_type: str) -> Image.Image | None:
    filename = COIN_SPECS.get(coin_type, "")
    if not filename:
        return None
    return _load_image(os.path.join(_COIN_DIR, filename), _COIN_ICON_SIZE)


def _load_bool_icon(kind: str) -> Image.Image | None:
    if kind not in ("yes", "no"):
        return None
    return _load_image(os.path.join(_ICON_DIR, f"{kind}.png"), _BOOL_ICON_SIZE)


def _load_inline_icon(filename: str) -> Image.Image | None:
    return _load_image(_image_path(filename), _INLINE_ICON_SIZE)


def _recipe_item_label(entry: dict) -> str:
    name = entry.get("name", "")
    amount = entry.get("amount")
    if amount and str(amount) not in ("1", ""):
        return f"{name}×{amount}"
    return name


def _ingredient_item_width(
    draw: ImageDraw.ImageDraw, ing: dict, font
) -> int:
    return ING_ICON_SLOT[0] + 4 + _text_width(draw, _recipe_item_label(ing), font) + 15


def _layout_ingredient_rows(
    draw: ImageDraw.ImageDraw,
    ingredients: list[dict],
    font,
    start_x: int,
    max_x: int,
) -> list[list[dict]]:
    rows: list[list[dict]] = []
    row: list[dict] = []
    x_pos = start_x
    for ing in ingredients:
        item_w = _ingredient_item_width(draw, ing, font)
        if row and x_pos + item_w > max_x:
            rows.append(row)
            row = []
            x_pos = start_x
        row.append(ing)
        x_pos += item_w
    if row:
        rows.append(row)
    return rows


def _recipe_ingredients_height(
    draw: ImageDraw.ImageDraw,
    ingredients: list[dict],
    font,
    start_x: int,
    max_x: int,
    row_h: int = 36,
) -> int:
    if not ingredients:
        return 0
    rows = _layout_ingredient_rows(draw, ingredients, font, start_x, max_x)
    return len(rows) * row_h


def _layout_rich_segments(
    draw: ImageDraw.ImageDraw,
    segments: list[dict],
    font,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    lines: list[list[tuple[str, str]]] = [[]]

    def line_width(line: list[tuple[str, str]]) -> int:
        width = 0
        for kind, payload in line:
            if kind == "text":
                width += _text_width(draw, payload, font)
            else:
                img = _load_inline_icon(payload)
                width += (img.width + 2) if img else 18
        return width

    for seg in segments:
        if seg.get("type") == "text":
            for ch in seg.get("text", ""):
                if ch == "\n":
                    lines.append([])
                    continue
                if lines[-1] and line_width(lines[-1]) + _text_width(draw, ch, font) > max_width:
                    lines.append([])
                if lines[-1] and lines[-1][-1][0] == "text":
                    kind, payload = lines[-1][-1]
                    lines[-1][-1] = (kind, payload + ch)
                else:
                    lines[-1].append(("text", ch))
        elif seg.get("type") == "icon":
            fn = seg.get("image", "")
            img = _load_inline_icon(fn)
            iw = (img.width + 2) if img else 18
            if lines[-1] and line_width(lines[-1]) + iw > max_width:
                lines.append([])
            lines[-1].append(("icon", fn))
    return [line for line in lines if line]


def _key_badge_label(seg: dict) -> str:
    symbol = seg.get("symbol", "")
    label = seg.get("label", "")
    if symbol and label:
        return f"{symbol}{label}"
    return symbol or label


def _key_badge_width(draw: ImageDraw.ImageDraw, seg: dict, font) -> int:
    inner = _key_badge_label(seg)
    bbox = draw.textbbox((0, 0), inner, font=font)
    return bbox[2] - bbox[0] + KEY_BADGE_PAD_X * 2


def _layout_description_segments(
    draw: ImageDraw.ImageDraw,
    segments: list[dict],
    font,
    max_width: int,
) -> list[list[tuple[str, object]]]:
    lines: list[list[tuple[str, object]]] = [[]]

    def line_width(line: list[tuple[str, object]]) -> int:
        width = 0
        for kind, payload in line:
            if kind == "text":
                width += _text_width(draw, payload, font)
            elif kind == "key":
                width += _key_badge_width(draw, payload, font) + 2
            else:
                img = _load_inline_icon(payload)
                width += (img.width + 2) if img else 18
        return width

    for seg in segments:
        if seg.get("type") == "text":
            for ch in seg.get("text", ""):
                if ch == "\n":
                    lines.append([])
                    continue
                if lines[-1] and line_width(lines[-1]) + _text_width(draw, ch, font) > max_width:
                    lines.append([])
                if lines[-1] and lines[-1][-1][0] == "text":
                    kind, payload = lines[-1][-1]
                    lines[-1][-1] = (kind, payload + ch)
                else:
                    lines[-1].append(("text", ch))
        elif seg.get("type") == "key":
            bw = _key_badge_width(draw, seg, font) + 2
            if lines[-1] and line_width(lines[-1]) + bw > max_width:
                lines.append([])
            lines[-1].append(("key", seg))
        elif seg.get("type") == "icon":
            fn = seg.get("image", "")
            img = _load_inline_icon(fn)
            iw = (img.width + 2) if img else 18
            if lines[-1] and line_width(lines[-1]) + iw > max_width:
                lines.append([])
            lines[-1].append(("icon", fn))
    return [line for line in lines if line]


def _draw_key_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    seg: dict,
    font,
) -> int:
    inner = _key_badge_label(seg)
    bbox = draw.textbbox((0, 0), inner, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w = tw + KEY_BADGE_PAD_X * 2
    h = th + KEY_BADGE_PAD_Y * 2
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=4,
        outline=COLORS["key_border"],
        fill=COLORS["key_bg"],
    )
    draw.text((x + KEY_BADGE_PAD_X, y + KEY_BADGE_PAD_Y), inner, fill=COLORS["text"], font=font)
    return w + 2


def _draw_description_line(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    x: int,
    y: int,
    line: list[tuple[str, object]],
    font,
) -> None:
    cx = x
    for kind, payload in line:
        if kind == "text":
            draw.text((cx, y), payload, fill=COLORS["text"], font=font)
            cx += _text_width(draw, payload, font)
        elif kind == "key":
            cx += _draw_key_badge(draw, cx, y - 1, payload, font)
        else:
            img = _load_inline_icon(payload)
            if img:
                card.paste(img, (cx, y + 1), img)
                cx += img.width + 2


def _resolve_description_rich(data: dict) -> list[list[dict]]:
    rich = data.get("description_rich")
    if rich:
        return rich
    text = (data.get("description") or "").strip()
    return description_text_to_rich(text) if text else []


def _calc_description_area(measure, description_rich: list[list[dict]], font) -> int:
    max_w = CARD_WIDTH - CARD_PADDING * 2 - 30
    area = 20 + 30
    for para in description_rich:
        lines = _layout_description_segments(measure, para, font, max_w)
        area += max(1, len(lines)) * DESC_LINE_HEIGHT
        area += 6
    return area + 10


def _draw_description_section(
    draw,
    card: Image.Image,
    y: int,
    description_rich: list[list[dict]],
    font_header,
    font_small,
    ui,
) -> int:
    draw.text((CARD_PADDING + 10, y), ui["description"], fill=COLORS["accent"], font=font_header)
    y += 30
    desc_x = CARD_PADDING + 20
    max_w = CARD_WIDTH - CARD_PADDING * 2 - 30
    for para in description_rich:
        for line in _layout_description_segments(draw, para, font_small, max_w):
            _draw_description_line(draw, card, desc_x, y, line, font_small)
            y += DESC_LINE_HEIGHT
        y += 6
    return y + 10


def _rich_stat_height(
    draw: ImageDraw.ImageDraw,
    segments: list[dict],
    font,
    max_width: int,
    extra: str,
) -> int:
    lines = _layout_rich_segments(draw, segments, font, max_width)
    if extra:
        extra_text = _format_stat_text("", extra)
        if extra_text:
            lines.extend(
                [[("text", line)] for line in _wrap_text_lines(draw, extra_text, font, max_width)]
            )
    if not lines:
        return 0
    return max(STAT_MIN_ROW, len(lines) * STAT_LINE_HEIGHT + 6)


def _draw_rich_stat_value(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    x: int,
    y: int,
    stat: dict,
    font,
    locale: str,
) -> int:
    label = stat.get("label", "")
    segments = stat.get("segments", [])
    extra = stat.get("extra", "")
    max_width = CARD_WIDTH - CARD_PADDING - x
    color = stat.get("color") if label in RARITY_LABELS else COLORS["value"]
    lines = _layout_rich_segments(draw, segments, font, max_width)
    if extra:
        extra_text = _format_stat_text("", extra)
        if extra_text:
            for line in _wrap_text_lines(draw, extra_text, font, max_width):
                lines.append([("text", line)])

    for i, line in enumerate(lines):
        cx = x
        cy = y + i * STAT_LINE_HEIGHT
        for kind, payload in line:
            if kind == "text":
                draw.text((cx, cy), payload, fill=color, font=font)
                cx += _text_width(draw, payload, font)
            else:
                img = _load_inline_icon(payload)
                if img:
                    card.paste(img, (cx, cy + 2), img)
                    cx += img.width + 2
    return max(STAT_MIN_ROW, len(lines) * STAT_LINE_HEIGHT + 6)


def _format_stat_text(value: str, extra: str) -> str:
    value = (value or "").strip()
    extra = (extra or "").strip()
    if not extra:
        return value
    extra_core = extra.strip("()（）[] ")
    if extra_core and extra_core in value:
        return value
    if extra[0] in "(（[":
        return f"{value} {extra}".strip() if value else extra
    if value:
        return f"{value} ({extra})"
    return f"({extra})"


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_text_lines(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int
) -> list[str]:
    if not text:
        return []
    if max_width <= 0 or _text_width(draw, text, font) <= max_width:
        return [text]
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if _text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def _stat_value_x(draw: ImageDraw.ImageDraw, stats: list[dict], font) -> int:
    if not stats:
        return STAT_LABEL_LEFT + 60
    max_label_w = max(
        (_text_width(draw, stat.get("label", ""), font) for stat in stats),
        default=0,
    )
    return STAT_LABEL_LEFT + max_label_w + STAT_LABEL_VALUE_GAP


def _draw_stat_label(
    draw: ImageDraw.ImageDraw,
    label: str,
    y: int,
    font,
    value_x: int,
) -> None:
    label_w = _text_width(draw, label, font)
    label_x = value_x - STAT_LABEL_VALUE_GAP - label_w
    draw.text((label_x, y), label, fill=COLORS["label"], font=font)


def _stat_value_height(
    draw: ImageDraw.ImageDraw,
    card: Image.Image | None,
    x: int,
    stat: dict,
    font,
    locale: str,
) -> int:
    stat = normalize_stat_for_display(stat, locale)
    if stat.get("coins"):
        return STAT_MIN_ROW

    bool_icon = resolve_bool_icon(stat)
    if bool_icon:
        return STAT_MIN_ROW

    if stat.get("segments"):
        max_width = CARD_WIDTH - CARD_PADDING - x
        return _rich_stat_height(
            draw, stat.get("segments", []), font, max_width, stat.get("extra", "")
        )

    v_text = _format_stat_text(stat.get("value", ""), stat.get("extra", ""))
    if not v_text:
        return 0

    max_width = CARD_WIDTH - CARD_PADDING - x
    lines = _wrap_text_lines(draw, v_text, font, max_width)
    return max(STAT_MIN_ROW, len(lines) * STAT_LINE_HEIGHT + 6)


def _draw_stat_value(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    x: int,
    y: int,
    stat: dict,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    locale: str,
) -> int:
    stat = normalize_stat_for_display(stat, locale)
    label = stat.get("label", "")

    if stat.get("coins"):
        cx = x
        for coin in stat["coins"]:
            amount = str(coin.get("amount", ""))
            draw.text((cx, y), amount, fill=COLORS["value"], font=font)
            bbox = draw.textbbox((cx, y), amount, font=font)
            cx = bbox[2] + 4
            coin_img = _load_coin_icon(coin.get("type", ""))
            if coin_img:
                card.paste(coin_img, (cx, y + 1), coin_img)
                cx += _COIN_ICON_SIZE[0] + 8
        return STAT_MIN_ROW

    bool_icon = resolve_bool_icon(stat)
    if bool_icon:
        icon_img = _load_bool_icon(bool_icon)
        if icon_img:
            card.paste(icon_img, (x, y + 2), icon_img)
        return STAT_MIN_ROW

    if stat.get("segments"):
        return _draw_rich_stat_value(draw, card, x, y, stat, font, locale)

    v_text = _format_stat_text(stat.get("value", ""), stat.get("extra", ""))
    if not v_text:
        return 0

    color = stat.get("color") if label in RARITY_LABELS else COLORS["value"]
    max_width = CARD_WIDTH - CARD_PADDING - x
    lines = _wrap_text_lines(draw, v_text, font, max_width)
    for i, line in enumerate(lines):
        draw.text((x, y + i * STAT_LINE_HEIGHT), line, fill=color, font=font)
    return max(STAT_MIN_ROW, len(lines) * STAT_LINE_HEIGHT + 6)


def _load_entity_image(filename: str) -> Image.Image | None:
    img = _load_image(_image_path(filename))
    if not img:
        return None
    return _fit_image(img, 48, 36)


def _drop_column_widths() -> tuple[int, int, int]:
    col_entity = CARD_PADDING + 20
    col_qty = DROP_COL_QTY
    col_chance = DROP_COL_CHANCE
    return col_entity, col_qty, col_chance


def _drop_entry_row_height(
    draw: ImageDraw.ImageDraw,
    entry: dict,
    font,
    col_qty: int,
    col_chance: int,
) -> int:
    qty_w = max(40, col_chance - col_qty - 10)
    chance_w = max(40, CARD_WIDTH - CARD_PADDING - col_chance)
    qty_lines = _wrap_text_lines(draw, entry.get("quantity", ""), font, qty_w)
    chance_lines = _wrap_text_lines(draw, entry.get("chance", ""), font, chance_w)
    line_count = max(len(qty_lines), len(chance_lines), 1)
    return max(DROP_ROW_HEIGHT, line_count * STAT_LINE_HEIGHT + 8)


def _draw_drop_field_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    max_w: int,
    font,
    fill,
) -> int:
    lines = _wrap_text_lines(draw, text, font, max_w)
    for i, line in enumerate(lines):
        draw.text((x, y + i * STAT_LINE_HEIGHT), line, fill=fill, font=font)
    return len(lines)


def _calc_drops_area(drops: dict | None, locale: str = "zh") -> int:
    if not drops:
        return 0
    block = drops_display_block(drops, locale)
    if not block:
        return 0
    measure = ImageDraw.Draw(Image.new("RGBA", (CARD_WIDTH, 100)))
    font_small = _try_get_font(13)
    _, col_qty, col_chance = _drop_column_widths()
    area = 34
    if block.get("label"):
        area += DROP_MODE_HEADER
    area += DROP_TABLE_HEADER
    for entry in block.get("entries", []):
        area += _drop_entry_row_height(measure, entry, font_small, col_qty, col_chance)
    return area + 12


def _draw_drops_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    drops: dict,
    font_header,
    font_small,
    ui: dict,
    locale: str = "zh",
) -> int:
    block = drops_display_block(drops, locale)
    if not block:
        return y

    draw.text((CARD_PADDING + 10, y), ui["drops"], fill=COLORS["accent"], font=font_header)
    y += 30

    col_entity, col_qty, col_chance = _drop_column_widths()
    qty_w = max(40, col_chance - col_qty - 10)
    chance_w = max(40, CARD_WIDTH - CARD_PADDING - col_chance)

    label = block.get("label", "")
    if label:
        draw.text((col_entity, y), label, fill=COLORS["label"], font=font_small)
        y += DROP_MODE_HEADER

    draw.text((col_entity, y), ui["col_entity"], fill=COLORS["label"], font=font_small)
    draw.text((col_qty, y), ui["col_qty"], fill=COLORS["label"], font=font_small)
    draw.text((col_chance, y), ui["col_chance"], fill=COLORS["label"], font=font_small)
    y += DROP_TABLE_HEADER

    for entry in block.get("entries", []):
        row_h = _drop_entry_row_height(draw, entry, font_small, col_qty, col_chance)
        img = _load_entity_image(entry.get("image", ""))
        entity_slot_w = 48
        text_x = col_entity + entity_slot_w + 8
        if img:
            _paste_in_slot(card, img, col_entity, y, entity_slot_w, row_h)
        draw.text((text_x, y + 4), entry.get("name", ""), fill=COLORS["text"], font=font_small)
        _draw_drop_field_lines(
            draw, entry.get("quantity", ""), col_qty, y + 4, qty_w, font_small, COLORS["value"]
        )
        _draw_drop_field_lines(
            draw, entry.get("chance", ""), col_chance, y + 4, chance_w, font_small, COLORS["value"]
        )
        y += row_h
    return y


def _normalize_message(text: str) -> str:
    text = text.strip()
    if text.startswith("/"):
        return text[1:].strip()
    return text


def _extract_query_text(text: str) -> str | None:
    """解析查询指令参数，非查询指令时返回 None。"""
    normalized = _normalize_message(text)
    if normalized == "泰拉更新" or normalized.startswith("泰拉更新 "):
        return None
    for prefix in ("泰拉查询", "泰拉", "terraria"):
        if normalized == prefix:
            return ""
        if normalized.startswith(prefix + " "):
            return normalized[len(prefix) + 1 :].strip()
    return None


def _is_update_command(text: str) -> bool:
    normalized = _normalize_message(text)
    return normalized == "泰拉更新"


# 匹配 泰拉查询/泰拉/泰拉更新/terraria，无需 / 前缀（/ 也兼容）
_TERRARIA_CMD_RE = r"^/?(泰拉更新|泰拉查询|泰拉|terraria)(\s|$)"


_CARD_UI = {
    "zh": {
        "description": "▎描述",
        "stats": "▎属性",
        "recipe": "▎合成配方",
        "drops": "▎来自",
        "col_entity": "实体",
        "col_qty": "数量",
        "col_chance": "几率",
        "station": "制作站:",
        "materials": "材料:",
        "result": "→ 产物:",
        "unknown": "未知物品",
        "recipe_title": "📜 合成配方",
    },
    "en": {
        "description": "▎Description",
        "stats": "▎Stats",
        "recipe": "▎Recipe",
        "drops": "▎From",
        "col_entity": "Entity",
        "col_qty": "Qty",
        "col_chance": "Rate",
        "station": "Station:",
        "materials": "Materials:",
        "result": "→ Result:",
        "unknown": "Unknown Item",
        "recipe_title": "📜 Recipe",
    },
}


def _query_locale_hint(query: str) -> str | None:
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", query))
    has_latin = bool(re.search(r"[A-Za-z]", query))
    if has_cjk and not has_latin:
        return "zh"
    if has_latin and not has_cjk:
        return "en"
    return None


def _item_en_name(item: dict) -> str:
    en = item.get("en") or {}
    return en.get("name") or item.get("en_name") or ""


def _fuzzy_match(query: str, items: dict[str, dict]) -> list[tuple[str, str]]:
    query = query.strip()
    if not query:
        return []

    locale_hint = _query_locale_hint(query)
    query_lower = query.lower()
    found: dict[tuple[str, str], int] = {}

    def add(key: str, locale: str, rank: int) -> None:
        slot = (key, locale)
        if slot not in found or rank < found[slot]:
            found[slot] = rank

    for key, item in items.items():
        zh_name = item.get("name", key)
        en_name = _item_en_name(item)

        if locale_hint in (None, "zh"):
            if query == key or query == zh_name:
                add(key, "zh", 0)
            elif query in key or query in zh_name:
                add(key, "zh", min(len(key), len(zh_name)))

        if locale_hint in (None, "en") and en_name:
            en_lower = en_name.lower()
            if query_lower == en_lower:
                add(key, "en", 0)
            elif query_lower in en_lower:
                add(key, "en", len(en_name))

    ranked = sorted(found.items(), key=lambda x: (x[1], x[0][1], x[0][0]))
    if any(rank == 0 for _, rank in ranked):
        ranked = [(pair, rank) for pair, rank in ranked if rank == 0]
    return [pair for pair, _ in ranked]


def _resolve_display_item(item: dict, locale: str) -> dict | None:
    if locale == "en":
        en = item.get("en")
        if not en or not en.get("name"):
            return None
        return {
            "name": en.get("name", item.get("name", "")),
            "image": en.get("image") or item.get("image", ""),
            "stats": en.get("stats", []),
            "recipe": merge_en_recipe(item.get("recipe"), en.get("recipe")),
            "drops": en.get("drops"),
            "description": en.get("description") or item.get("description"),
            "description_rich": en.get("description_rich") or item.get("description_rich"),
        }
    rich = item.get("description_rich")
    return {
        "name": item.get("name", ""),
        "image": item.get("image", ""),
        "stats": item.get("stats", []),
        "recipe": item.get("recipe"),
        "drops": item.get("drops"),
        "description": item.get("description"),
        "description_rich": rich,
    }


def _match_label(key: str, item: dict, locale: str) -> str:
    if locale == "en":
        return _item_en_name(item) or item.get("name", key)
    return item.get("name", key)


def _format_text_result(data: dict, locale: str = "zh") -> str:
    ui = _CARD_UI.get(locale, _CARD_UI["zh"])
    lines = [f"📦 {data.get('name', ui['unknown'])}", "=" * 30]

    description = data.get("description")
    if description:
        lines.append("")
        lines.append(ui["description"].lstrip("▎"))
        lines.append("-" * 30)
        for para in description.split("\n\n"):
            lines.append(f"  {para}")

    for stat in data.get("stats", []):
        label = stat.get("label", "")
        stat = normalize_stat_for_display(stat, locale)
        if stat.get("coins"):
            parts = []
            for coin in stat["coins"]:
                abbr = {"pc": "PC", "gc": "GC", "sc": "SC", "cc": "CC"}.get(
                    coin.get("type", ""), ""
                )
                parts.append(f"{coin.get('amount', '')} {abbr}".strip())
            v = " ".join(parts)
        elif resolve_bool_icon(stat) == "yes":
            v = "✔"
        elif resolve_bool_icon(stat) == "no":
            v = "✘"
        else:
            value = stat.get("value", "")
            extra = stat.get("extra", "")
            if not value and not extra:
                continue
            v = _format_stat_text(stat.get("value", ""), stat.get("extra", ""))
        lines.append(f"  {label}: {v}")

    recipe = data.get("recipe")
    if recipe:
        lines.append("")
        lines.append(ui["recipe_title"])
        lines.append("-" * 30)
        station = recipe.get("station", "")
        if station:
            lines.append(f"  {ui['station']} {station}")
        ings = " + ".join(_recipe_item_label(ing) for ing in recipe.get("ingredients", []))
        result = _recipe_item_label(recipe.get("result", {}) or {})
        if not result:
            result = data.get("name", "")
        if ings:
            lines.append(f"  {ings} → {result}")

    drops = data.get("drops")
    if drops:
        lines.append("")
        lines.append(ui["drops"].lstrip("▎"))
        block = drops_display_block(drops, locale)
        if block:
            label = block.get("label", "")
            if label:
                lines.append(f"  [{label}]")
            lines.append(
                f"  {ui['col_entity']}\t{ui['col_qty']}\t{ui['col_chance']}"
            )
            for entry in block.get("entries", []):
                lines.append(
                    f"  {entry.get('name', '')}: "
                    f"{entry.get('quantity', '')} ({entry.get('chance', '')})"
                )

    return "\n".join(lines)


def _generate_item_card(data: dict, locale: str = "zh") -> str:
    _ensure_dirs()
    ui = _CARD_UI.get(locale, _CARD_UI["zh"])

    font_title = _try_get_font(26)
    font_header = _try_get_font(20)
    font_body = _try_get_font(16)
    font_small = _try_get_font(13)

    stats = [
        s
        for s in data.get("stats", [])
        if s.get("value")
        or s.get("extra")
        or s.get("coins")
        or s.get("bool_icon")
        or s.get("segments")
        or resolve_bool_icon(s)
    ]
    recipe = data.get("recipe")
    drops = data.get("drops")
    description_rich = _resolve_description_rich(data)

    measure = ImageDraw.Draw(Image.new("RGBA", (CARD_WIDTH, 100)))
    stat_value_x = _stat_value_x(measure, stats, font_body)
    stats_area = 20
    for stat in stats:
        stats_area += _stat_value_height(
            measure, None, stat_value_x, stat, font_body, locale
        )

    desc_area = 0
    if description_rich:
        desc_area = _calc_description_area(measure, description_rich, font_small) + 20

    title_area = 60
    sep_area = 30
    recipe_area = 0
    if recipe:
        recipe_area = 80
        station = recipe.get("station", "")
        if station:
            station_text = f"{ui['station']} {station}"
            station_lines = _wrap_text_lines(
                measure,
                station_text,
                font_small,
                CARD_WIDTH - CARD_PADDING * 2 - 40,
            )
            recipe_area += len(station_lines) * 18 + 10
        ing_count = len(recipe.get("ingredients", []))
        if ing_count:
            ing_start_x = CARD_PADDING + 36
            ing_max_x = CARD_WIDTH - CARD_PADDING - 10
            recipe_area += (
                _recipe_ingredients_height(
                    measure, recipe.get("ingredients", []), font_small, ing_start_x, ing_max_x
                )
                + 44
            )
        else:
            recipe_area += 44

    drops_area = 0
    if drops:
        drops_area = 20 + _calc_drops_area(drops, locale)
        if recipe:
            drops_area += 20

    total_height = (
        title_area + desc_area + stats_area + sep_area + recipe_area + drops_area + CARD_PADDING * 2
    )
    card = Image.new("RGBA", (CARD_WIDTH, total_height), COLORS["bg"])
    draw = ImageDraw.Draw(card)

    item_img = _load_image(_image_path(data.get("image", "")), ITEM_ICON_SLOT)
    draw.rounded_rectangle(
        [CARD_PADDING, CARD_PADDING, CARD_WIDTH - CARD_PADDING, CARD_PADDING + title_area],
        radius=8,
        fill=COLORS["header_bg"],
    )

    icon_x = CARD_PADDING + 15
    if item_img:
        _paste_in_slot(
            card,
            item_img,
            icon_x,
            CARD_PADDING,
            ITEM_ICON_SLOT[0],
            title_area,
        )
        text_x = icon_x + ITEM_ICON_SLOT[0] + 12
    else:
        text_x = icon_x

    draw.text(
        (text_x, CARD_PADDING + 6),
        data.get("name", ui["unknown"]),
        fill=COLORS["title"],
        font=font_title,
    )

    y = CARD_PADDING + title_area + 10
    if description_rich:
        y = _draw_description_section(
            draw, card, y, description_rich, font_header, font_small, ui
        )
        draw.line(
            [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
            fill=COLORS["separator"],
            width=1,
        )
        y += 20

    draw.text((CARD_PADDING + 10, y), ui["stats"], fill=COLORS["accent"], font=font_header)
    y += 30

    stat_value_x = _stat_value_x(draw, stats, font_body)
    for stat in stats:
        label = stat.get("label", "")
        _draw_stat_label(draw, label, y, font_body, stat_value_x)
        row_h = _draw_stat_value(draw, card, stat_value_x, y, stat, font_body, locale)
        y += row_h

    y += 10
    draw.line(
        [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
        fill=COLORS["separator"],
        width=1,
    )
    y += 20

    if recipe:
        draw.text((CARD_PADDING + 10, y), ui["recipe"], fill=COLORS["accent"], font=font_header)
        y += 30

        station = recipe.get("station", "")
        if station:
            station_text = f"{ui['station']} {station}"
            station_x = CARD_PADDING + 20
            station_max_w = CARD_WIDTH - CARD_PADDING * 2 - 40
            station_lines = _wrap_text_lines(
                draw, station_text, font_small, station_max_w
            )
            for line in station_lines:
                draw.text(
                    (station_x, y),
                    line,
                    fill=COLORS["label"],
                    font=font_small,
                )
                y += 18
            y += 4

        ingredients = recipe.get("ingredients", [])
        if ingredients:
            mat_x = CARD_PADDING + 20
            draw.text((mat_x, y), ui["materials"], fill=COLORS["label"], font=font_small)
            mat_bbox = draw.textbbox((mat_x, y), ui["materials"], font=font_small)
            y += mat_bbox[3] - mat_bbox[1] + 10
            ing_start_x = CARD_PADDING + 36
            ing_max_x = CARD_WIDTH - CARD_PADDING - 10
            ing_row_h = 36
            ing_rows = _layout_ingredient_rows(
                draw, ingredients, font_small, ing_start_x, ing_max_x
            )
            for row_items in ing_rows:
                x_pos = ing_start_x
                for ing in row_items:
                    ing_name = _recipe_item_label(ing)
                    ing_img = _load_image(
                        _image_path(ing.get("image", "")), ING_ICON_SLOT
                    )
                    if ing_img:
                        _paste_in_slot(
                            card, ing_img, x_pos, y, ING_ICON_SLOT[0], ing_row_h
                        )
                    draw.text(
                        (x_pos + ING_ICON_SLOT[0] + 4, y + 2),
                        ing_name,
                        fill=COLORS["text"],
                        font=font_small,
                    )
                    x_pos += _ingredient_item_width(draw, ing, font_small)
                y += ing_row_h

        result = recipe.get("result", {})
        result_name = _recipe_item_label(result) or data.get("name", "")
        result_img = _load_image(_image_path(result.get("image", "")), ING_ICON_SLOT)
        draw.text((CARD_PADDING + 20, y), ui["result"], fill=COLORS["accent"], font=font_body)
        rx = CARD_PADDING + 100
        if result_img:
            _paste_in_slot(card, result_img, rx, y, ING_ICON_SLOT[0], ING_ICON_SLOT[1])
            rx += ING_ICON_SLOT[0] + 4
        draw.text((rx, y + 2), result_name, fill=COLORS["title"], font=font_body)
        y += 36

    if drops:
        if recipe:
            draw.line(
                [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
                fill=COLORS["separator"],
                width=1,
            )
            y += 20
        _draw_drops_section(draw, card, y, drops, font_header, font_small, ui, locale)

    safe_name = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", data.get("name", "unknown"))
    output_path = os.path.join(CARDS_DIR, f"card_v15_{locale}_{safe_name}.png")
    card.convert("RGB").save(output_path, "PNG")
    return output_path


def _format_update_result(result: dict) -> str:
    en_count = result.get("en_backfill_count", 0)
    drops_count = result.get("drops_backfill_count", 0)
    desc_count = result.get("desc_backfill_count", 0)
    extra_lines = ""
    if en_count:
        extra_lines += f"\n英文数据回填：{en_count} 个"
    if drops_count:
        extra_lines += f"\n掉落来源回填：{drops_count} 个"
    if desc_count:
        extra_lines += f"\n描述回填：{desc_count} 个"
    if result.get("new_count", 0) == 0 and not extra_lines:
        return (
            f"✅ Wiki 数据已是最新\n"
            f"当前共 {result.get('total', 0)} 个物品"
        )
    if result.get("new_count", 0) == 0:
        return (
            f"✅ Wiki 数据更新完成\n"
            f"当前总计：{result.get('total', 0)} 个物品{extra_lines}"
        )
    return (
        f"✅ Wiki 数据更新完成\n"
        f"本次新增：{result.get('new_count', 0)} 个\n"
        f"当前总计：{result.get('total', 0)} 个\n"
        f"新图片：{result.get('images_ok', 0)}/{result.get('images_total', 0)}{extra_lines}"
    )


class TerrariaQueryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.cron_time: str = (config.get("cron_time") or "").strip()
        self.update_admin_id: str = str(config.get("update_admin_id") or "").strip()
        self.show_update_progress: bool = bool(config.get("show_update_progress", True))

        _ensure_dirs()
        self.items: dict[str, dict] = {}
        self._load_items()

        self._cron_task: asyncio.Task | None = None
        self._update_lock = asyncio.Lock()

        try:
            if self.cron_time:
                self._start_cron_task()
        except RuntimeError:
            pass

    def _can_update(self, event: AstrMessageEvent | None) -> bool:
        if not self.update_admin_id:
            return True
        if event is None:
            return True
        return str(event.get_sender_id() or "").strip() == self.update_admin_id

    async def _run_wiki_update(self, force: bool = False) -> dict:
        async with self._update_lock:
            result = await update_wiki_data(force=force)
            self._load_items()
            return result

    def _start_cron_task(self) -> None:
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
        self._cron_task = asyncio.create_task(self._cron_loop())

    async def _cron_loop(self) -> None:
        try:
            cron = croniter(self.cron_time)
        except (ValueError, KeyError) as e:
            logger.error(f"泰拉瑞亚 Wiki 定时更新：无效的 Cron 表达式 '{self.cron_time}': {e}")
            return

        while True:
            try:
                next_time = cron.get_next(datetime)
                wait_seconds = (next_time - datetime.now()).total_seconds()
                if wait_seconds > 0:
                    logger.info(
                        f"泰拉瑞亚 Wiki 定时更新：下次执行 "
                        f"{next_time.strftime('%Y-%m-%d %H:%M:%S')}，等待 {wait_seconds:.0f} 秒"
                    )
                    await asyncio.sleep(wait_seconds)

                result = await self._run_wiki_update(force=False)
                logger.info(
                    f"泰拉瑞亚 Wiki 定时更新完成：新增 {result.get('new_count', 0)} 个，"
                    f"总计 {result.get('total', 0)} 个"
                )
            except asyncio.CancelledError:
                logger.info("泰拉瑞亚 Wiki 定时更新任务已取消")
                break
            except Exception as e:
                logger.error(f"泰拉瑞亚 Wiki 定时更新失败: {e}")
                await asyncio.sleep(60)

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        if self.cron_time and (self._cron_task is None or self._cron_task.done()):
            self._start_cron_task()
            logger.info(f"泰拉瑞亚 Wiki 定时更新已启用，Cron: {self.cron_time}")
        elif not self.cron_time:
            logger.info("未配置 Cron，泰拉瑞亚 Wiki 定时更新未启用")

    def _load_items(self) -> None:
        if not os.path.exists(ITEMS_JSON):
            logger.warning(
                f"未找到离线数据 {ITEMS_JSON}，请先运行 prepare_data.py 准备数据"
            )
            return
        try:
            with open(ITEMS_JSON, "r", encoding="utf-8") as f:
                self.items = json.load(f)
            logger.info(f"泰拉瑞亚查询插件已加载，共 {len(self.items)} 个物品")
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"加载 items.json 失败: {e}")
            self.items = {}

    @filter.regex(_TERRARIA_CMD_RE, priority=10)
    async def on_terraria_command(self, event: AstrMessageEvent):
        """处理泰拉瑞亚查询/更新指令（支持无 / 前缀）。"""
        raw = event.message_str.strip()

        if _is_update_command(raw):
            async for result in self._handle_update(event):
                yield result
            event.stop_event()
            return

        query_text = _extract_query_text(raw)
        if query_text is None:
            return

        async for result in self._handle_query(event, query_text):
            yield result
        event.stop_event()

    async def _handle_query(self, event: AstrMessageEvent, text: str):
        if not text:
            yield event.plain_result(
                "用法: 泰拉查询 <物品名>\n"
                "例如: 泰拉查询 天顶剑\n"
                "      泰拉查询 Zenith"
            )
            return

        if not self.items:
            yield event.plain_result(
                "❌ 离线数据尚未准备。\n"
                "请在 WebUI 配置插件后发送「泰拉更新」，或从仓库拉取已包含的 data/ 目录。"
            )
            return

        matches = _fuzzy_match(text, self.items)
        if not matches:
            yield event.plain_result(f"❌ 未找到「{text}」的相关信息。")
            return

        if len(matches) > 3:
            lines = [f"找到 {len(matches)} 个匹配结果，请输入更精确的名称后重新查询：", ""]
            for key, locale in matches:
                item = self.items[key]
                label = _match_label(key, item, locale)
                if locale == "en" and item.get("name"):
                    lines.append(f"· {label} ({item['name']})")
                else:
                    lines.append(f"· {label}")
            yield event.plain_result("\n".join(lines))
            return

        if len(matches) > 1:
            yield event.plain_result(f"找到 {len(matches)} 个匹配结果：")

        for key, locale in matches:
            item = self.items[key]
            display = _resolve_display_item(item, locale)
            if not display:
                yield event.plain_result(
                    f"❌ 「{item.get('name', key)}」暂无英文数据，请尝试中文查询或执行「泰拉更新」。"
                )
                continue
            try:
                card_path = _generate_item_card(display, locale=locale)
                yield event.image_result(card_path)
            except Exception as e:
                logger.error(f"生成图片失败 ({key}/{locale}): {e}")
                yield event.plain_result(_format_text_result(display, locale=locale))

    async def _handle_update(self, event: AstrMessageEvent):
        if not self._can_update(event):
            yield event.plain_result("❌ 仅管理员可执行 Wiki 数据更新。")
            return

        if self._update_lock.locked():
            yield event.plain_result("⏳ 已有更新任务进行中，请稍候。")
            return

        if self.show_update_progress:
            yield event.plain_result("🔄 正在从 Wiki 增量更新物品数据，请稍候…")

        try:
            result = await self._run_wiki_update(force=False)
            yield event.plain_result(_format_update_result(result))
        except Exception as e:
            logger.error(f"Wiki 数据更新失败: {e}")
            yield event.plain_result(f"❌ 更新失败：{str(e)[:120]}")

    async def terminate(self):
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
            try:
                await self._cron_task
            except asyncio.CancelledError:
                pass
        logger.info("泰拉瑞亚查询插件已卸载")
