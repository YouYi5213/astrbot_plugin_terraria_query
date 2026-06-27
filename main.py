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

from prepare_data import update_wiki_data

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


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


def _try_get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


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


def _fuzzy_match(query: str, items: dict[str, dict]) -> list[str]:
    query = query.strip()
    if not query:
        return []
    if query in items:
        return [query]
    matches = [name for name in items if query in name]
    matches.sort(key=lambda n: (len(n), n))
    return matches


def _format_text_result(data: dict) -> str:
    lines = [f"📦 {data.get('name', '未知物品')}", "=" * 30]

    for stat in data.get("stats", []):
        label = stat.get("label", "")
        value = stat.get("value", "")
        extra = stat.get("extra", "")
        if not value and not extra:
            continue
        v = value + (f" ({extra})" if extra else "")
        lines.append(f"  {label}: {v}")

    recipe = data.get("recipe")
    if recipe:
        lines.append("")
        lines.append("📜 合成配方")
        lines.append("-" * 30)
        station = recipe.get("station", "")
        if station:
            lines.append(f"  制作站: {station}")
        ings = " + ".join(ing.get("name", "") for ing in recipe.get("ingredients", []))
        result = recipe.get("result", {}).get("name", data.get("name", ""))
        if ings:
            lines.append(f"  {ings} → {result}")

    return "\n".join(lines)


def _generate_item_card(data: dict) -> str:
    _ensure_dirs()

    font_title = _try_get_font(26)
    font_header = _try_get_font(20)
    font_body = _try_get_font(16)
    font_small = _try_get_font(13)

    stats = [s for s in data.get("stats", []) if s.get("value") or s.get("extra")]
    recipe = data.get("recipe")

    title_area = 60
    stats_area = len(stats) * ROW_HEIGHT + 20
    sep_area = 30
    recipe_area = 0
    if recipe:
        recipe_area = 80
        ing_count = len(recipe.get("ingredients", []))
        recipe_area += max(1, (ing_count + 3) // 4) * 60 + 30

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
        data.get("name", "未知物品"),
        fill=COLORS["title"],
        font=font_title,
    )

    y = CARD_PADDING + title_area + 10
    draw.text((CARD_PADDING + 10, y), "▎属性", fill=COLORS["accent"], font=font_header)
    y += 30

    for stat in stats:
        label = stat.get("label", "")
        value = stat.get("value", "")
        extra = stat.get("extra", "")
        draw.text((CARD_PADDING + 20, y), label, fill=COLORS["label"], font=font_body)
        v_text = value + (f" ({extra})" if extra else "")
        bbox = draw.textbbox((0, 0), label, font=font_body)
        label_w = bbox[2] - bbox[0]
        draw.text((CARD_PADDING + 30 + label_w + 20, y), v_text, fill=COLORS["value"], font=font_body)
        y += ROW_HEIGHT

    y += 10
    draw.line(
        [CARD_PADDING + 10, y, CARD_WIDTH - CARD_PADDING - 10, y],
        fill=COLORS["separator"],
        width=1,
    )
    y += 20

    if recipe:
        draw.text((CARD_PADDING + 10, y), "▎合成配方", fill=COLORS["accent"], font=font_header)
        y += 30

        station = recipe.get("station", "")
        if station:
            draw.text((CARD_PADDING + 20, y), f"制作站: {station}", fill=COLORS["label"], font=font_small)
            y += 22

        ingredients = recipe.get("ingredients", [])
        if ingredients:
            draw.text((CARD_PADDING + 20, y), "材料:", fill=COLORS["label"], font=font_small)
            y += 5
            for i in range(0, len(ingredients), 4):
                row_items = ingredients[i : i + 4]
                x_pos = CARD_PADDING + 20
                for ing in row_items:
                    ing_name = ing.get("name", "")
                    ing_img = _load_image(_image_path(ing.get("image", "")), (28, 28))
                    if ing_img:
                        card.paste(ing_img, (x_pos, y), ing_img)
                    draw.text((x_pos + 32, y + 2), ing_name, fill=COLORS["text"], font=font_small)
                    bbox = draw.textbbox((0, 0), ing_name, font=font_small)
                    x_pos += 32 + (bbox[2] - bbox[0]) + 15
                y += 35

        result = recipe.get("result", {})
        result_name = result.get("name", data.get("name", ""))
        result_img = _load_image(_image_path(result.get("image", "")), (28, 28))
        draw.text((CARD_PADDING + 20, y), "→ 产物:", fill=COLORS["accent"], font=font_body)
        rx = CARD_PADDING + 100
        if result_img:
            card.paste(result_img, (rx, y), result_img)
            rx += 32
        draw.text((rx, y + 2), result_name, fill=COLORS["title"], font=font_body)

    safe_name = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", data.get("name", "unknown"))
    output_path = os.path.join(CARDS_DIR, f"card_{safe_name}.png")
    card.convert("RGB").save(output_path, "PNG")
    return output_path


def _format_update_result(result: dict) -> str:
    if result.get("new_count", 0) == 0:
        return (
            f"✅ Wiki 数据已是最新\n"
            f"当前共 {result.get('total', 0)} 个物品"
        )
    return (
        f"✅ Wiki 数据更新完成\n"
        f"本次新增：{result.get('new_count', 0)} 个\n"
        f"当前总计：{result.get('total', 0)} 个\n"
        f"新图片：{result.get('images_ok', 0)}/{result.get('images_total', 0)}"
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
            yield event.plain_result("用法: 泰拉查询 <物品名>\n例如: 泰拉查询 天顶剑")
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
            lines.extend(f"· {name}" for name in matches)
            yield event.plain_result("\n".join(lines))
            return

        if len(matches) > 1:
            yield event.plain_result(f"找到 {len(matches)} 个匹配结果：")

        for name in matches:
            data = self.items[name]
            try:
                card_path = _generate_item_card(data)
                yield event.image_result(card_path)
            except Exception as e:
                logger.error(f"生成图片失败 ({name}): {e}")
                yield event.plain_result(_format_text_result(data))

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
