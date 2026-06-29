"""旧版（前代主机 / 3DS）内容的内部标记 — 仅供检索与维护，不在卡片上展示。

统一字段（写入 bosses.json / categories/*.json）：
- internal_tags: 字符串列表，便于 grep / 脚本筛选（如 legacy、old_gen、legacy_boss）
- legacy_scope: 固定为 old_gen
- legacy_source: 条目来源（boss_ingest / boss_drop / navbox / craft_chain / variant_page / armor_set_piece）
- legacy_boss / legacy_item: 布尔快捷标记（与 internal_tags 同步）
- single_mode / exclude_overview: Boss 专用行为标记

卡片渲染前请用 strip_internal_metadata_for_card() 去掉上述字段。
"""

from __future__ import annotations

from typing import Any

# --- internal_tags 取值 ---
TAG_LEGACY = "legacy"
TAG_OLD_GEN = "old_gen"
TAG_LEGACY_BOSS = "legacy_boss"
TAG_LEGACY_ITEM = "legacy_item"
TAG_LEGACY_PART = "legacy_part"

LEGACY_SCOPE_OLD_GEN = "old_gen"

# --- legacy_source 取值 ---
SOURCE_BOSS_INGEST = "boss_ingest"
SOURCE_BOSS_DROP = "boss_drop"
SOURCE_NAVBOX = "navbox"
SOURCE_CRAFT_CHAIN = "craft_chain"
SOURCE_VARIANT_PAGE = "variant_page"
SOURCE_ARMOR_SET_PIECE = "armor_set_piece"

# 写入 JSON、但不应出现在卡片 / 纯文本展示里的键
INTERNAL_METADATA_FIELDS = frozenset(
    {
        "internal_tags",
        "legacy_scope",
        "legacy_source",
        "legacy_boss",
        "legacy_item",
        "legacy_part",
        "single_mode",
        "exclude_overview",
        "wiki_title",
    }
)


def build_internal_tags(*tags: str) -> list[str]:
    return sorted({t for t in tags if t})


def is_legacy_boss(entry: dict | None) -> bool:
    if not entry:
        return False
    if entry.get("legacy_boss"):
        return True
    tags = entry.get("internal_tags") or []
    return TAG_LEGACY_BOSS in tags or (TAG_LEGACY in tags and entry.get("page_type") == "boss")


def is_legacy_item(entry: dict | None) -> bool:
    if not entry:
        return False
    if entry.get("legacy_item"):
        return True
    tags = entry.get("internal_tags") or []
    return TAG_LEGACY_ITEM in tags


def is_legacy_entry(entry: dict | None) -> bool:
    return is_legacy_boss(entry) or is_legacy_item(entry)


def boss_uses_single_mode(entry: dict | None) -> bool:
    if not entry:
        return False
    if entry.get("single_mode"):
        return True
    return is_legacy_boss(entry)


def apply_legacy_boss_metadata(
    boss: dict[str, Any],
    wiki_title: str = "",
    *,
    source: str = SOURCE_BOSS_INGEST,
) -> None:
    boss["legacy_boss"] = True
    boss["exclude_overview"] = True
    boss["single_mode"] = True
    boss["legacy_scope"] = LEGACY_SCOPE_OLD_GEN
    boss["legacy_source"] = source
    boss["internal_tags"] = build_internal_tags(
        TAG_LEGACY,
        TAG_OLD_GEN,
        TAG_LEGACY_BOSS,
    )
    for part in boss.get("parts") or []:
        apply_legacy_boss_part_metadata(part, parent_wiki_title=wiki_title)


def apply_legacy_boss_part_metadata(
    part: dict[str, Any],
    *,
    parent_wiki_title: str = "",
) -> None:
    part["legacy_part"] = True
    part["legacy_scope"] = LEGACY_SCOPE_OLD_GEN
    part["legacy_source"] = SOURCE_BOSS_INGEST
    tags = [TAG_LEGACY, TAG_OLD_GEN, TAG_LEGACY_PART]
    if parent_wiki_title:
        tags.append(f"boss_part:{parent_wiki_title}")
    part["internal_tags"] = build_internal_tags(*tags)


def apply_legacy_item_metadata(
    item: dict[str, Any],
    *,
    source: str,
    parent_set: str | None = None,
) -> None:
    item["legacy_item"] = True
    item["legacy_scope"] = LEGACY_SCOPE_OLD_GEN
    item["legacy_source"] = source
    tags = [TAG_LEGACY, TAG_OLD_GEN, TAG_LEGACY_ITEM]
    if parent_set:
        tags.append(f"parent_set:{parent_set}")
    item["internal_tags"] = build_internal_tags(*tags)


def inherit_legacy_item_from_parent(
    piece: dict[str, Any],
    parent: dict[str, Any],
    *,
    parent_set: str,
) -> None:
    if not is_legacy_item(parent):
        return
    apply_legacy_item_metadata(
        piece,
        source=SOURCE_ARMOR_SET_PIECE,
        parent_set=parent_set,
    )


def strip_internal_metadata_for_card(data: dict) -> dict:
    """复制一份供卡片 / 展示用的 dict，去掉内部维护字段。"""
    out = {k: v for k, v in data.items() if k not in INTERNAL_METADATA_FIELDS}
    parts = out.get("parts")
    if isinstance(parts, list):
        out["parts"] = [
            strip_internal_metadata_for_card(part) if isinstance(part, dict) else part
            for part in parts
        ]
    set_pieces = out.get("set_pieces")
    if isinstance(set_pieces, list):
        cleaned: list = []
        for piece in set_pieces:
            if isinstance(piece, dict):
                cleaned.append(strip_internal_metadata_for_card(piece))
            else:
                cleaned.append(piece)
        out["set_pieces"] = cleaned
    return out


def backfill_internal_tags_on_bosses(bosses: dict[str, dict]) -> int:
    updated = 0
    for boss in bosses.values():
        if not is_legacy_boss(boss) and not boss.get("legacy_boss"):
            continue
        before = tuple(boss.get("internal_tags") or [])
        apply_legacy_boss_metadata(
            boss,
            wiki_title=boss.get("wiki_title") or boss.get("name") or "",
            source=boss.get("legacy_source") or SOURCE_BOSS_INGEST,
        )
        if tuple(boss.get("internal_tags") or []) != before:
            updated += 1
    return updated


def backfill_internal_tags_on_items(items: dict[str, dict]) -> int:
    updated = 0
    parent_legacy: dict[str, bool] = {
        name: is_legacy_item(item) for name, item in items.items()
    }
    for name, item in items.items():
        changed = False
        if item.get("legacy_item") or TAG_LEGACY_ITEM in (item.get("internal_tags") or []):
            before = tuple(item.get("internal_tags") or [])
            apply_legacy_item_metadata(
                item,
                source=item.get("legacy_source") or SOURCE_CRAFT_CHAIN,
                parent_set=item.get("parent_set"),
            )
            if tuple(item.get("internal_tags") or []) != before:
                changed = True
        elif item.get("from_armor_set") and item.get("parent_set"):
            parent = item["parent_set"]
            if parent_legacy.get(parent):
                before = tuple(item.get("internal_tags") or [])
                inherit_legacy_item_from_parent(item, items.get(parent) or {}, parent_set=parent)
                if tuple(item.get("internal_tags") or []) != before:
                    changed = True
        if changed:
            updated += 1
    return updated
