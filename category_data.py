"""泰拉瑞亚物品分类：路径、拆分与合并加载。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

try:
    from .prepare_data import fetch_category_members
except ImportError:
    from prepare_data import fetch_category_members

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
CATEGORY_MANIFEST = os.path.join(CATEGORIES_DIR, "manifest.json")

# 根目录遗留路径（迁移前）
LEGACY_ITEMS_JSON = os.path.join(DATA_DIR, "items.json")
LEGACY_MOUNTS_JSON = os.path.join(DATA_DIR, "mounts.json")
LEGACY_PETS_JSON = os.path.join(DATA_DIR, "pets.json")

MOUNTS_FILE = "mounts.json"
PETS_FILE = "pets.json"

# Wiki 多分类重叠时的分配优先级
CATEGORY_PRIORITY: tuple[str, ...] = (
    "coins",
    "ores",
    "bars",
    "gems",
    "ammo",
    "paints",
    "dyes",
    "potions",
    "minions",
    "tools",
    "weapons",
    "armor",
    "accessories",
    "vanity",
    "wings",
    "blocks",
    "walls",
    "crafting_stations",
    "furniture",
    "mechanisms",
    "statues",
    "misc",
)

ITEM_POOL_KEYS = CATEGORY_PRIORITY
MOUNT_KEY = "mounts"
PET_KEY = "pets"


@dataclass(frozen=True)
class ItemCategorySpec:
    key: str
    label: str
    filename: str
    wiki_categories: tuple[str, ...] = ()
    page_types: tuple[str, ...] = ()


ITEM_CATEGORY_SPECS: tuple[ItemCategorySpec, ...] = (
    ItemCategorySpec("tools", "工具", "tools.json", ("Category:工具物品",)),
    ItemCategorySpec("weapons", "武器", "weapons.json", ("Category:武器物品",)),
    ItemCategorySpec("ammo", "弹药", "ammo.json", ("Category:弹药物品",)),
    ItemCategorySpec(
        "armor",
        "盔甲",
        "armor.json",
        ("Category:盔甲物品", "Category:盔甲套装"),
        ("armor_set", "armor_piece"),
    ),
    ItemCategorySpec("furniture", "家具", "furniture.json", ("Category:家具物品",)),
    ItemCategorySpec(
        "crafting_stations", "制作站", "crafting_stations.json", ("Category:制作站物品",)
    ),
    ItemCategorySpec("coins", "钱币", "coins.json", ("Category:钱币",)),
    ItemCategorySpec("ores", "矿石", "ores.json", ("Category:矿石物品",)),
    ItemCategorySpec("bars", "锭", "bars.json", ("Category:锭物品",)),
    ItemCategorySpec("accessories", "配饰", "accessories.json", ("Category:配饰物品",)),
    ItemCategorySpec("blocks", "物块", "blocks.json", ("Category:物块物品",)),
    ItemCategorySpec("walls", "墙", "walls.json", ("Category:墙物品",)),
    ItemCategorySpec("paints", "油漆", "paints.json", ("Category:油漆",)),
    ItemCategorySpec("gems", "宝石", "gems.json", ("Category:宝石物品",)),
    ItemCategorySpec("vanity", "时装物品", "vanity.json", ("Category:时装物品",), ("vanity_set", "vanity_piece")),
    ItemCategorySpec("dyes", "染料", "dyes.json", ("Category:染料物品",)),
    ItemCategorySpec("potions", "药水", "potions.json", ("Category:药水物品",)),
    ItemCategorySpec("statues", "雕像", "statues.json"),
    ItemCategorySpec("mechanisms", "机械", "mechanisms.json", ("Category:机械物品",)),
    ItemCategorySpec("minions", "仆从", "minions.json", ("Category:仆从召唤物品",)),
    ItemCategorySpec("wings", "翅膀", "wings.json", page_types=("wing",)),
    ItemCategorySpec("misc", "杂项", "misc.json", ("Category:其他物品",)),
)

ITEM_CATEGORY_BY_KEY = {spec.key: spec for spec in ITEM_CATEGORY_SPECS}

TITLE_INDEX_PATH = os.path.join(CATEGORIES_DIR, "_title_index.json")
_STATUE_NAME_RE = re.compile(r"雕像$")


def mounts_json_path(categories_dir: str = CATEGORIES_DIR) -> str:
    return os.path.join(categories_dir, MOUNTS_FILE)


def pets_json_path(categories_dir: str = CATEGORIES_DIR) -> str:
    return os.path.join(categories_dir, PETS_FILE)


def category_json_path(key: str, categories_dir: str = CATEGORIES_DIR) -> str:
    if key == MOUNT_KEY:
        return mounts_json_path(categories_dir)
    if key == PET_KEY:
        return pets_json_path(categories_dir)
    return os.path.join(categories_dir, ITEM_CATEGORY_BY_KEY[key].filename)


def all_wiki_categories() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for spec in ITEM_CATEGORY_SPECS:
        for cat in spec.wiki_categories:
            if cat not in seen:
                seen.add(cat)
                out.append(cat)
    return out


def _load_json_dict(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_dict(path: str, data: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def categories_are_split(categories_dir: str = CATEGORIES_DIR) -> bool:
    if os.path.isfile(os.path.join(categories_dir, MOUNTS_FILE)):
        return True
    if os.path.isfile(CATEGORY_MANIFEST):
        return True
    for key in ITEM_POOL_KEYS:
        path = category_json_path(key, categories_dir)
        if os.path.exists(path) and os.path.getsize(path) > 2:
            return True
    return False


def load_legacy_items() -> dict[str, dict]:
    return _load_json_dict(LEGACY_ITEMS_JSON)


def load_legacy_mounts() -> dict[str, dict]:
    if os.path.isfile(mounts_json_path()):
        return _load_json_dict(mounts_json_path())
    return _load_json_dict(LEGACY_MOUNTS_JSON)


def load_legacy_pets() -> dict[str, dict]:
    if os.path.isfile(pets_json_path()):
        return _load_json_dict(pets_json_path())
    return _load_json_dict(LEGACY_PETS_JSON)


def load_split_item_buckets(
    categories_dir: str = CATEGORIES_DIR,
) -> dict[str, dict[str, dict]]:
    buckets: dict[str, dict[str, dict]] = {}
    for key in ITEM_POOL_KEYS:
        data = _load_json_dict(category_json_path(key, categories_dir))
        if data:
            buckets[key] = data
    return buckets


def merge_item_buckets(
    buckets: dict[str, dict[str, dict]] | None = None,
    *,
    categories_dir: str = CATEGORIES_DIR,
) -> dict[str, dict]:
    if buckets is None:
        buckets = load_split_item_buckets(categories_dir)
    merged: dict[str, dict] = {}
    for key in ITEM_POOL_KEYS:
        for name, item in buckets.get(key, {}).items():
            if name not in merged:
                merged[name] = item
    return merged


def load_items_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    return merge_item_buckets(categories_dir=categories_dir)


def load_mounts_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    return _load_json_dict(mounts_json_path(categories_dir))


def load_pets_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    return _load_json_dict(pets_json_path(categories_dir))


def _item_lookup_titles(item: dict) -> set[str]:
    titles: set[str] = set()
    for field in ("wiki_title", "name"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            titles.add(value.strip())
    return titles


def _is_statue_item(name: str, item: dict) -> bool:
    if _STATUE_NAME_RE.search(name):
        return True
    wiki_title = item.get("wiki_title")
    if isinstance(wiki_title, str) and _STATUE_NAME_RE.search(wiki_title):
        return True
    for stat in item.get("stats") or []:
        if stat.get("label") in ("类型", "Type") and "雕像" in str(stat.get("value") or ""):
            return True
    return False


def _pick_category_key(
    item: dict,
    name: str,
    title_to_keys: dict[str, frozenset[str]],
) -> str:
    matched: set[str] = set()
    for title in _item_lookup_titles(item):
        matched.update(title_to_keys.get(title, ()))

    page_type = item.get("page_type")
    if page_type:
        for spec in ITEM_CATEGORY_SPECS:
            if page_type in spec.page_types:
                matched.add(spec.key)

    if item.get("from_wings_table") or item.get("page_type") == "wing":
        matched.add("wings")

    if item.get("page_type") == "armor_piece":
        matched.add("armor")
    elif item.get("page_type") == "vanity_piece":
        matched.add("vanity")

    if _is_statue_item(name, item):
        matched.add("statues")

    for key in CATEGORY_PRIORITY:
        if key in matched:
            return key
    return "misc"


async def build_title_category_map(
    session: aiohttp.ClientSession,
    *,
    refresh: bool = False,
) -> dict[str, frozenset[str]]:
    if not refresh and os.path.isfile(TITLE_INDEX_PATH):
        try:
            with open(TITLE_INDEX_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {title: frozenset(keys) for title, keys in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    title_to_keys: dict[str, set[str]] = {}
    for spec in ITEM_CATEGORY_SPECS:
        for wiki_cat in spec.wiki_categories:
            titles = await fetch_category_members(session, wiki_cat)
            for title in titles:
                title_to_keys.setdefault(title, set()).add(spec.key)

    serializable = {title: sorted(keys) for title, keys in title_to_keys.items()}
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    with open(TITLE_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False)

    return {title: frozenset(keys) for title, keys in title_to_keys.items()}


def assign_items_to_categories(
    items: dict[str, dict],
    title_to_keys: dict[str, frozenset[str]],
    *,
    exclude_names: frozenset[str] | None = None,
) -> dict[str, dict[str, dict]]:
    exclude = exclude_names or frozenset()
    buckets: dict[str, dict[str, dict]] = {key: {} for key in ITEM_POOL_KEYS}
    for name, item in items.items():
        if name in exclude:
            continue
        key = _pick_category_key(item, name, title_to_keys)
        entry = dict(item)
        entry["item_category"] = key
        buckets[key][name] = entry
    return buckets


def write_category_buckets(
    buckets: dict[str, dict[str, dict]],
    *,
    categories_dir: str = CATEGORIES_DIR,
    mounts: dict[str, dict] | None = None,
    pets: dict[str, dict] | None = None,
) -> dict[str, int]:
    os.makedirs(categories_dir, exist_ok=True)
    counts: dict[str, int] = {}

    for key in ITEM_POOL_KEYS:
        path = category_json_path(key, categories_dir)
        data = buckets.get(key, {})
        _write_json_dict(path, data)
        counts[key] = len(data)

    if mounts is not None:
        _write_json_dict(mounts_json_path(categories_dir), mounts)
        counts[MOUNT_KEY] = len(mounts)
    if pets is not None:
        _write_json_dict(pets_json_path(categories_dir), pets)
        counts[PET_KEY] = len(pets)

    manifest_categories = [
        {
            "key": key,
            "label": ITEM_CATEGORY_BY_KEY[key].label,
            "file": ITEM_CATEGORY_BY_KEY[key].filename,
            "count": counts.get(key, 0),
        }
        for key in ITEM_POOL_KEYS
    ]
    if MOUNT_KEY in counts:
        manifest_categories.append(
            {"key": MOUNT_KEY, "label": "坐骑", "file": MOUNTS_FILE, "count": counts[MOUNT_KEY]}
        )
    if PET_KEY in counts:
        manifest_categories.append(
            {"key": PET_KEY, "label": "宠物", "file": PETS_FILE, "count": counts[PET_KEY]}
        )

    manifest = {
        "version": 1,
        "categories": manifest_categories,
        "total": sum(counts.values()),
    }
    manifest_path = os.path.join(categories_dir, "manifest.json")
    _write_json_dict(manifest_path, manifest)
    return counts


def save_merged_items_to_categories(
    items: dict[str, dict],
    *,
    categories_dir: str = CATEGORIES_DIR,
    title_to_keys: dict[str, frozenset[str]] | None = None,
    mounts: dict[str, dict] | None = None,
    pets: dict[str, dict] | None = None,
) -> dict[str, int]:
    """将内存中的 items 字典拆分写入 categories/（需已有 title_to_keys 或仅按 page_type 兜底）。"""
    exclude = frozenset()
    if mounts:
        exclude |= frozenset(mounts.keys())
    if pets:
        exclude |= frozenset(pets.keys())
    buckets = assign_items_to_categories(
        items, title_to_keys or {}, exclude_names=exclude
    )
    return write_category_buckets(
        buckets, categories_dir=categories_dir, mounts=mounts, pets=pets
    )


def load_title_index(categories_dir: str = CATEGORIES_DIR) -> dict[str, frozenset[str]]:
    path = os.path.join(categories_dir, "_title_index.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {title: frozenset(keys) for title, keys in raw.items()}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def persist_items_to_categories(
    items: dict[str, dict],
    *,
    categories_dir: str = CATEGORIES_DIR,
    title_to_keys: dict[str, frozenset[str]] | None = None,
    mounts: dict[str, dict] | None = None,
    pets: dict[str, dict] | None = None,
) -> dict[str, int]:
    os.makedirs(categories_dir, exist_ok=True)
    keys = title_to_keys if title_to_keys is not None else load_title_index(categories_dir)
    mount_data = mounts if mounts is not None else load_mounts_for_plugin(categories_dir)
    pet_data = pets if pets is not None else load_pets_for_plugin(categories_dir)
    return save_merged_items_to_categories(
        items,
        categories_dir=categories_dir,
        title_to_keys=keys,
        mounts=mount_data,
        pets=pet_data,
    )


async def migrate_to_categories_dir(
    *,
    categories_dir: str = CATEGORIES_DIR,
    remove_legacy: bool = False,
) -> dict[str, Any]:
    """从根目录 items/mounts/pets.json 迁移到 categories/。"""
    items = load_legacy_items()
    mounts = _load_json_dict(LEGACY_MOUNTS_JSON)
    pets = _load_json_dict(LEGACY_PETS_JSON)

    connector = aiohttp.TCPConnector(limit=5)
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        title_to_keys = await build_title_category_map(session)

    exclude = frozenset(mounts.keys()) | frozenset(pets.keys())
    buckets = assign_items_to_categories(items, title_to_keys, exclude_names=exclude)
    counts = write_category_buckets(
        buckets, categories_dir=categories_dir, mounts=mounts, pets=pets
    )

    if remove_legacy:
        for path in (LEGACY_ITEMS_JSON, LEGACY_MOUNTS_JSON, LEGACY_PETS_JSON):
            if os.path.isfile(path):
                os.remove(path)

    return {
        "items_source": len(items),
        "mounts": len(mounts),
        "pets": len(pets),
        "split_total": sum(counts.get(k, 0) for k in ITEM_POOL_KEYS),
        "counts": counts,
        "categories_dir": categories_dir,
    }


def format_split_report(result: dict[str, Any]) -> str:
    lines = [
        f"源 items：{result['items_source']} 条",
        f"坐骑：{result['mounts']} 条 → categories/{MOUNTS_FILE}",
        f"宠物：{result['pets']} 条 → categories/{PETS_FILE}",
        f"拆分物品合计：{result['split_total']} 条",
        "",
    ]
    for key in ITEM_POOL_KEYS:
        spec = ITEM_CATEGORY_BY_KEY[key]
        count = result["counts"].get(key, 0)
        lines.append(f"  {spec.label:6}  categories/{spec.filename:22} {count:4}")
    if MOUNT_KEY in result["counts"]:
        lines.append(f"  {'坐骑':6}  categories/{MOUNTS_FILE:22} {result['counts'][MOUNT_KEY]:4}")
    if PET_KEY in result["counts"]:
        lines.append(f"  {'宠物':6}  categories/{PETS_FILE:22} {result['counts'][PET_KEY]:4}")
    return "\n".join(lines)
