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
    normalize_stat_for_display,
    update_wiki_data,
)

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_PLUGIN_DIR, "assets", "fonts")
_COIN_DIR = os.path.join(_PLUGIN_DIR, "assets", "coins")
_COIN_ICON_SIZE = (18, 18)
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
COLORS = {
    "bg": (30, 30, 35, 230),
    "header_bg": (45, 45, 55, 255),
    "text": (220, 220, 220),
    "title": (255, 215, 0),
    "label": (180, 180, 200),
    "value": (255, 255, 255),
    "accent": (100, 180, 255),
    "separator": (60, 60, 70),
}


def _ensure_dirs() -> None:
    for d in (DATA_DIR, IMAGES_DIR, CARDS_DIR):
        os.makedirs(d, exist_ok=True)


def _image_path(filename: str) -> str:
    if not filename:
        return ""
    return os.path.join(IMAGES_DIR, filename)


def _load_image(path: str, size: tuple[int, int] | None = None) -> Image.Image | None:
    if not path or not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
        if size:
            img = img.resize(size, Image.LANCZOS)
        return img
    except Exception:
        return None


def _load_coin_icon(coin_type: str) -> Image.Image | None:
    filename = COIN_SPECS.get(coin_type, "")
    if not filename:
        return None
    return _load_image(os.path.join(_COIN_DIR, filename), _COIN_ICON_SIZE)


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

    v_text = _format_stat_text(stat.get("value", ""), stat.get("extra", ""))
    if not v_text:
        return 0

    color = stat.get("color") if label in RARITY_LABELS else COLORS["value"]
    max_width = CARD_WIDTH - CARD_PADDING - x
    lines = _wrap_text_lines(draw, v_text, font, max_width)
    for i, line in enumerate(lines):
        draw.text((x, y + i * STAT_LINE_HEIGHT), line, fill=color, font=font)
    return max(STAT_MIN_ROW, len(lines) * STAT_LINE_HEIGHT + 6)


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
        "stats": "▎属性",
        "recipe": "▎合成配方",
        "station": "制作站:",
        "materials": "材料:",
        "result": "→ 产物:",
        "unknown": "未知物品",
        "recipe_title": "📜 合成配方",
    },
    "en": {
        "stats": "▎Stats",
        "recipe": "▎Recipe",
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
            "recipe": en.get("recipe"),
        }
    return {
        "name": item.get("name", ""),
        "image": item.get("image", ""),
        "stats": item.get("stats", []),
        "recipe": item.get("recipe"),
    }


def _match_label(key: str, item: dict, locale: str) -> str:
    if locale == "en":
        return _item_en_name(item) or item.get("name", key)
    return item.get("name", key)


def _format_text_result(data: dict, locale: str = "zh") -> str:
    ui = _CARD_UI.get(locale, _CARD_UI["zh"])
    lines = [f"📦 {data.get('name', ui['unknown'])}", "=" * 30]

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
        ings = " + ".join(ing.get("name", "") for ing in recipe.get("ingredients", []))
        result = recipe.get("result", {}).get("name", data.get("name", ""))
        if ings:
            lines.append(f"  {ings} → {result}")

    return "\n".join(lines)


def _generate_item_card(data: dict, locale: str = "zh") -> str:
    _ensure_dirs()
    ui = _CARD_UI.get(locale, _CARD_UI["zh"])

    font_title = _try_get_font(26)
    font_header = _try_get_font(20)
    font_body = _try_get_font(16)
    font_small = _try_get_font(13)

    stats = [s for s in data.get("stats", []) if s.get("value") or s.get("extra")]
    recipe = data.get("recipe")

    measure = ImageDraw.Draw(Image.new("RGBA", (CARD_WIDTH, 100)))
    stats_area = 20
    for stat in stats:
        label = stat.get("label", "")
        label_bbox = measure.textbbox((0, 0), label, font=font_body)
        label_w = label_bbox[2] - label_bbox[0]
        value_x = CARD_PADDING + 30 + label_w + 20
        stats_area += _stat_value_height(measure, None, value_x, stat, font_body, locale)

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
        recipe_area += max(1, (ing_count + 3) // 4) * 36 + 44

    total_height = title_area + stats_area + sep_area + recipe_area + CARD_PADDING * 2
    card = Image.new("RGBA", (CARD_WIDTH, total_height), COLORS["bg"])
    draw = ImageDraw.Draw(card)

    item_img = _load_image(_image_path(data.get("image", "")), (48, 48))
    draw.rounded_rectangle(
        [CARD_PADDING, CARD_PADDING, CARD_WIDTH - CARD_PADDING, CARD_PADDING + title_area],
        radius=8,
        fill=COLORS["header_bg"],
    )

    icon_x = CARD_PADDING + 15
    if item_img:
        card.paste(item_img, (icon_x, CARD_PADDING + 6), item_img)
        text_x = icon_x + 60
    else:
        text_x = icon_x

    draw.text(
        (text_x, CARD_PADDING + 6),
        data.get("name", ui["unknown"]),
        fill=COLORS["title"],
        font=font_title,
    )

    y = CARD_PADDING + title_area + 10
    draw.text((CARD_PADDING + 10, y), ui["stats"], fill=COLORS["accent"], font=font_header)
    y += 30

    for stat in stats:
        label = stat.get("label", "")
        draw.text((CARD_PADDING + 20, y), label, fill=COLORS["label"], font=font_body)
        bbox = draw.textbbox((0, 0), label, font=font_body)
        label_w = bbox[2] - bbox[0]
        value_x = CARD_PADDING + 30 + label_w + 20
        row_h = _draw_stat_value(draw, card, value_x, y, stat, font_body, locale)
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
            ing_row_h = 36
            for i in range(0, len(ingredients), 4):
                row_items = ingredients[i : i + 4]
                x_pos = ing_start_x
                for ing in row_items:
                    ing_name = ing.get("name", "")
                    ing_img = _load_image(_image_path(ing.get("image", "")), (28, 28))
                    if ing_img:
                        card.paste(ing_img, (x_pos, y), ing_img)
                    draw.text((x_pos + 32, y + 2), ing_name, fill=COLORS["text"], font=font_small)
                    bbox = draw.textbbox((0, 0), ing_name, font=font_small)
                    x_pos += 32 + (bbox[2] - bbox[0]) + 15
                y += ing_row_h

        result = recipe.get("result", {})
        result_name = result.get("name", data.get("name", ""))
        result_img = _load_image(_image_path(result.get("image", "")), (28, 28))
        draw.text((CARD_PADDING + 20, y), ui["result"], fill=COLORS["accent"], font=font_body)
        rx = CARD_PADDING + 100
        if result_img:
            card.paste(result_img, (rx, y), result_img)
            rx += 32
        draw.text((rx, y + 2), result_name, fill=COLORS["title"], font=font_body)

    safe_name = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", data.get("name", "unknown"))
    output_path = os.path.join(CARDS_DIR, f"card_v4_{locale}_{safe_name}.png")
    card.convert("RGB").save(output_path, "PNG")
    return output_path


def _format_update_result(result: dict) -> str:
    en_count = result.get("en_backfill_count", 0)
    en_line = f"\n英文数据回填：{en_count} 个" if en_count else ""
    if result.get("new_count", 0) == 0 and en_count == 0:
        return (
            f"✅ Wiki 数据已是最新\n"
            f"当前共 {result.get('total', 0)} 个物品"
        )
    if result.get("new_count", 0) == 0:
        return (
            f"✅ Wiki 数据更新完成\n"
            f"当前总计：{result.get('total', 0)} 个物品{en_line}"
        )
    return (
        f"✅ Wiki 数据更新完成\n"
        f"本次新增：{result.get('new_count', 0)} 个\n"
        f"当前总计：{result.get('total', 0)} 个\n"
        f"新图片：{result.get('images_ok', 0)}/{result.get('images_total', 0)}{en_line}"
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
