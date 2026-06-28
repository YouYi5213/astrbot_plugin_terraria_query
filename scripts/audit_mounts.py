"""Audit mount summon items against 坐骑.html wiki table."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WIKI_PAGE = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "坐骑.html"
MOUNTS_JSON = ROOT / "data" / "terraria_query" / "categories" / "mounts.json"
CATEGORIES_DIR = ROOT / "data" / "terraria_query" / "categories"

from category_data import load_items_for_plugin  # noqa: E402


def _cell_item_name(td) -> str:
    text_link = td.select_one("span > span > a")
    if text_link:
        return text_link.get_text(strip=True)
    for a in td.select("a"):
        title = (a.get("title") or "").strip()
        if title:
            return title
        alt = (a.get("alt") or "").strip()
        if alt:
            return alt
    return td.get_text(strip=True)


def main() -> int:
    html = WIKI_PAGE.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    pairs: list[tuple[str, str]] = []
    seen_rows: set[tuple[str, str]] = set()
    for table in soup.select("table.terraria.lined"):
        for tr in table.select("tr")[1:]:
            tds = tr.select("td")
            if len(tds) < 2:
                continue
            mount_name = _cell_item_name(tds[0])
            item_name = _cell_item_name(tds[1])
            if not mount_name or not item_name:
                continue
            key = (mount_name, item_name)
            if key in seen_rows:
                continue
            seen_rows.add(key)
            pairs.append(key)

    wiki_items = sorted({item for _, item in pairs})
    mounts_data = json.loads(MOUNTS_JSON.read_text(encoding="utf-8"))
    items_data = load_items_for_plugin(str(CATEGORIES_DIR))

    print(f"Wiki table rows: {len(pairs)}, unique items: {len(wiki_items)}")
    print(f"mounts.json entries: {len(mounts_data)}")

    missing = [
        x for x in wiki_items if x not in mounts_data and x not in items_data
    ]
    in_items_only = [
        x for x in wiki_items if x in items_data and x not in mounts_data
    ]
    in_mounts = [x for x in wiki_items if x in mounts_data]
    extra_mounts = [x for x in mounts_data if x not in wiki_items]

    print(f"In mounts.json: {len(in_mounts)}")
    print(f"In items.json only: {in_items_only}")
    print(f"Missing entirely: {missing}")
    print(f"Extra in mounts.json: {extra_mounts}")
    print()

    def searchable_names(item: dict, key: str) -> set[str]:
        names = {key, item.get("name", ""), item.get("wiki_title", ""), item.get("en_name", "")}
        names.update(item.get("search_terms") or [])
        names.update(item.get("aliases") or [])
        buff = item.get("buff") or {}
        mount = item.get("mount") or {}
        names.add(buff.get("name", ""))
        names.add(mount.get("name", ""))
        return {n for n in names if n}

    alias_gaps: list[tuple[str, str, str]] = []
    for mount_name, item_name in pairs:
        item = mounts_data.get(item_name) or items_data.get(item_name)
        if not item:
            alias_gaps.append((mount_name, item_name, "NOT FOUND"))
            continue
        names = searchable_names(item, item_name)
        short = mount_name.removesuffix("坐骑")
        if mount_name not in names and short not in names:
            buff = (item.get("buff") or {}).get("name", "")
            mount = (item.get("mount") or {}).get("name", "")
            alias_gaps.append((mount_name, item_name, f"buff={buff!r} mount={mount!r}"))

    print(f"Mount display name not searchable: {len(alias_gaps)}")
    for row in alias_gaps:
        print(f"  {row[0]!r} -> item {row[1]!r}: {row[2]}")

    incomplete = []
    for item_name in in_mounts:
        item = mounts_data[item_name]
        if not item.get("buff") or not item.get("mount"):
            incomplete.append(
                (item_name, bool(item.get("buff")), bool(item.get("mount")))
            )
    if incomplete:
        print()
        print("Incomplete mount cards (missing buff/mount section):")
        for row in incomplete:
            print(f"  {row[0]}: buff={row[1]} mount={row[2]}")

    out = ROOT / "scripts" / "audit_mounts_result.json"
    out.write_text(
        json.dumps(
            {
                "wiki_pairs": pairs,
                "wiki_items": wiki_items,
                "missing": missing,
                "in_items_only": in_items_only,
                "extra_mounts": extra_mounts,
                "alias_gaps": [
                    {"mount": m, "item": i, "detail": d} for m, i, d in alias_gaps
                ],
                "incomplete": incomplete,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return 1 if missing or alias_gaps else 0


if __name__ == "__main__":
    sys.exit(main())
