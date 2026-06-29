import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legacy_metadata import (  # noqa: E402
    INTERNAL_METADATA_FIELDS,
    SOURCE_BOSS_DROP,
    SOURCE_NAVBOX,
    TAG_LEGACY,
    TAG_LEGACY_BOSS,
    TAG_LEGACY_ITEM,
    apply_legacy_boss_metadata,
    apply_legacy_item_metadata,
    backfill_internal_tags_on_bosses,
    backfill_internal_tags_on_items,
    boss_uses_single_mode,
    inherit_legacy_item_from_parent,
    is_legacy_boss,
    is_legacy_item,
    strip_internal_metadata_for_card,
)
from boss_data import load_bosses_for_plugin  # noqa: E402


def test_apply_legacy_boss_metadata_tags():
    boss = {"name": "奥库瑞姆", "page_type": "boss", "parts": [{"name": "头"}]}
    apply_legacy_boss_metadata(boss, "奥库瑞姆")
    assert boss["legacy_boss"] is True
    assert boss["legacy_scope"] == "old_gen"
    assert TAG_LEGACY in boss["internal_tags"]
    assert TAG_LEGACY_BOSS in boss["internal_tags"]
    assert boss["parts"][0].get("legacy_part") is True
    assert is_legacy_boss(boss)
    assert boss_uses_single_mode(boss)


def test_apply_legacy_item_metadata_and_strip_for_card():
    item = {"name": "枯萎之魂"}
    apply_legacy_item_metadata(item, source=SOURCE_BOSS_DROP)
    assert item["legacy_item"] is True
    assert item["legacy_source"] == SOURCE_BOSS_DROP
    assert TAG_LEGACY_ITEM in item["internal_tags"]
    display = strip_internal_metadata_for_card(item)
    assert "legacy_item" not in display
    assert "internal_tags" not in display
    assert display["name"] == "枯萎之魂"


def test_inherit_legacy_set_piece():
    parent = {"name": "龙盔甲", "legacy_item": True, "internal_tags": [TAG_LEGACY, TAG_LEGACY_ITEM]}
    apply_legacy_item_metadata(parent, source=SOURCE_NAVBOX)
    piece = {"name": "龙面具", "from_armor_set": True, "parent_set": "龙盔甲"}
    inherit_legacy_item_from_parent(piece, parent, parent_set="龙盔甲")
    assert piece["legacy_item"] is True
    assert piece["legacy_source"] == "armor_set_piece"
    assert "parent_set:龙盔甲" in piece["internal_tags"]


def test_backfill_on_loaded_bosses():
    bosses = {
        "奥库瑞姆": {
            "name": "奥库瑞姆",
            "legacy_boss": True,
            "internal_tags": [TAG_LEGACY, TAG_LEGACY_BOSS],
        }
    }
    backfill_internal_tags_on_bosses(bosses)
    ocram = bosses["奥库瑞姆"]
    assert ocram.get("internal_tags")
    assert TAG_LEGACY_BOSS in ocram["internal_tags"]


def test_internal_fields_not_in_card_strip_list_leak():
    for field in ("legacy_boss", "legacy_item", "internal_tags", "legacy_source"):
        assert field in INTERNAL_METADATA_FIELDS
