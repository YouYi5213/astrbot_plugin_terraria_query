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

from .category_data import (
    load_biomes_for_plugin,
    load_items_for_plugin,
    load_mounts_for_plugin,
    load_npcs_for_plugin,
    load_pets_for_plugin,
)
from .prepare_data import (
    COIN_SPECS,
    RARITY_LABELS,
    description_text_to_rich,
    drops_display_block,
    normalize_stat_for_display,
    resolve_bool_icon,
    update_wiki_data,
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
_SYMBOL_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_RESOLVED_FONT_PATH: str | None = None
_RESOLVED_SYMBOL_FONT_PATH: str | None = None

_KEY_SYMBOL_SPLIT_RE = re.compile(r"^([▼▲◀▶↷⚷⚒])(?:\s*)(.*)$")


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


def _resolve_symbol_font_path() -> str | None:
    global _RESOLVED_SYMBOL_FONT_PATH
    if _RESOLVED_SYMBOL_FONT_PATH is not None:
        return _RESOLVED_SYMBOL_FONT_PATH or None

    candidates = [
        "C:/Windows/Fonts/seguisym.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansSymbols-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, 16)
                _RESOLVED_SYMBOL_FONT_PATH = path
                return path
            except Exception:
                continue

    _RESOLVED_SYMBOL_FONT_PATH = ""
    return None


def _try_get_symbol_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size in _SYMBOL_FONT_CACHE:
        return _SYMBOL_FONT_CACHE[size]

    font_path = _resolve_symbol_font_path()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, size)
            _SYMBOL_FONT_CACHE[size] = font
            return font
        except Exception:
            pass

    font = _try_get_font(size)
    _SYMBOL_FONT_CACHE[size] = font
    return font


def _font_size(font) -> int:
    return getattr(font, "size", 13)


def _normalize_key_seg(seg: dict) -> dict:
    seg = dict(seg)
    symbol = (seg.get("symbol") or "").strip()
    label = (seg.get("label") or "").strip()
    if not symbol and label:
        match = _KEY_SYMBOL_SPLIT_RE.match(label)
        if match:
            symbol, label = match.group(1), match.group(2)
    seg["symbol"] = symbol
    seg["label"] = label
    return seg


def _resolve_data_dir() -> str:
    candidates = [
        os.path.join(_PLUGIN_DIR, "data", "terraria_query"),
        os.path.join(os.getcwd(), "data", "terraria_query"),
    ]
    for d in candidates:
        if os.path.isdir(os.path.join(d, "categories")):
            return d
    return candidates[0]


DATA_DIR = _resolve_data_dir()
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
MOUNTS_JSON = os.path.join(CATEGORIES_DIR, "mounts.json")
PETS_JSON = os.path.join(CATEGORIES_DIR, "pets.json")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
CARDS_DIR = os.path.join(DATA_DIR, "cards")

CARD_WIDTH = 600
CARD_PADDING = 20
CARD_VERSION = "v31"
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
BUFF_ICON_SLOT = (32, 32)
MOUNT_PREVIEW_SLOT = (80, 72)
PET_PREVIEW_SLOT = (80, 72)
BIOME_BANNER_MAX_SIZE = (CARD_WIDTH - CARD_PADDING * 2, 140)
NPC_SPRITE_MAX_SIZE = (56, 72)
NPC_SHOP_ICON_SLOT = (36, 36)
NPC_PREF_ICON_SLOT = (24, 24)
NPC_TABLE_ROW_MIN = 36
NPC_COL_PRICE = 220
NPC_COL_AVAIL = 330
NPC_SHIMMER_MAX_SIZE = (56, 72)
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


def _prune_old_card_cache(keep_version: str = CARD_VERSION) -> None:
    """删除旧版本卡片缓存，避免磁盘无限增长。"""
    if not os.path.isdir(CARDS_DIR):
        return
    prefix = f"card_{keep_version}_"
    for name in os.listdir(CARDS_DIR):
        if not name.startswith("card_v") or name.startswith(prefix):
            continue
        try:
            os.remove(os.path.join(CARDS_DIR, name))
        except OSError:
            pass


_CARD_CACHE_PRUNED = False


def _clear_card_cache(keep_version: str = CARD_VERSION) -> None:
    """清除当前版本卡片缓存（Wiki 数据更新后调用）。"""
    if not os.path.isdir(CARDS_DIR):
        return
    prefix = f"card_{keep_version}_"
    for name in os.listdir(CARDS_DIR):
        if name.startswith(prefix):
            try:
                os.remove(os.path.join(CARDS_DIR, name))
            except OSError:
                pass


def _card_output_path(name: str, locale: str = "zh", *, kind: str | None = None) -> str:
    safe_name = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", name or "unknown")
    kind_part = f"_{kind}" if kind else ""
    return os.path.join(CARDS_DIR, f"card_{CARD_VERSION}_{locale}{kind_part}_{safe_name}")


def _image_path(filename: str) -> str:
    if not filename:
        return ""
    return os.path.join(IMAGES_DIR, filename)


def _resolve_inline_icon_path(filename: str) -> str:
    """Wiki 属性内联图标常用缩略图名（如 17px-Titanium_Mask.png），本地存完整物品图。"""
    if not filename:
        return ""
    path = _image_path(filename)
    if os.path.exists(path):
        return path
    base = re.sub(r"^\d+px-", "", filename, flags=re.I)
    if base != filename:
        alt = _image_path(base)
        if os.path.exists(alt):
            return alt
    return path


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


def _load_item_image(filename: str, size: tuple[int, int] | None = None) -> Image.Image | None:
    return _load_image(_resolve_inline_icon_path(filename), size)


def _load_inline_icon(filename: str) -> Image.Image | None:
    return _load_item_image(filename, _INLINE_ICON_SIZE)


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
    seg = _normalize_key_seg(seg)
    symbol = seg.get("symbol", "")
    label = seg.get("label", "")
    if symbol and label:
        return f"{symbol}{label}"
    return symbol or label


def _key_badge_width(draw: ImageDraw.ImageDraw, seg: dict, font) -> int:
    seg = _normalize_key_seg(seg)
    symbol = seg.get("symbol", "")
    label = seg.get("label", "")
    size = _font_size(font)
    width = KEY_BADGE_PAD_X * 2
    if symbol:
        sym_font = _try_get_symbol_font(size)
        width += _text_width(draw, symbol, sym_font)
    if label:
        width += _text_width(draw, label, font)
    return width + 2


def _coin_segment_width(draw: ImageDraw.ImageDraw, seg: dict, font) -> int:
    amount = str(seg.get("amount", ""))
    return _text_width(draw, amount, font) + 4 + _COIN_ICON_SIZE[0] + 2


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
            elif kind == "coin":
                width += _coin_segment_width(draw, payload, font)
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
        elif seg.get("type") == "coin":
            cw = _coin_segment_width(draw, seg, font)
            if lines[-1] and line_width(lines[-1]) + cw > max_width:
                lines.append([])
            lines[-1].append(("coin", seg))
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
    seg = _normalize_key_seg(seg)
    symbol = seg.get("symbol", "")
    label = seg.get("label", "")
    size = _font_size(font)
    sym_font = _try_get_symbol_font(size)
    sym_w = _text_width(draw, symbol, sym_font) if symbol else 0
    label_w = _text_width(draw, label, font) if label else 0
    tw = sym_w + label_w
    th = max(
        draw.textbbox((0, 0), symbol or "A", font=sym_font)[3]
        - draw.textbbox((0, 0), symbol or "A", font=sym_font)[1],
        draw.textbbox((0, 0), label or "A", font=font)[3]
        - draw.textbbox((0, 0), label or "A", font=font)[1],
    )
    w = tw + KEY_BADGE_PAD_X * 2
    h = th + KEY_BADGE_PAD_Y * 2
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=4,
        outline=COLORS["key_border"],
        fill=COLORS["key_bg"],
    )
    cx = x + KEY_BADGE_PAD_X
    cy = y + KEY_BADGE_PAD_Y
    if symbol:
        draw.text((cx, cy), symbol, fill=COLORS["text"], font=sym_font)
        cx += sym_w
    if label:
        draw.text((cx, cy), label, fill=COLORS["text"], font=font)
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
        elif kind == "coin":
            amount = str(payload.get("amount", ""))
            draw.text((cx, y), amount, fill=COLORS["text"], font=font)
            cx += _text_width(draw, amount, font) + 4
            coin_img = _load_coin_icon(payload.get("coin_type", ""))
            if coin_img:
                card.paste(coin_img, (cx, y + 1), coin_img)
                cx += _COIN_ICON_SIZE[0] + 2
        else:
            img = _load_inline_icon(payload)
            if img:
                card.paste(img, (cx, y + 1), img)
                cx += img.width + 2


def _resolve_source_rich(data: dict) -> list[list[dict]]:
    rich = data.get("source_rich")
    if rich:
        return rich
    text = (data.get("source") or "").strip()
    return description_text_to_rich(text) if text else []


def _is_wing_item(data: dict) -> bool:
    return data.get("page_type") == "wing" or bool(data.get("from_wings_table"))


def _card_ui_for_data(data: dict) -> dict:
    ui = dict(_CARD_UI)
    if _is_wing_item(data):
        ui["recipe"] = ui["source"]
    return ui


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


def _font_line_height(font) -> int:
    try:
        bbox = font.getbbox("Ag")
        return max(bbox[3] - bbox[1], 16)
    except Exception:
        return 18


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
    img = _load_item_image(filename)
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


def _calc_buff_section_area(
    measure: ImageDraw.ImageDraw,
    buff: dict,
    font_header,
    font_body,
    ui: dict,
) -> int:
    area = 20 + 30
    name_h = max(BUFF_ICON_SLOT[1], _font_line_height(font_body))
    area += name_h + 8
    if buff.get("tooltip"):
        stat_value_x = _stat_value_x(measure, [{"label": ui["buff_tooltip"]}], font_body)
        area += _stat_value_height(
            measure,
            None,
            stat_value_x,
            {"label": ui["buff_tooltip"], "value": buff["tooltip"]},
            font_body,
            "zh",
        )
    return area


def _draw_buff_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    buff: dict,
    font_header,
    font_body,
    ui: dict,
) -> int:
    draw.text((CARD_PADDING + 10, y), ui["buff"], fill=COLORS["accent"], font=font_header)
    y += 30

    icon_x = CARD_PADDING + 10
    buff_img = _load_item_image(buff.get("image", ""), BUFF_ICON_SLOT)
    row_h = max(BUFF_ICON_SLOT[1], _font_line_height(font_body))
    if buff_img:
        _paste_in_slot(card, buff_img, icon_x, y, BUFF_ICON_SLOT[0], row_h)
    text_x = icon_x + BUFF_ICON_SLOT[0] + 8
    draw.text((text_x, y + 2), buff.get("name", ""), fill=COLORS["text"], font=font_body)
    y += row_h + 8

    tooltip = buff.get("tooltip", "")
    if tooltip:
        stat_value_x = _stat_value_x(draw, [{"label": ui["buff_tooltip"]}], font_body)
        _draw_stat_label(draw, ui["buff_tooltip"], y, font_body, stat_value_x)
        row_h = _draw_stat_value(
            draw,
            card,
            stat_value_x,
            y,
            {"label": ui["buff_tooltip"], "value": tooltip},
            font_body,
            "zh",
        )
        y += row_h
    return y


def _calc_mount_section_area(
    mount: dict,
    font_header,
    font_body,
) -> int:
    area = 20 + 30
    area += _font_line_height(font_body) + 8
    area += MOUNT_PREVIEW_SLOT[1] + 10
    return area


def _draw_mount_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    mount: dict,
    font_header,
    font_body,
    ui: dict,
) -> int:
    draw.text((CARD_PADDING + 10, y), ui["mount"], fill=COLORS["accent"], font=font_header)
    y += 30

    name = mount.get("name", "")
    if name:
        draw.text((CARD_PADDING + 10, y), name, fill=COLORS["text"], font=font_body)
        y += _font_line_height(font_body) + 8

    mount_img = _load_item_image(mount.get("image", ""), MOUNT_PREVIEW_SLOT)
    if mount_img:
        preview_x = CARD_PADDING + 10
        _paste_in_slot(
            card,
            mount_img,
            preview_x,
            y,
            MOUNT_PREVIEW_SLOT[0],
            MOUNT_PREVIEW_SLOT[1],
        )
        y += MOUNT_PREVIEW_SLOT[1] + 10
    return y


def _calc_pet_section_area(
    pet: dict,
    font_header,
    font_body,
) -> int:
    area = 20 + 30
    area += _font_line_height(font_body) + 8
    area += PET_PREVIEW_SLOT[1] + 10
    return area


def _draw_pet_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    pet: dict,
    font_header,
    font_body,
    ui: dict,
) -> int:
    draw.text((CARD_PADDING + 10, y), ui["pet"], fill=COLORS["accent"], font=font_header)
    y += 30

    name = pet.get("name", "")
    if name:
        draw.text((CARD_PADDING + 10, y), name, fill=COLORS["text"], font=font_body)
        y += _font_line_height(font_body) + 8

    pet_img = _load_item_image(pet.get("image", ""), PET_PREVIEW_SLOT)
    if pet_img:
        preview_x = CARD_PADDING + 10
        _paste_in_slot(
            card,
            pet_img,
            preview_x,
            y,
            PET_PREVIEW_SLOT[0],
            PET_PREVIEW_SLOT[1],
        )
        y += PET_PREVIEW_SLOT[1] + 10
    return y


def _normalize_message(text: str) -> str:
    text = text.strip()
    if text.startswith("/"):
        return text[1:].strip()
    return text


def _extract_query_text(text: str) -> str | None:
    """解析查询指令参数，非查询指令时返回 None。"""
    normalized = _normalize_message(text)
    if normalized in ("泰拉更新", "泰拉强制更新") or normalized.startswith("泰拉更新 "):
        return None
    for prefix in ("泰拉查询", "泰拉", "terraria"):
        if normalized == prefix:
            return ""
        if normalized.startswith(prefix + " "):
            return normalized[len(prefix) + 1 :].strip()
    return None


def _is_force_update_command(text: str) -> bool:
    normalized = _normalize_message(text)
    return normalized == "泰拉强制更新"


def _is_update_command(text: str) -> bool:
    normalized = _normalize_message(text)
    return normalized in ("泰拉更新", "泰拉强制更新")


# 匹配 泰拉查询/泰拉/泰拉更新/泰拉强制更新/terraria，无需 / 前缀（/ 也兼容）
_TERRARIA_CMD_RE = r"^/?(泰拉强制更新|泰拉更新|泰拉查询|泰拉|terraria)(\s|$)"

_WING_STAT_LABELS = {
    "zh": {
        "类型",
        "飞行时间",
        "高度（格）",
        "最大水平速度",
        "水平加速度",
        "垂直倍率",
        "稀有度",
    },
    "en": {
        "Type",
        "Flight time",
        "Height (tiles)",
        "Max horizontal speed",
        "Horizontal acceleration",
        "Vertical multiplier",
        "Rarity",
    },
}


def _display_stats(data: dict, locale: str) -> list[dict]:
    stats = data.get("stats", [])
    if data.get("page_type") == "wing" or data.get("from_wings_table"):
        allowed = _WING_STAT_LABELS.get(locale, _WING_STAT_LABELS["zh"])
        stats = [s for s in stats if s.get("label") in allowed]
    return stats


_CARD_UI = {
    "description": "▎描述",
    "source": "▎来源",
    "stats": "▎属性",
    "buff": "▎给予增益",
    "buff_label": "增益",
    "buff_tooltip": "增益提示",
    "mount": "▎召唤坐骑",
    "pet": "▎召唤宠物",
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
    "set_pieces": "▎套装部件",
    "npc_spawn": "▎生成条件",
    "npc_shop": "▎出售物品",
    "npc_preferences": "▎生活偏好",
    "npc_shimmer": "▎微光形态",
    "npc_shop_col_item": "物品",
    "npc_shop_col_price": "花费",
    "npc_shop_col_avail": "何时有售",
    "npc_pref_col_biome": "生物群系",
    "npc_pref_col_neighbor": "邻居",
}


def _item_zh_search_names(key: str, item: dict) -> set[str]:
    names = {key, item.get("name", ""), item.get("wiki_title", "")}
    names.update(item.get("search_terms") or [])
    buff = item.get("buff") or {}
    mount = item.get("mount") or {}
    pet = item.get("pet") or {}
    for label in (buff.get("name"), mount.get("name"), pet.get("name")):
        if not label:
            continue
        names.add(label)
        if label.endswith("坐骑"):
            names.add(label[:-2])
    return {n for n in names if n}


def _biome_zh_search_names(key: str, biome: dict) -> set[str]:
    names = {key, biome.get("name", ""), biome.get("wiki_title", "")}
    names.update(biome.get("search_terms") or [])
    return {n for n in names if n}


def _npc_zh_search_names(key: str, npc: dict) -> set[str]:
    names = {key, npc.get("name", ""), npc.get("wiki_title", "")}
    names.update(npc.get("search_terms") or [])
    return {n for n in names if n}


_SEARCH_INDEX: list[tuple[str, str, frozenset[str]]] | None = None
_SEARCH_INDEX_SIG: tuple | None = None


def _search_index_signature(
    items: dict[str, dict],
    mounts: dict[str, dict],
    pets: dict[str, dict],
    biomes: dict[str, dict] | None = None,
    npcs: dict[str, dict] | None = None,
) -> tuple:
    if biomes is None:
        biomes = {}
    if npcs is None:
        npcs = {}
    return (
        len(items),
        len(mounts),
        len(pets),
        len(biomes),
        len(npcs),
        tuple(sorted(items.keys())),
        tuple(sorted(mounts.keys())),
        tuple(sorted(pets.keys())),
        tuple(sorted(biomes.keys())),
        tuple(sorted(npcs.keys())),
    )


def rebuild_search_index(
    items: dict[str, dict],
    mounts: dict[str, dict],
    pets: dict[str, dict],
    biomes: dict[str, dict] | None = None,
    npcs: dict[str, dict] | None = None,
) -> None:
    """预构建搜索索引，避免每次查询重复计算别名集合。"""
    if biomes is None:
        biomes = {}
    if npcs is None:
        npcs = {}
    global _SEARCH_INDEX, _SEARCH_INDEX_SIG
    entries: list[tuple[str, str, frozenset[str]]] = []
    for pool_name, pool, name_fn in (
        ("biome", biomes, _biome_zh_search_names),
        ("npc", npcs, _npc_zh_search_names),
        ("mount", mounts, _item_zh_search_names),
        ("pet", pets, _item_zh_search_names),
        ("item", items, _item_zh_search_names),
    ):
        for key, item in pool.items():
            zh_names = frozenset(name_fn(key, item))
            entries.append((pool_name, key, zh_names))
    _SEARCH_INDEX = entries
    _SEARCH_INDEX_SIG = _search_index_signature(items, mounts, pets, biomes, npcs)


def _fuzzy_match(query: str, items: dict[str, dict]) -> list[str]:
    """中文名称模糊匹配。"""
    query = query.strip()
    if not query:
        return []

    found: dict[str, int] = {}

    for key, item in items.items():
        for zh_name in _item_zh_search_names(key, item):
            if query == zh_name:
                found[key] = min(found.get(key, 999), 0)
            elif query in zh_name:
                found[key] = min(found.get(key, 999), len(zh_name))

    ranked = sorted(found.items(), key=lambda x: (x[1], x[0]))
    if any(rank == 0 for _, rank in ranked):
        ranked = [(k, r) for k, r in ranked if r == 0]
    return [key for key, _ in ranked]


_POOL_SEARCH_ORDER = ("biome", "npc", "mount", "pet", "item")
_POOL_PRIORITY = {name: idx for idx, name in enumerate(_POOL_SEARCH_ORDER)}
_FUZZY_MATCH_CARD_MAX = 2


def _rank_pool_from_index(query: str, pool_name: str) -> list[str]:
    global _SEARCH_INDEX
    if not _SEARCH_INDEX:
        return []

    query = query.strip()
    if not query:
        return []

    found: dict[str, int] = {}

    for p_name, key, zh_names in _SEARCH_INDEX:
        if p_name != pool_name:
            continue
        for zh_name in zh_names:
            if query == zh_name:
                found[key] = min(found.get(key, 999), 0)
            elif query in zh_name:
                found[key] = min(found.get(key, 999), len(zh_name))

    ranked = sorted(found.items(), key=lambda x: (x[1], x[0]))
    if any(rank == 0 for _, rank in ranked):
        ranked = [(k, r) for k, r in ranked if r == 0]
    return [key for key, _ in ranked]


def _collect_ranked_matches(query: str) -> list[tuple[str, str, int]]:
    """跨池排名：(来源, 键, 匹配度)。0 为精确匹配，越大越模糊。"""
    global _SEARCH_INDEX
    if not _SEARCH_INDEX:
        return []

    query = query.strip()
    if not query:
        return []

    found: dict[tuple[str, str], int] = {}
    for p_name, key, zh_names in _SEARCH_INDEX:
        for zh_name in zh_names:
            if query == zh_name:
                rank = 0
            elif query in zh_name:
                rank = len(zh_name)
            else:
                continue
            slot = (p_name, key)
            found[slot] = min(found.get(slot, 999), rank)

    return sorted(
        ((p, k, r) for (p, k), r in found.items()),
        key=lambda x: (x[2], _POOL_PRIORITY.get(x[0], 99), x[1]),
    )


def _split_search_matches(
    query: str,
    items: dict[str, dict],
    mounts: dict[str, dict],
    pets: dict[str, dict] | None = None,
    biomes: dict[str, dict] | None = None,
    npcs: dict[str, dict] | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """拆分为精确匹配与模糊匹配（均按相关度排序）。"""
    if pets is None:
        pets = {}
    if biomes is None:
        biomes = {}
    if npcs is None:
        npcs = {}
    global _SEARCH_INDEX, _SEARCH_INDEX_SIG
    sig = _search_index_signature(items, mounts, pets, biomes, npcs)
    if _SEARCH_INDEX is None or _SEARCH_INDEX_SIG != sig:
        rebuild_search_index(items, mounts, pets, biomes, npcs)

    exact: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []
    seen_exact: set[str] = set()
    seen_partial: set[str] = set()

    for pool_name, key, rank in _collect_ranked_matches(query):
        if rank == 0:
            if key not in seen_exact:
                exact.append((pool_name, key))
                seen_exact.add(key)
        elif key not in seen_partial:
            partial.append((pool_name, key))
            seen_partial.add(key)

    exact.sort(key=lambda x: (_POOL_PRIORITY.get(x[0], 99), x[1]))
    return exact, partial


def _rank_all_from_index(query: str) -> list[tuple[str, str, int]]:
    return _collect_ranked_matches(query)


def _fuzzy_match_all(
    query: str,
    items: dict[str, dict],
    mounts: dict[str, dict],
    pets: dict[str, dict] | None = None,
    biomes: dict[str, dict] | None = None,
    npcs: dict[str, dict] | None = None,
) -> list[tuple[str, str]]:
    """返回 (来源, 键) 列表，来源为 biome、npc、item、mount 或 pet。"""
    exact, partial = _split_search_matches(
        query, items, mounts, pets, biomes, npcs
    )
    if exact:
        return exact
    return partial


def _format_partial_item_hints(query: str, partial_items: list[str], items: dict) -> str:
    lines = [f"以下物品名称也包含「{query}」：", ""]
    for key in partial_items:
        lines.append(f"· {items[key].get('name', key)}")
    return "\n".join(lines)


def _display_item(item: dict) -> dict:
    return {
        "name": item.get("name", ""),
        "image": item.get("image", ""),
        "stats": item.get("stats", []),
        "recipe": item.get("recipe"),
        "source": item.get("source"),
        "source_rich": item.get("source_rich"),
        "drops": item.get("drops"),
        "description": item.get("description"),
        "description_rich": item.get("description_rich"),
        "set_pieces": item.get("set_pieces"),
        "page_type": item.get("page_type"),
        "from_wings_table": item.get("from_wings_table"),
        "buff": item.get("buff"),
        "mount": item.get("mount"),
        "pet": item.get("pet"),
    }


def _match_list_label(key: str, item: dict, query: str) -> str:
    return item.get("name", key)


def _format_stat_plain(stat: dict, locale: str) -> str:
    stat = normalize_stat_for_display(stat, locale)
    if stat.get("coins"):
        parts = []
        for coin in stat["coins"]:
            abbr = {"pc": "PC", "gc": "GC", "sc": "SC", "cc": "CC"}.get(
                coin.get("type", ""), ""
            )
            parts.append(f"{coin.get('amount', '')} {abbr}".strip())
        return " ".join(parts)
    if resolve_bool_icon(stat) == "yes":
        return "✔"
    if resolve_bool_icon(stat) == "no":
        return "✘"
    if stat.get("segments"):
        parts: list[str] = []
        for seg in stat["segments"]:
            if seg.get("type") == "text":
                parts.append(seg.get("text", ""))
            elif seg.get("type") == "icon":
                alt = seg.get("alt") or seg.get("image", "")
                if alt:
                    parts.append(f"[{alt}]")
        return _format_stat_text("".join(parts), stat.get("extra", ""))
    return _format_stat_text(stat.get("value", ""), stat.get("extra", ""))


def _format_recipe_plain(
    recipe: dict | None,
    fallback_name: str,
) -> list[str]:
    if not recipe:
        return []
    lines: list[str] = []
    station = recipe.get("station", "")
    if station:
        lines.append(f"    {_CARD_UI['station']} {station}")
    ings = " + ".join(_recipe_item_label(ing) for ing in recipe.get("ingredients", []))
    result = _recipe_item_label(recipe.get("result", {}) or {}) or fallback_name
    if ings:
        lines.append(f"    {ings} → {result}")
    return lines


def _format_text_result(data: dict) -> str:
    ui = _card_ui_for_data(data)
    lines = [f"📦 {data.get('name', ui['unknown'])}", "=" * 30]
    is_wing = _is_wing_item(data)

    if not is_wing:
        description = data.get("description")
        if description:
            lines.append("")
            lines.append(ui["description"].lstrip("▎"))
            lines.append("-" * 30)
            for para in description.split("\n\n"):
                lines.append(f"  {para}")

    stat_rows = _display_stats(data, "zh")
    if stat_rows:
        lines.append("")
        lines.append(ui["stats"].lstrip("▎"))
        lines.append("-" * 30)
    for stat in stat_rows:
        label = stat.get("label", "")
        v = _format_stat_plain(stat, "zh")
        if not v and not stat.get("coins") and not resolve_bool_icon(stat):
            if not stat.get("segments"):
                continue
        lines.append(f"  {label}: {v}")

    recipe = data.get("recipe") if not (data.get("set_pieces") or []) else None
    if is_wing and recipe:
        lines.append("")
        lines.append(ui["source"].lstrip("▎"))
        lines.append("-" * 30)
        for rline in _format_recipe_plain(recipe, data.get("name", "")):
            lines.append(rline)
    elif is_wing and data.get("source"):
        lines.append("")
        lines.append(ui["source"].lstrip("▎"))
        lines.append("-" * 30)
        for para in data["source"].split("\n\n"):
            lines.append(f"  {para}")

    if is_wing:
        description = data.get("description")
        if description:
            lines.append("")
            lines.append(ui["description"].lstrip("▎"))
            lines.append("-" * 30)
            for para in description.split("\n\n"):
                lines.append(f"  {para}")

    buff = data.get("buff")
    if buff:
        lines.append("")
        lines.append(ui["buff"].lstrip("▎"))
        lines.append("-" * 30)
        if buff.get("name"):
            lines.append(f"  {ui['buff_label']}: {buff['name']}")
        if buff.get("tooltip"):
            lines.append(f"  {ui['buff_tooltip']}: {buff['tooltip']}")

    mount = data.get("mount")
    if mount:
        lines.append("")
        lines.append(ui["mount"].lstrip("▎"))
        lines.append("-" * 30)
        if mount.get("name"):
            lines.append(f"  {mount['name']}")

    pet = data.get("pet")
    if pet:
        lines.append("")
        lines.append(ui["pet"].lstrip("▎"))
        lines.append("-" * 30)
        if pet.get("name"):
            lines.append(f"  {pet['name']}")

    set_pieces = data.get("set_pieces") or []
    if not recipe:
        recipe = data.get("recipe") if not set_pieces else None
    if set_pieces:
        lines.append("")
        lines.append(ui["set_pieces"].lstrip("▎"))
        lines.append("-" * 30)
        for piece in set_pieces:
            lines.append(f"  · {piece.get('name', '')}")
            for pstat in piece.get("stats", []):
                plabel = pstat.get("label", "")
                pv = _format_stat_plain(pstat, "zh")
                if pv:
                    lines.append(f"      {plabel}: {pv}")
            for rline in _format_recipe_plain(piece.get("recipe"), piece.get("name", "")):
                lines.append(rline)

    if recipe and not is_wing:
        lines.append("")
        lines.append(ui["recipe_title"])
        lines.append("-" * 30)
        for rline in _format_recipe_plain(recipe, data.get("name", "")):
            lines.append(f"  {rline.strip()}")

    drops = data.get("drops")
    if drops:
        lines.append("")
        lines.append(ui["drops"].lstrip("▎"))
        block = drops_display_block(drops, "zh")
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


def _visible_stats(stats: list[dict]) -> list[dict]:
    return [
        s
        for s in stats
        if s.get("value")
        or s.get("extra")
        or s.get("coins")
        or s.get("bool_icon")
        or s.get("segments")
        or resolve_bool_icon(s)
    ]


def _calc_recipe_block_height(
    measure,
    recipe: dict,
    font_small,
    ui: dict,
) -> int:
    if not recipe:
        return 0
    height = 30
    station = recipe.get("station", "")
    if station:
        station_text = f"{ui['station']} {station}"
        station_lines = _wrap_text_lines(
            measure,
            station_text,
            font_small,
            CARD_WIDTH - CARD_PADDING * 2 - 40,
        )
        height += len(station_lines) * 18 + 10
    ing_count = len(recipe.get("ingredients", []))
    if ing_count:
        ing_start_x = CARD_PADDING + 36
        ing_max_x = CARD_WIDTH - CARD_PADDING - 10
        height += (
            _recipe_ingredients_height(
                measure, recipe.get("ingredients", []), font_small, ing_start_x, ing_max_x
            )
            + 44
        )
    else:
        height += 44
    return height


def _draw_recipe_block(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    recipe: dict,
    font_header,
    font_body,
    font_small,
    ui: dict,
) -> int:
    draw.text((CARD_PADDING + 10, y), ui["recipe"], fill=COLORS["accent"], font=font_header)
    y += 30

    station = recipe.get("station", "")
    if station:
        station_text = f"{ui['station']} {station}"
        station_x = CARD_PADDING + 20
        station_max_w = CARD_WIDTH - CARD_PADDING * 2 - 40
        for line in _wrap_text_lines(draw, station_text, font_small, station_max_w):
            draw.text((station_x, y), line, fill=COLORS["label"], font=font_small)
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
        for row_items in _layout_ingredient_rows(
            draw, ingredients, font_small, ing_start_x, ing_max_x
        ):
            x_pos = ing_start_x
            for ing in row_items:
                ing_name = _recipe_item_label(ing)
                ing_img = _load_item_image(ing.get("image", ""), ING_ICON_SLOT)
                if ing_img:
                    _paste_in_slot(card, ing_img, x_pos, y, ING_ICON_SLOT[0], ing_row_h)
                draw.text(
                    (x_pos + ING_ICON_SLOT[0] + 4, y + 2),
                    ing_name,
                    fill=COLORS["text"],
                    font=font_small,
                )
                x_pos += _ingredient_item_width(draw, ing, font_small)
            y += ing_row_h

    result = recipe.get("result", {}) or {}
    result_name = _recipe_item_label(result)
    result_img = _load_item_image(result.get("image", ""), ING_ICON_SLOT)
    draw.text((CARD_PADDING + 20, y), ui["result"], fill=COLORS["accent"], font=font_body)
    rx = CARD_PADDING + 100
    if result_img:
        _paste_in_slot(card, result_img, rx, y, ING_ICON_SLOT[0], ING_ICON_SLOT[1])
        rx += ING_ICON_SLOT[0] + 4
    draw.text((rx, y + 2), result_name, fill=COLORS["title"], font=font_body)
    return y + 36


def _calc_set_pieces_area(
    measure,
    set_pieces: list[dict],
    font_header,
    font_body,
    font_small,
    ui: dict,
    locale: str,
) -> int:
    if not set_pieces:
        return 0
    area = 20
    for piece in set_pieces:
        area += 42
        pstats = _visible_stats(piece.get("stats", []))
        stat_value_x = _stat_value_x(measure, pstats, font_body)
        for stat in pstats:
            area += _stat_value_height(
                measure, None, stat_value_x, stat, font_body, locale
            )
        area += 8
        if piece.get("recipe"):
            area += _calc_recipe_block_height(measure, piece["recipe"], font_small, ui)
        area += 16
    return area


def _draw_set_pieces_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    set_pieces: list[dict],
    font_header,
    font_body,
    font_small,
    ui: dict,
    locale: str,
) -> int:
    draw.text((CARD_PADDING + 10, y), ui["set_pieces"], fill=COLORS["accent"], font=font_header)
    y += 30

    for piece in set_pieces:
        piece_img = _load_item_image(piece.get("image", ""), ING_ICON_SLOT)
        px = CARD_PADDING + 16
        if piece_img:
            _paste_in_slot(card, piece_img, px, y, ING_ICON_SLOT[0], ING_ICON_SLOT[1])
            text_x = px + ING_ICON_SLOT[0] + 8
        else:
            text_x = px
        draw.text(
            (text_x, y + 4),
            piece.get("name", ""),
            fill=COLORS["title"],
            font=font_body,
        )
        y += 40

        pstats = _visible_stats(piece.get("stats", []))
        stat_value_x = _stat_value_x(draw, pstats, font_body)
        for stat in pstats:
            label = stat.get("label", "")
            _draw_stat_label(draw, label, y, font_body, stat_value_x)
            row_h = _draw_stat_value(draw, card, stat_value_x, y, stat, font_body, locale)
            y += row_h

        y += 6
        if piece.get("recipe"):
            y = _draw_recipe_block(
                draw, card, y, piece["recipe"], font_header, font_body, font_small, ui
            )

        draw.line(
            [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
            fill=COLORS["separator"],
            width=1,
        )
        y += 16

    return y


def _generate_item_card(data: dict) -> str:
    _ensure_dirs()
    ui = _card_ui_for_data(data)
    locale = "zh"
    is_wing = _is_wing_item(data)
    output_path = _card_output_path(data.get("name", ""), locale)
    if os.path.isfile(output_path):
        return output_path

    font_title = _try_get_font(26)
    font_header = _try_get_font(20)
    font_body = _try_get_font(16)
    font_small = _try_get_font(13)

    stats = [
        s
        for s in _display_stats(data, locale)
        if s.get("value")
        or s.get("extra")
        or s.get("coins")
        or s.get("bool_icon")
        or s.get("segments")
        or resolve_bool_icon(s)
    ]
    set_pieces = data.get("set_pieces") or []
    recipe = data.get("recipe") if not set_pieces else None
    drops = data.get("drops")
    buff = data.get("buff")
    mount = data.get("mount")
    pet = data.get("pet")
    description_rich = _resolve_description_rich(data)
    source_rich = _resolve_source_rich(data) if is_wing else []

    measure = ImageDraw.Draw(Image.new("RGBA", (CARD_WIDTH, 100)))
    stat_value_x = _stat_value_x(measure, stats, font_body)
    stats_area = 20
    for stat in stats:
        stats_area += _stat_value_height(
            measure, None, stat_value_x, stat, font_body, locale
        )

    desc_area = 0
    if description_rich and (not is_wing or description_rich):
        desc_area = _calc_description_area(measure, description_rich, font_small) + 20

    source_area = 0
    if is_wing and recipe:
        source_area = _calc_recipe_block_height(measure, recipe, font_small, ui)
    elif is_wing and source_rich:
        source_area = _calc_description_area(measure, source_rich, font_small) + 20

    title_area = 60
    sep_area = 30
    recipe_area = 0
    if recipe and not is_wing:
        recipe_area = _calc_recipe_block_height(measure, recipe, font_small, ui)

    drops_area = 0
    if drops:
        drops_area = 20 + _calc_drops_area(drops, locale)
        if recipe or set_pieces or buff or mount or pet or source_area:
            drops_area += 20

    pieces_area = 0
    if set_pieces:
        pieces_area = _calc_set_pieces_area(
            measure, set_pieces, font_header, font_body, font_small, ui, locale
        )

    buff_area = 0
    if buff:
        buff_area = _calc_buff_section_area(measure, buff, font_header, font_body, ui)

    mount_area = 0
    if mount:
        mount_area = _calc_mount_section_area(mount, font_header, font_body)

    pet_area = 0
    if pet:
        pet_area = _calc_pet_section_area(pet, font_header, font_body)

    wing_extra_sep = 20 if is_wing and source_area and description_rich else 0

    total_height = (
        title_area
        + (0 if is_wing else desc_area)
        + stats_area
        + sep_area
        + source_area
        + wing_extra_sep
        + (desc_area if is_wing else 0)
        + buff_area
        + mount_area
        + pet_area
        + pieces_area
        + recipe_area
        + drops_area
        + CARD_PADDING * 2
    )
    card = Image.new("RGBA", (CARD_WIDTH, total_height), COLORS["bg"])
    draw = ImageDraw.Draw(card)

    item_img = _load_item_image(data.get("image", ""), ITEM_ICON_SLOT)
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
    if not is_wing and description_rich:
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

    if is_wing and recipe:
        y = _draw_recipe_block(
            draw, card, y, recipe, font_header, font_body, font_small, ui
        )
        if description_rich:
            draw.line(
                [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
                fill=COLORS["separator"],
                width=1,
            )
            y += 20
    elif is_wing and source_rich:
        source_ui = {**ui, "description": ui["source"]}
        y = _draw_description_section(
            draw, card, y, source_rich, font_header, font_small, source_ui
        )
        if description_rich:
            draw.line(
                [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
                fill=COLORS["separator"],
                width=1,
            )
            y += 20

    if is_wing and description_rich:
        y = _draw_description_section(
            draw, card, y, description_rich, font_header, font_small, ui
        )
        draw.line(
            [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
            fill=COLORS["separator"],
            width=1,
        )
        y += 20

    if buff:
        y = _draw_buff_section(draw, card, y, buff, font_header, font_body, ui)
        draw.line(
            [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
            fill=COLORS["separator"],
            width=1,
        )
        y += 20

    if mount:
        y = _draw_mount_section(draw, card, y, mount, font_header, font_body, ui)
        draw.line(
            [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
            fill=COLORS["separator"],
            width=1,
        )
        y += 20

    if pet:
        y = _draw_pet_section(draw, card, y, pet, font_header, font_body, ui)
        draw.line(
            [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
            fill=COLORS["separator"],
            width=1,
        )
        y += 20

    if set_pieces:
        y = _draw_set_pieces_section(
            draw, card, y, set_pieces, font_header, font_body, font_small, ui, locale
        )

    if recipe and not is_wing:
        y = _draw_recipe_block(
            draw, card, y, recipe, font_header, font_body, font_small, ui
        )

    if drops:
        if recipe or set_pieces or buff or mount or pet:
            draw.line(
                [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
                fill=COLORS["separator"],
                width=1,
            )
            y += 20
        _draw_drops_section(draw, card, y, drops, font_header, font_small, ui, locale)

    card.convert("RGB").save(output_path, "PNG")
    return output_path


def _display_biome(biome: dict) -> dict:
    return {
        "name": biome.get("name", ""),
        "image": biome.get("image", ""),
        "description": biome.get("description"),
        "description_rich": biome.get("description_rich"),
        "page_type": "biome",
    }


def _format_biome_text(data: dict) -> str:
    lines = [data.get("name", ""), ""]
    desc = (data.get("description") or "").strip()
    if desc:
        lines.append(desc)
    return "\n".join(lines)


def _generate_biome_card(data: dict) -> str:
    _ensure_dirs()
    ui = _CARD_UI
    locale = "zh"
    output_path = _card_output_path(data.get("name", ""), locale)
    if os.path.isfile(output_path):
        return output_path

    font_title = _try_get_font(26)
    font_header = _try_get_font(20)
    font_small = _try_get_font(16)
    description_rich = _resolve_description_rich(data)

    measure = ImageDraw.Draw(Image.new("RGBA", (CARD_WIDTH, 100)))
    title_area = 52
    banner_img = _load_item_image(data.get("image", ""), BIOME_BANNER_MAX_SIZE)
    banner_h = banner_img.height if banner_img else 0
    banner_area = banner_h + 16 if banner_h else 0

    desc_area = 0
    if description_rich:
        desc_area = _calc_description_area(measure, description_rich, font_small) + 10

    total_height = CARD_PADDING * 2 + title_area + banner_area + desc_area
    card = Image.new("RGBA", (CARD_WIDTH, total_height), COLORS["bg"])
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [CARD_PADDING, CARD_PADDING, CARD_WIDTH - CARD_PADDING, CARD_PADDING + title_area],
        radius=8,
        fill=COLORS["header_bg"],
    )
    draw.text(
        (CARD_PADDING + 15, CARD_PADDING + 10),
        data.get("name", ui["unknown"]),
        fill=COLORS["title"],
        font=font_title,
    )

    y = CARD_PADDING + title_area + 12
    if banner_img:
        banner_x = CARD_PADDING + max(0, (BIOME_BANNER_MAX_SIZE[0] - banner_img.width) // 2)
        card.paste(banner_img, (banner_x, y), banner_img)
        y += banner_img.height + 16

    if description_rich:
        y = _draw_description_section(
            draw, card, y, description_rich, font_header, font_small, ui
        )

    card.convert("RGB").save(output_path, "PNG")
    return output_path


def _draw_table_hline(draw: ImageDraw.ImageDraw, y: int) -> None:
    draw.line(
        [(CARD_PADDING + 10, y), (CARD_WIDTH - CARD_PADDING - 10, y)],
        fill=COLORS["separator"],
        width=1,
    )


def _npc_entry_label(entry: dict | str) -> str:
    if isinstance(entry, dict):
        return entry.get("name", "")
    return str(entry)


def _npc_pref_entry_lines(entries: list) -> str:
    return "、".join(_npc_entry_label(e) for e in entries if _npc_entry_label(e))


def _draw_npc_icon_entries(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    x: int,
    y: int,
    max_w: int,
    entries: list,
    font,
) -> int:
    if not entries:
        return 0
    cx = x
    cy = y
    line_h = max(NPC_PREF_ICON_SLOT[1], STAT_LINE_HEIGHT)
    for entry in entries:
        label = _npc_entry_label(entry)
        if not label:
            continue
        image = entry.get("image", "") if isinstance(entry, dict) else ""
        icon_w = NPC_PREF_ICON_SLOT[0] + 4 if image else 0
        text_w = _text_width(draw, label, font)
        block_w = icon_w + text_w + 10
        if cx + block_w > x + max_w and cx > x:
            cx = x
            cy += line_h + 4
        if image:
            img = _load_item_image(image, NPC_PREF_ICON_SLOT)
            if img:
                _paste_in_slot(card, img, cx, cy, NPC_PREF_ICON_SLOT[0], line_h)
            cx += icon_w
        draw.text((cx, cy + 2), label, fill=COLORS["value"], font=font)
        cx += text_w + 10
    return max(line_h, cy + line_h - y)


def _calc_npc_icon_entries_height(
    draw: ImageDraw.ImageDraw,
    max_w: int,
    entries: list,
    font,
) -> int:
    if not entries:
        return STAT_LINE_HEIGHT
    cx = 0
    cy = 0
    line_h = max(NPC_PREF_ICON_SLOT[1], STAT_LINE_HEIGHT)
    row_count = 1
    for entry in entries:
        label = _npc_entry_label(entry)
        if not label:
            continue
        image = entry.get("image", "") if isinstance(entry, dict) else ""
        icon_w = NPC_PREF_ICON_SLOT[0] + 4 if image else 0
        text_w = _text_width(draw, label, font)
        block_w = icon_w + text_w + 10
        if cx + block_w > max_w and cx > 0:
            cx = 0
            cy += line_h + 4
            row_count += 1
        cx += block_w
    return row_count * line_h + max(0, row_count - 1) * 4


def _draw_npc_shop_price(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    x: int,
    y: int,
    entry: dict,
    font,
) -> None:
    coins = entry.get("coins") or []
    if coins:
        cx = x
        for coin in coins:
            amount = str(coin.get("amount", ""))
            if amount:
                draw.text((cx, y), amount, fill=COLORS["value"], font=font)
                cx += _text_width(draw, amount, font) + 2
            coin_img = _load_coin_icon(coin.get("type", ""))
            if coin_img:
                card.paste(coin_img, (cx, y + 1), coin_img)
                cx += _COIN_ICON_SIZE[0] + 4
        return
    price = entry.get("price", "")
    if price:
        draw.text((x, y), price, fill=COLORS["value"], font=font)


def _calc_npc_shop_row_height(draw: ImageDraw.ImageDraw, entry: dict, font) -> int:
    avail_w = CARD_WIDTH - CARD_PADDING - NPC_COL_AVAIL
    avail_lines = _wrap_text_lines(draw, entry.get("availability", ""), font, avail_w)
    return max(NPC_TABLE_ROW_MIN, max(len(avail_lines), 1) * STAT_LINE_HEIGHT + 8)


def _calc_npc_shop_section_area(
    measure: ImageDraw.ImageDraw,
    shop: list[dict],
    font_small,
) -> int:
    area = 30 + DROP_TABLE_HEADER + 6
    for entry in shop:
        area += _calc_npc_shop_row_height(measure, entry, font_small) + 2
    return area + 10


def _draw_npc_shop_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    shop: list[dict],
    font_header,
    font_small,
    ui: dict,
) -> int:
    col_item = CARD_PADDING + 16
    draw.text((CARD_PADDING + 10, y), ui["npc_shop"], fill=COLORS["accent"], font=font_header)
    y += 30
    draw.text((col_item, y), ui["npc_shop_col_item"], fill=COLORS["label"], font=font_small)
    draw.text((NPC_COL_PRICE, y), ui["npc_shop_col_price"], fill=COLORS["label"], font=font_small)
    draw.text((NPC_COL_AVAIL, y), ui["npc_shop_col_avail"], fill=COLORS["label"], font=font_small)
    y += DROP_TABLE_HEADER
    _draw_table_hline(draw, y)
    y += 4

    for entry in shop:
        row_h = _calc_npc_shop_row_height(draw, entry, font_small)
        img = _load_item_image(entry.get("image", ""), NPC_SHOP_ICON_SLOT)
        if img:
            _paste_in_slot(card, img, col_item, y, NPC_SHOP_ICON_SLOT[0], row_h)
        text_x = col_item + NPC_SHOP_ICON_SLOT[0] + 6
        draw.text((text_x, y + 4), entry.get("name", ""), fill=COLORS["text"], font=font_small)
        _draw_npc_shop_price(draw, card, NPC_COL_PRICE, y + 4, entry, font_small)
        avail_w = CARD_WIDTH - CARD_PADDING - NPC_COL_AVAIL
        for i, line in enumerate(_wrap_text_lines(draw, entry.get("availability", ""), font_small, avail_w)):
            draw.text((NPC_COL_AVAIL, y + 4 + i * STAT_LINE_HEIGHT), line, fill=COLORS["value"], font=font_small)
        y += row_h
        _draw_table_hline(draw, y)
        y += 2
    return y + 6


def _calc_npc_pref_row_height(
    draw: ImageDraw.ImageDraw,
    row: dict,
    font,
) -> int:
    col_level = CARD_PADDING + 16
    col_biome = col_level + 52
    col_neighbor = 280
    biome_w = col_neighbor - col_biome - 8
    neighbor_w = CARD_WIDTH - CARD_PADDING - col_neighbor
    biome_h = _calc_npc_icon_entries_height(draw, biome_w, row.get("biomes") or [], font)
    neighbor_h = _calc_npc_icon_entries_height(draw, neighbor_w, row.get("neighbors") or [], font)
    return max(NPC_TABLE_ROW_MIN, biome_h, neighbor_h) + 8


def _calc_npc_pref_section_area(
    measure: ImageDraw.ImageDraw,
    preferences: list[dict],
    font_small,
) -> int:
    area = 30 + DROP_TABLE_HEADER + 6
    for row in preferences:
        area += _calc_npc_pref_row_height(measure, row, font_small) + 2
    return area + 10


def _draw_npc_pref_section(
    draw: ImageDraw.ImageDraw,
    card: Image.Image,
    y: int,
    preferences: list[dict],
    font_header,
    font_small,
    ui: dict,
) -> int:
    col_level = CARD_PADDING + 16
    col_biome = col_level + 52
    col_neighbor = 280
    biome_w = col_neighbor - col_biome - 8
    neighbor_w = CARD_WIDTH - CARD_PADDING - col_neighbor

    draw.text((CARD_PADDING + 10, y), ui["npc_preferences"], fill=COLORS["accent"], font=font_header)
    y += 30
    draw.text((col_biome, y), ui["npc_pref_col_biome"], fill=COLORS["label"], font=font_small)
    draw.text((col_neighbor, y), ui["npc_pref_col_neighbor"], fill=COLORS["label"], font=font_small)
    y += DROP_TABLE_HEADER
    _draw_table_hline(draw, y)
    y += 4

    for row in preferences:
        row_h = _calc_npc_pref_row_height(draw, row, font_small)
        draw.text((col_level, y + 4), row.get("level", ""), fill=COLORS["text"], font=font_small)
        _draw_npc_icon_entries(
            draw, card, col_biome, y + 4, biome_w, row.get("biomes") or [], font_small
        )
        _draw_npc_icon_entries(
            draw, card, col_neighbor, y + 4, neighbor_w, row.get("neighbors") or [], font_small
        )
        y += row_h
        _draw_table_hline(draw, y)
        y += 2
    return y + 6


def _npc_preference_lines(preferences: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in preferences:
        parts = [row.get("level", "")]
        biomes = row.get("biomes") or []
        neighbors = row.get("neighbors") or []
        if biomes:
            parts.append("生物群系：" + _npc_pref_entry_lines(biomes))
        if neighbors:
            parts.append("邻居：" + _npc_pref_entry_lines(neighbors))
        lines.append(" · ".join(p for p in parts if p))
    return lines


def _npc_shop_lines(shop: list[dict]) -> list[str]:
    lines: list[str] = []
    for entry in shop:
        parts = [entry.get("name", "")]
        if entry.get("price"):
            parts.append(entry["price"])
        if entry.get("availability"):
            parts.append(entry["availability"])
        line = " · ".join(p for p in parts if p)
        if line:
            lines.append(line)
    return lines


def _npc_spawn_lines(spawn: str) -> list[str]:
    lines: list[str] = []
    for block in spawn.split("\n"):
        block = block.strip()
        if block:
            lines.append(block)
    return lines


def _calc_labeled_text_block(
    measure,
    lines: list[str],
    font,
    *,
    include_header: bool = True,
) -> int:
    max_w = CARD_WIDTH - CARD_PADDING * 2 - 30
    area = (30 if include_header else 0) + 10
    for line in lines:
        wrapped = _wrap_text_lines(measure, line, font, max_w)
        area += max(1, len(wrapped)) * DESC_LINE_HEIGHT + 4
    return area


def _draw_labeled_text_block(
    draw,
    y: int,
    header: str,
    lines: list[str],
    font_header,
    font_body,
) -> int:
    draw.text((CARD_PADDING + 10, y), header, fill=COLORS["accent"], font=font_header)
    y += 30
    x = CARD_PADDING + 20
    max_w = CARD_WIDTH - CARD_PADDING * 2 - 30
    for line in lines:
        for wrapped in _wrap_text_lines(draw, line, font_body, max_w):
            draw.text((x, y), wrapped, fill=COLORS["value"], font=font_body)
            y += DESC_LINE_HEIGHT
        y += 4
    return y + 6


def _display_npc(npc: dict) -> dict:
    return {
        "name": npc.get("name", ""),
        "image": npc.get("image", ""),
        "description": npc.get("description"),
        "spawn": npc.get("spawn"),
        "shop": npc.get("shop"),
        "preferences": npc.get("preferences"),
        "shimmer": npc.get("shimmer"),
        "shimmer_image": npc.get("shimmer_image"),
        "page_type": "npc",
    }


def _format_npc_text(data: dict) -> str:
    ui = _CARD_UI
    lines = [data.get("name", ""), ""]
    if data.get("description"):
        lines.extend([ui["description"].lstrip("▎"), data["description"], ""])
    if data.get("spawn"):
        lines.extend([ui["npc_spawn"].lstrip("▎"), data["spawn"], ""])
    if data.get("shop"):
        lines.append(ui["npc_shop"].lstrip("▎"))
        lines.extend(_npc_shop_lines(data["shop"]))
        lines.append("")
    if data.get("preferences"):
        lines.append(ui["npc_preferences"].lstrip("▎"))
        lines.extend(_npc_preference_lines(data["preferences"]))
        lines.append("")
    if data.get("shimmer"):
        lines.extend([ui["npc_shimmer"].lstrip("▎"), data["shimmer"]])
    return "\n".join(lines).strip()


def _generate_npc_card(data: dict) -> str:
    _ensure_dirs()
    ui = _CARD_UI
    locale = "zh"
    output_path = _card_output_path(data.get("name", ""), locale, kind="npc")
    if os.path.isfile(output_path):
        return output_path

    font_title = _try_get_font(26)
    font_header = _try_get_font(20)
    font_small = _try_get_font(16)

    measure = ImageDraw.Draw(Image.new("RGBA", (CARD_WIDTH, 100)))
    title_area = 52
    sprite_img = _load_item_image(data.get("image", ""), NPC_SPRITE_MAX_SIZE)
    sprite_area = sprite_img.height + 16 if sprite_img else 0

    desc_lines = [data["description"]] if data.get("description") else []
    spawn_lines = _npc_spawn_lines(data.get("spawn", ""))
    shop = data.get("shop") or []
    preferences = data.get("preferences") or []
    shimmer_lines = [data["shimmer"]] if data.get("shimmer") else []
    shimmer_img = (
        _load_item_image(data.get("shimmer_image", ""), NPC_SHIMMER_MAX_SIZE)
        if data.get("shimmer_image")
        else None
    )
    shimmer_img_area = shimmer_img.height + 12 if shimmer_img else 0

    body_area = 0
    if desc_lines:
        body_area += _calc_labeled_text_block(measure, desc_lines, font_small)
    if spawn_lines:
        body_area += _calc_labeled_text_block(measure, spawn_lines, font_small)
    if shop:
        body_area += _calc_npc_shop_section_area(measure, shop, font_small)
    if preferences:
        body_area += _calc_npc_pref_section_area(measure, preferences, font_small)
    if shimmer_lines:
        body_area += _calc_labeled_text_block(measure, shimmer_lines, font_small)
        body_area += shimmer_img_area

    total_height = CARD_PADDING * 2 + title_area + sprite_area + body_area + 10
    card = Image.new("RGBA", (CARD_WIDTH, total_height), COLORS["bg"])
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [CARD_PADDING, CARD_PADDING, CARD_WIDTH - CARD_PADDING, CARD_PADDING + title_area],
        radius=8,
        fill=COLORS["header_bg"],
    )
    draw.text(
        (CARD_PADDING + 15, CARD_PADDING + 10),
        data.get("name", ui["unknown"]),
        fill=COLORS["title"],
        font=font_title,
    )

    y = CARD_PADDING + title_area + 12
    if sprite_img:
        px = CARD_PADDING + max(0, (CARD_WIDTH - CARD_PADDING * 2 - sprite_img.width) // 2)
        card.paste(sprite_img, (px, y), sprite_img)
        y += sprite_img.height + 16

    if desc_lines:
        y = _draw_labeled_text_block(draw, y, ui["description"], desc_lines, font_header, font_small)
    if spawn_lines:
        y = _draw_labeled_text_block(draw, y, ui["npc_spawn"], spawn_lines, font_header, font_small)
    if shop:
        y = _draw_npc_shop_section(draw, card, y, shop, font_header, font_small, ui)
    if preferences:
        y = _draw_npc_pref_section(draw, card, y, preferences, font_header, font_small, ui)
    if shimmer_lines:
        y = _draw_labeled_text_block(draw, y, ui["npc_shimmer"], shimmer_lines, font_header, font_small)
        if shimmer_img:
            sx = CARD_PADDING + 20
            card.paste(shimmer_img, (sx, y), shimmer_img)

    card.convert("RGB").save(output_path, "PNG")
    return output_path


def _format_update_result(result: dict, force: bool = False) -> str:
    drops_count = result.get("drops_backfill_count", 0)
    desc_count = result.get("desc_backfill_count", 0)
    extra_lines = ""
    if force:
        extra_lines += "\n模式：全量重建"
    if drops_count:
        extra_lines += f"\n掉落来源回填：{drops_count} 个"
    if desc_count:
        extra_lines += f"\n描述回填：{desc_count} 个"
    piece_sync = result.get("piece_sync_count", 0)
    if piece_sync:
        extra_lines += f"\n套装部件同步：{piece_sync} 个"
    mount_new = result.get("mount_new_count", 0)
    mount_total = result.get("mount_total", 0)
    if mount_new or mount_total:
        extra_lines += f"\n坐骑召唤物：{mount_total} 个（本次 +{mount_new}）"
    pet_new = result.get("pet_new_count", 0)
    pet_total = result.get("pet_total", 0)
    if pet_new or pet_total:
        extra_lines += f"\n宠物召唤物：{pet_total} 个（本次 +{pet_new}）"
    biome_new = result.get("biome_new_count", 0)
    biome_total = result.get("biome_total", 0)
    if biome_new or biome_total:
        extra_lines += f"\n生物群系：{biome_total} 个（本次 +{biome_new}）"
    npc_new = result.get("npc_new_count", 0)
    npc_total = result.get("npc_total", 0)
    if npc_new or npc_total:
        extra_lines += f"\n城镇 NPC：{npc_total} 个（本次 +{npc_new}）"
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
        self.mounts: dict[str, dict] = {}
        self.pets: dict[str, dict] = {}
        self.biomes: dict[str, dict] = {}
        self.npcs: dict[str, dict] = {}
        self._load_data()

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
            self._load_data(invalidate_cards=True)
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

    def _load_data(self, *, invalidate_cards: bool = False) -> None:
        global _CARD_CACHE_PRUNED
        self._load_items()
        self._load_mounts()
        self._load_pets()
        self._load_biomes()
        self._load_npcs()
        rebuild_search_index(self.items, self.mounts, self.pets, self.biomes, self.npcs)
        if not _CARD_CACHE_PRUNED:
            _prune_old_card_cache()
            _CARD_CACHE_PRUNED = True
        if invalidate_cards:
            _clear_card_cache()

    def _load_items(self) -> None:
        try:
            self.items = load_items_for_plugin(CATEGORIES_DIR)
            if self.items:
                logger.info(
                    f"泰拉瑞亚查询插件已加载（categories/），共 {len(self.items)} 个物品"
                )
                return
        except Exception as e:
            logger.error(f"加载物品数据失败: {e}")
        self.items = {}

    def _load_mounts(self) -> None:
        try:
            self.mounts = load_mounts_for_plugin(CATEGORIES_DIR)
            if self.mounts:
                logger.info(f"已加载 {len(self.mounts)} 个坐骑召唤物")
                return
        except Exception as e:
            logger.error(f"加载 mounts.json 失败: {e}")
        self.mounts = {}

    def _load_pets(self) -> None:
        try:
            self.pets = load_pets_for_plugin(CATEGORIES_DIR)
            if self.pets:
                logger.info(f"已加载 {len(self.pets)} 个宠物召唤物")
                return
        except Exception as e:
            logger.error(f"加载 pets.json 失败: {e}")
        self.pets = {}

    def _load_biomes(self) -> None:
        try:
            self.biomes = load_biomes_for_plugin(CATEGORIES_DIR)
            if self.biomes:
                logger.info(f"已加载 {len(self.biomes)} 个生物群系")
                return
        except Exception as e:
            logger.error(f"加载 biomes.json 失败: {e}")
        self.biomes = {}

    def _load_npcs(self) -> None:
        try:
            self.npcs = load_npcs_for_plugin(CATEGORIES_DIR)
            if self.npcs:
                logger.info(f"已加载 {len(self.npcs)} 个城镇 NPC")
                return
        except Exception as e:
            logger.error(f"加载 npcs.json 失败: {e}")
        self.npcs = {}

    @filter.regex(_TERRARIA_CMD_RE, priority=10)
    async def on_terraria_command(self, event: AstrMessageEvent):
        """处理泰拉瑞亚查询/更新指令（支持无 / 前缀）。"""
        raw = event.message_str.strip()

        if _is_update_command(raw):
            force = _is_force_update_command(raw)
            async for result in self._handle_update(event, force=force):
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
                "用法: 泰拉查询 <物品名/群系名/NPC名>\n"
                "例如: 泰拉查询 天顶剑\n"
                "      泰拉查询 森林\n"
                "      泰拉查询 军火商"
            )
            return

        if not self.items and not self.mounts and not self.pets and not self.biomes and not self.npcs:
            yield event.plain_result(
                "❌ 离线数据尚未准备。\n"
                "请在 WebUI 配置插件后发送「泰拉更新」，或从仓库拉取已包含的 data/ 目录。"
            )
            return

        matches = _fuzzy_match_all(
            text, self.items, self.mounts, self.pets, self.biomes, self.npcs
        )
        if not matches:
            yield event.plain_result(f"❌ 未找到「{text}」的相关信息。")
            return

        exact, partial = _split_search_matches(
            text, self.items, self.mounts, self.pets, self.biomes, self.npcs
        )

        if exact:
            source, key = exact[0]
            async for result in self._yield_match_card(event, source, key):
                yield result

            partial_items = [k for pool, k in partial if pool == "item"]
            if partial_items:
                yield event.plain_result(
                    _format_partial_item_hints(text, partial_items, self.items)
                )
            return

        if len(matches) > _FUZZY_MATCH_CARD_MAX:
            lines = [f"找到 {len(matches)} 个匹配结果，请输入更精确的名称后重新查询：", ""]
            for source, key in matches:
                pool = {
                    "biome": self.biomes,
                    "npc": self.npcs,
                    "mount": self.mounts,
                    "pet": self.pets,
                    "item": self.items,
                }[source]
                item = pool[key]
                lines.append(f"· {_match_list_label(key, item, text)}")
            yield event.plain_result("\n".join(lines))
            return

        if len(matches) > 1:
            yield event.plain_result(f"找到 {len(matches)} 个匹配结果：")

        for source, key in matches:
            async for result in self._yield_match_card(event, source, key):
                yield result

    async def _yield_match_card(
        self, event: AstrMessageEvent, source: str, key: str
    ):
        pool = {
            "biome": self.biomes,
            "npc": self.npcs,
            "mount": self.mounts,
            "pet": self.pets,
            "item": self.items,
        }[source]
        item = pool[key]
        if source == "biome":
            display = _display_biome(item)
            try:
                card_path = _generate_biome_card(display)
                yield event.image_result(card_path)
            except Exception as e:
                logger.error(f"生成群系图片失败 ({key}): {e}")
                yield event.plain_result(_format_biome_text(display))
            return
        if source == "npc":
            display = _display_npc(item)
            try:
                card_path = _generate_npc_card(display)
                yield event.image_result(card_path)
            except Exception as e:
                logger.error(f"生成 NPC 图片失败 ({key}): {e}")
                yield event.plain_result(_format_npc_text(display))
            return
        display = _display_item(item)
        try:
            card_path = _generate_item_card(display)
            yield event.image_result(card_path)
        except Exception as e:
            logger.error(f"生成图片失败 ({key}): {e}")
            yield event.plain_result(_format_text_result(display))

    async def _handle_update(self, event: AstrMessageEvent, force: bool = False):
        if not self._can_update(event):
            yield event.plain_result("❌ 仅管理员可执行 Wiki 数据更新。")
            return

        if self._update_lock.locked():
            yield event.plain_result("⏳ 已有更新任务进行中，请稍候。")
            return

        if self.show_update_progress:
            if force:
                yield event.plain_result("🔄 正在从 Wiki **全量重建**数据，请稍候…")
            else:
                yield event.plain_result("🔄 正在从 Wiki 增量更新物品数据，请稍候…")

        try:
            result = await self._run_wiki_update(force=force)
            yield event.plain_result(_format_update_result(result, force=force))
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
