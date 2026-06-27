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
import os
import re
import sys
from urllib.parse import quote, unquote

import aiohttp
from bs4 import BeautifulSoup

WIKI_BASE = "https://terraria.wiki.gg/zh"
API_URL = f"{WIKI_BASE}/api.php"
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


def _parse_stat_value(td) -> tuple[str, str]:
    extra_el = td.select_one(".small-bold, .knockback, .usetime")
    extra = _clean_text(extra_el.get_text(" ", strip=True)) if extra_el else ""
    if extra_el:
        extra_el.extract()
    value = _clean_text(td.get_text())
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


async def fetch_page_html(session: aiohttp.ClientSession, title: str) -> str | None:
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
                API_URL, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=25)
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


async def main(limit: int | None = None, force: bool = False) -> None:
    os.makedirs(IMAGES_DIR, exist_ok=True)

    items: dict[str, dict] = {} if force else _load_existing_items()
    if force:
        print("全量模式：将重新抓取所有物品", flush=True)
    else:
        print(f"增量模式：已有 {len(items)} 个物品，跳过已存在的", flush=True)

    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        print("正在收集分类页面列表...", flush=True)
        all_titles: set[str] = set()
        for cat in CATEGORIES:
            titles = await fetch_category_members(session, cat)
            print(f"  {cat}: {len(titles)} 页", flush=True)
            all_titles.update(titles)
            await asyncio.sleep(0.3)

        title_list = sorted(all_titles)
        if not force:
            title_list = [t for t in title_list if t not in items]
        if limit:
            title_list = title_list[:limit]
        print(f"共 {len(title_list)} 个待处理页面", flush=True)

        image_urls: dict[str, str] = {}
        new_count = 0

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
            items[name] = item
            image_urls.update(_collect_image_urls(item))
            new_count += 1
            if i % 50 == 0 or i == len(title_list):
                print(f"  已扫描 {i}/{len(title_list)}，本次新增 {new_count}，总计 {len(items)}", flush=True)
            await asyncio.sleep(0.15)

        print(f"正在下载 {len(image_urls)} 张新图片...", flush=True)
        semaphore = asyncio.Semaphore(8)
        tasks = [
            download_image(session, fn, url, semaphore)
            for fn, url in image_urls.items()
        ]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r)
        print(f"  图片下载完成: {ok}/{len(image_urls)}", flush=True)

    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"已保存 {len(items)} 个物品到 {ITEMS_JSON}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="准备泰拉瑞亚 Wiki 离线数据")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个页面（调试用）")
    parser.add_argument("--force", action="store_true", help="全量重建，覆盖已有数据")
    args = parser.parse_args()
    try:
        asyncio.run(main(limit=args.limit, force=args.force))
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(1)
