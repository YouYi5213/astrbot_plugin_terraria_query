"""
泰拉瑞亚 Wiki 离线数据准备脚本
================================
一次性运行，从 terraria.wiki.gg 中文 Wiki 爬取物品数据并保存到本地。

用法:
    python prepare_data.py              # 增量更新，仅抓取新增物品
    python prepare_data.py --limit 20   # 调试：仅处理前 20 个新页面
    python prepare_data.py --force      # 全量重建（覆盖已有数据）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from urllib.parse import quote, unquote

import aiohttp
from bs4 import BeautifulSoup

WIKI_BASE = "https://terraria.wiki.gg/zh"
API_URL = f"{WIKI_BASE}/api.php"
API_URL_EN = "https://terraria.wiki.gg/api.php"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
ITEMS_JSON = os.path.join(DATA_DIR, "items.json")

CATEGORIES = [
    "Category:近战武器",
    "Category:远程武器",
    "Category:魔法武器",
    "Category:召唤武器",
    "Category:武器物品",
    "Category:工具物品",
    "Category:制作材料物品",
    "Category:盔甲物品",
    "Category:盔甲套装",
    "Category:配饰物品",
    "Category:治疗物品",
]

HEADERS = {
    "User-Agent": "AstrBot-TerrariaQuery/1.0 (offline data preparation; +https://docs.astrbot.app)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

logger = logging.getLogger(__name__)


def _clean_text(text: str) -> str:
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text.strip()


def _image_url_from_src(src: str) -> str:
    if src.startswith("http"):
        return src
    if src.startswith("//"):
        return "https:" + src
    return "https://terraria.wiki.gg" + src


def _filename_from_url(url: str) -> str:
    name = unquote(url.split("/")[-1].split("?")[0])
    return name or "unknown.png"


RARITY_LABELS = frozenset({"稀有度", "Rarity"})
SELL_LABELS = frozenset({"卖出", "Sell"})

RARITY_SORTKEY_HEX = {
    "00*": "#b8b8b8",
    "01*": "#ffffff",
    "02*": "#5a9cff",
    "03*": "#55dd55",
    "04*": "#ffaa44",
    "05*": "#ff7777",
    "06*": "#ff88bb",
    "07*": "#cc88ff",
    "08*": "#aa55ff",
    "09*": "#dd66ff",
    "10*": "#ff5050",
    "11*": "#ffff66",
    "12*": "#88ff88",
}

RARITY_SORTKEY_ZH = {
    "00*": "灰色",
    "01*": "白色",
    "02*": "蓝色",
    "03*": "绿色",
    "04*": "橙色",
    "05*": "浅红色",
    "06*": "粉红色",
    "07*": "浅紫色",
    "08*": "紫色",
    "09*": "淡紫色",
    "10*": "红色",
    "11*": "黄色",
    "12*": "渐变色",
}

RARITY_SORTKEY_EN = {
    "00*": "Gray",
    "01*": "White",
    "02*": "Blue",
    "03*": "Green",
    "04*": "Orange",
    "05*": "Light Red",
    "06*": "Pink",
    "07*": "Light Purple",
    "08*": "Purple",
    "09*": "Violet",
    "10*": "Red",
    "11*": "Yellow",
    "12*": "Gradient",
}

COIN_SPECS = {
    "pc": "Platinum_Coin.png",
    "gc": "Gold_Coin.png",
    "sc": "Silver_Coin.png",
    "cc": "Copper_Coin.png",
}


def _extract_rarity_sortkey(td) -> str:
    sk = td.select_one("s.sortkey")
    if sk:
        return _clean_text(sk.get_text())
    txt = _clean_text(td.get_text())
    if re.fullmatch(r"\d+\*", txt):
        return txt
    return ""


def _extract_rarity_name_from_title(title: str) -> str:
    if not title:
        return ""
    match = re.match(r"^([^（(]+)", title.strip())
    return match.group(1).strip() if match else ""


def _parse_rarity_stat(td, label: str) -> dict:
    sortkey = _extract_rarity_sortkey(td)
    link = td.select_one(".rarity a") or td.select_one("a")
    title = link.get("title", "") if link else ""
    en_name = _extract_rarity_name_from_title(title)
    if label == "稀有度":
        display = RARITY_SORTKEY_ZH.get(sortkey, en_name or sortkey)
    else:
        display = en_name or RARITY_SORTKEY_EN.get(sortkey, sortkey)
    return {
        "label": label,
        "value": display or _clean_text(td.get_text()),
        "extra": "",
        "sortkey": sortkey,
        "color": RARITY_SORTKEY_HEX.get(sortkey, "#ffffff"),
    }


def _parse_sell_stat(td, label: str) -> dict:
    coins: list[dict] = []
    for cls, image in COIN_SPECS.items():
        el = td.select_one(f"span.{cls}")
        if not el:
            continue
        match = re.search(r"(\d+)", _clean_text(el.get_text()))
        if match:
            coins.append({"type": cls, "amount": match.group(1), "image": image})
    value = "" if coins else _clean_text(td.get_text())
    return {"label": label, "value": value, "extra": "", "coins": coins}


def parse_sell_text_to_coins(text: str) -> list[dict]:
    coins: list[dict] = []
    for match in re.finditer(r"(\d+)\s*(PC|GC|SC|CC)", text, re.I):
        abbr = match.group(2).lower()
        cls = {"pc": "pc", "gc": "gc", "sc": "sc", "cc": "cc"}.get(abbr)
        if cls:
            coins.append(
                {"type": cls, "amount": match.group(1), "image": COIN_SPECS[cls]}
            )
    return coins


def normalize_stat_for_display(stat: dict, locale: str = "zh") -> dict:
    stat = dict(stat)
    label = stat.get("label", "")

    if label in RARITY_LABELS:
        if stat.get("color") and stat.get("value") and "*" not in stat.get("value", ""):
            return stat
        sortkey = stat.get("sortkey") or stat.get("value", "")
        if re.fullmatch(r"\d+\*", sortkey):
            names = RARITY_SORTKEY_ZH if locale == "zh" else RARITY_SORTKEY_EN
            stat["value"] = names.get(sortkey, sortkey)
            stat["color"] = RARITY_SORTKEY_HEX.get(sortkey, "#ffffff")
            stat["sortkey"] = sortkey

    if label in SELL_LABELS:
        if stat.get("coins"):
            return stat
        coins = parse_sell_text_to_coins(stat.get("value", ""))
        if coins:
            stat["coins"] = coins
            stat["value"] = ""

    return stat


def _parse_stat_value(td) -> tuple[str, str]:
    extra_el = td.select_one(".small-bold, .knockback, .usetime")
    extra = _clean_text(extra_el.get_text(" ", strip=True)) if extra_el else ""
    if extra_el:
        extra_el.extract()
    value = _clean_text(td.get_text())
    if extra:
        extra_core = extra.strip("()（）[] ")
        if extra_core and extra_core in value:
            extra = ""
    return value, extra


def _parse_item_span(span) -> tuple[str, str]:
    """从 span.i 元素提取 (名称, 图片文件名)"""
    link = span.select_one("a[title]") or span.select_one("a")
    img = span.select_one("img")
    name = ""
    if link:
        name = _clean_text(link.get("title") or link.get_text())
    image = _filename_from_url(_image_url_from_src(img["src"])) if img and img.get("src") else ""
    return name, image


def parse_item_page(html: str, fallback_name: str) -> dict | None:
    """解析物品页面 HTML，无有效 infobox 时返回 None"""
    soup = BeautifulSoup(html, "lxml")
    root = (
        soup.select_one("#mw-content-text .mw-parser-output")
        or soup.select_one(".mw-parser-output")
        or soup
    )
    if root.select_one(".noarticletext"):
        return None

    infobox = soup.select_one("div.infobox.item")
    if not infobox:
        return None

    item: dict = {
        "name": fallback_name,
        "image": "",
        "stats": [],
        "recipe": None,
    }

    title_el = infobox.select_one(".title")
    if title_el:
        item["name"] = _clean_text(title_el.get_text())

    img_el = infobox.select_one(".section.images img")
    if img_el and img_el.get("src"):
        item["image"] = _filename_from_url(_image_url_from_src(img_el["src"]))

    for row in infobox.select(".section.statistics table.stat tr"):
        th, td = row.select_one("th"), row.select_one("td")
        if not th or not td:
            continue
        label = _clean_text(th.get_text())
        if label in RARITY_LABELS:
            item["stats"].append(_parse_rarity_stat(td, label))
            continue
        if label in SELL_LABELS:
            item["stats"].append(_parse_sell_stat(td, label))
            continue
        value, extra = _parse_stat_value(td)
        if not label:
            continue
        item["stats"].append({"label": label, "value": value, "extra": extra})

    for table in soup.select("table.terraria.cellborder.recipes"):
        # 跳过微光嬗变等带 caption 的配方表
        if table.select_one("caption"):
            continue
        rows = [tr for tr in table.select("tbody tr") if tr.get("data-rowid")]
        if not rows:
            continue
        row = rows[0]
        station_el = row.select_one("td.station")
        station = _clean_text(station_el.get_text()) if station_el else ""

        ingredients = []
        for li in row.select("td.ingredients li span.i"):
            name, image = _parse_item_span(li)
            if name:
                ingredients.append({"name": name, "image": image})

        result_el = row.select_one("td.result span.i")
        result_name, result_image = "", ""
        if result_el:
            result_name, result_image = _parse_item_span(result_el)

        item["recipe"] = {
            "station": station,
            "ingredients": ingredients,
            "result": {"name": result_name or item["name"], "image": result_image or item["image"]},
        }
        break

    return item


def _extract_en_locale(en_item: dict, zh_item: dict) -> dict:
    return {
        "name": en_item.get("name", ""),
        "image": en_item.get("image") or zh_item.get("image", ""),
        "stats": en_item.get("stats", []),
        "recipe": en_item.get("recipe"),
    }


async def fetch_en_langlink(session: aiohttp.ClientSession, zh_title: str) -> str | None:
    params = {
        "action": "query",
        "titles": zh_title,
        "prop": "langlinks",
        "lllang": "en",
        "format": "json",
        "redirects": "1",
    }
    try:
        async with session.get(
            API_URL, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("missing"):
            continue
        for link in page.get("langlinks", []):
            if link.get("lang") == "en":
                title = _clean_text(link.get("*", ""))
                if title:
                    return title
    return None


async def fetch_en_page_html(session: aiohttp.ClientSession, en_title: str) -> str | None:
    return await fetch_page_html(session, en_title, api_url=API_URL_EN)


def _guess_en_title_from_image(item: dict) -> str | None:
    image = item.get("image", "")
    if not image:
        return None
    stem = os.path.splitext(image)[0]
    if not stem:
        return None
    return stem.replace("_", " ")


async def attach_en_locale(
    session: aiohttp.ClientSession,
    item: dict,
    zh_title: str,
) -> bool:
    if item.get("en", {}).get("name"):
        return False

    en_title = await fetch_en_langlink(session, zh_title)
    if not en_title:
        en_title = _guess_en_title_from_image(item)

    if not en_title:
        return False

    en_html = await fetch_en_page_html(session, en_title)
    if not en_html:
        return False

    en_item = parse_item_page(en_html, en_title)
    if not en_item or not en_item.get("name"):
        return False

    item["en"] = _extract_en_locale(en_item, item)
    item["en_name"] = en_item["name"]
    return True


async def backfill_en_locales(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
    force: bool = False,
    limit: int | None = None,
) -> int:
    pending = [
        key
        for key, item in items.items()
        if force or not item.get("en", {}).get("name")
    ]
    pending.sort()
    if limit:
        pending = pending[:limit]

    updated = 0
    semaphore = asyncio.Semaphore(8)

    async def _process_one(key: str) -> bool:
        async with semaphore:
            item = items[key]
            zh_title = item.get("wiki_title") or item.get("name") or key
            ok = await attach_en_locale(session, item, zh_title)
            await asyncio.sleep(0.05)
            return ok

    batch_size = 50
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        results = await asyncio.gather(*[_process_one(key) for key in batch])
        updated += sum(1 for ok in results if ok)
        done = batch_start + len(batch)
        logger.info(f"英文数据回填进度 {done}/{len(pending)}，新增 {updated}")
        with open(ITEMS_JSON, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    return updated


async def fetch_category_members(
    session: aiohttp.ClientSession, category: str
) -> list[str]:
    """获取分类下所有页面标题（处理分页）"""
    titles: list[str] = []
    params: dict = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmlimit": "500",
        "format": "json",
    }
    while True:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        for m in members:
            if m.get("ns") == 0:
                titles.append(m["title"])
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        params["cmcontinue"] = cont
        await asyncio.sleep(0.2)
    return titles


async def fetch_page_html(
    session: aiohttp.ClientSession, title: str, api_url: str = API_URL
) -> str | None:
    """通过 MediaWiki API 获取页面 HTML（比直接抓取更稳定）"""
    params = {
        "action": "parse",
        "page": title,
        "format": "json",
        "prop": "text",
        "redirects": "1",
    }
    for attempt in range(3):
        try:
            async with session.get(
                api_url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                if resp.status == 404:
                    return None
                data = await resp.json()
                if "error" in data:
                    code = data["error"].get("code", "")
                    if code in ("missingtitle", "invalidtitle"):
                        return None
                    if attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    return None
                html = data.get("parse", {}).get("text", {}).get("*", "")
                if html and "Just a second" not in html:
                    return html
                if attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def download_image(
    session: aiohttp.ClientSession,
    filename: str,
    url: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    if not filename or not url:
        return False
    local_path = os.path.join(IMAGES_DIR, filename)
    if os.path.exists(local_path):
        return True

    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 404:
                    return False
                content = await resp.read()
                if resp.status >= 500 or not content:
                    return False
            with open(local_path, "wb") as f:
                f.write(content)
            return True
        except Exception:
            return False


def _collect_image_urls(item: dict) -> dict[str, str]:
    """收集物品涉及的所有图片 filename -> url"""
    urls: dict[str, str] = {}
    if item.get("image"):
        fn = item["image"]
        urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    recipe = item.get("recipe")
    if recipe:
        for ing in recipe.get("ingredients", []):
            fn = ing.get("image", "")
            if fn:
                urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
        res = recipe.get("result", {})
        fn = res.get("image", "")
        if fn:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    return urls


def _load_existing_items() -> dict[str, dict]:
    if not os.path.exists(ITEMS_JSON):
        return {}
    try:
        with open(ITEMS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


async def update_wiki_data(
    limit: int | None = None,
    force: bool = False,
    en_limit: int | None = None,
    en_only: bool = False,
) -> dict:
    """增量（或全量）更新 Wiki 离线数据，返回统计信息。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)

    items: dict[str, dict] = {} if force else _load_existing_items()
    before_total = len(items)
    pages_scanned = 0
    new_count = 0
    images_total = 0
    images_ok = 0
    en_backfill_count = 0

    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        if not en_only:
            all_titles: set[str] = set()
            for cat in CATEGORIES:
                titles = await fetch_category_members(session, cat)
                all_titles.update(titles)
                await asyncio.sleep(0.3)

            title_list = sorted(all_titles)
            if not force:
                title_list = [t for t in title_list if t not in items]
            if limit:
                title_list = title_list[:limit]
            pages_scanned = len(title_list)

            image_urls: dict[str, str] = {}

            for i, title in enumerate(title_list, 1):
                html = await fetch_page_html(session, title)
                if not html:
                    continue
                item = parse_item_page(html, title)
                if not item:
                    continue
                name = item["name"]
                if not force and name in items:
                    continue
                item["wiki_title"] = title
                items[name] = item
                await attach_en_locale(session, item, title)
                image_urls.update(_collect_image_urls(item))
                new_count += 1
                if i % 50 == 0 or i == len(title_list):
                    logger.info(
                        f"Wiki 更新进度 {i}/{len(title_list)}，新增 {new_count}，总计 {len(items)}"
                    )
                await asyncio.sleep(0.15)

            images_total = len(image_urls)
            if image_urls:
                semaphore = asyncio.Semaphore(8)
                tasks = [
                    download_image(session, fn, url, semaphore)
                    for fn, url in image_urls.items()
                ]
                results = await asyncio.gather(*tasks)
                images_ok = sum(1 for r in results if r)

        effective_en_limit = en_limit if en_only else (100 if en_limit is None else en_limit)
        en_backfill_count = await backfill_en_locales(
            session,
            items,
            force=bool(en_only and force),
            limit=effective_en_limit,
        )

    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    return {
        "ok": True,
        "force": force,
        "before_total": before_total,
        "new_count": new_count,
        "total": len(items),
        "pages_scanned": pages_scanned,
        "images_total": images_total,
        "images_ok": images_ok,
        "en_backfill_count": en_backfill_count,
    }


async def main(
    limit: int | None = None,
    force: bool = False,
    en_only: bool = False,
    en_limit: int | None = None,
) -> None:
    if en_only:
        existing = _load_existing_items()
        print(f"英文回填模式：已有 {len(existing)} 个物品", flush=True)
    elif force:
        print("全量模式：将重新抓取所有物品", flush=True)
    else:
        existing = _load_existing_items()
        print(f"增量模式：已有 {len(existing)} 个物品，跳过已存在的", flush=True)

    print("正在收集并更新 Wiki 数据...", flush=True)
    result = await update_wiki_data(
        limit=limit, force=force, en_only=en_only, en_limit=en_limit
    )
    print(
        f"更新完成：新增 {result['new_count']} 个，"
        f"共 {result['total']} 个物品，"
        f"新图片 {result['images_ok']}/{result['images_total']}，"
        f"英文回填 {result['en_backfill_count']} 个",
        flush=True,
    )
    print(f"已保存到 {ITEMS_JSON}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="准备泰拉瑞亚 Wiki 离线数据")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个页面（调试用）")
    parser.add_argument("--force", action="store_true", help="全量重建，覆盖已有数据")
    parser.add_argument(
        "--en-only",
        action="store_true",
        help="仅回填英文数据，不抓取中文 Wiki",
    )
    parser.add_argument(
        "--en-limit",
        type=int,
        default=None,
        help="英文回填最多处理 N 个物品（调试用）",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            main(
                limit=args.limit,
                force=args.force,
                en_only=args.en_only,
                en_limit=args.en_limit,
            )
        )
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(1)
