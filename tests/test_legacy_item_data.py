import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legacy_item_data import (  # noqa: E402
    _parse_list_variant_item,
    _resolve_legacy_item,
    build_legacy_item_seed_titles,
    collect_legacy_boss_drop_titles,
    expand_legacy_item_titles,
    ingest_legacy_items_local,
    load_old_gen_item_titles_from_navbox,
)
from category_data import load_items_for_plugin  # noqa: E402
from prepare_data import _WIKI_MIRROR_DIR  # noqa: E402


def _read_page(title: str) -> str:
    path = Path(_WIKI_MIRROR_DIR) / "wiki" / "zh" / "pages" / f"{title}.html"
    return path.read_text(encoding="utf-8")


def test_navbox_collects_old_gen_items():
    titles = load_old_gen_item_titles_from_navbox()
    assert len(titles) >= 60
    assert "龙盔甲" in titles
    assert "枯萎之魂" in titles


def test_build_legacy_item_seed_titles_maps_sources():
    seeds = build_legacy_item_seed_titles()
    assert isinstance(seeds, dict)
    assert seeds.get("龙盔甲") == "navbox"
    assert seeds.get("枯萎之魂") == "boss_drop"


def test_parse_variant_trophy_and_music_box():
    ocram = _parse_list_variant_item(_read_page("奥库瑞姆纪念章"), "奥库瑞姆纪念章")
    assert ocram is not None
    assert ocram["name"] == "奥库瑞姆纪念章"
    assert "Ocram_Trophy" in ocram["image"]

    music = _parse_list_variant_item(_read_page("八音盒（Boss 1）"), "八音盒（Boss 1）")
    assert music is not None
    assert music["name"] == "八音盒（Boss 1）"
    assert "Music_Box" in music["image"]


def test_resolve_legacy_item_uses_specific_name():
    item = _resolve_legacy_item(_read_page("枯萎之魂"), "枯萎之魂")
    assert item is not None
    assert item["name"] == "枯萎之魂"


def test_ingest_legacy_items_adds_boss_drops_and_craft_chain():
    items = load_items_for_plugin()
    before = len(items)
    result = ingest_legacy_items_local(items, download_images=False)
    assert result["new_count"] >= 0
    assert len(items) >= before

    seeds = build_legacy_item_seed_titles(items)
    expanded = expand_legacy_item_titles(seeds, items)

    for name in ("枯萎之魂", "龙盔甲", "Tizona剑", "蛋炮"):
        assert name in items, f"missing item: {name}"
        assert items[name].get("internal_tags")
        assert items[name].get("legacy_scope") == "old_gen"

    drops = collect_legacy_boss_drop_titles()
    missing_drops = [d for d in drops if d not in items]
    assert "奥库瑞姆纪念章" not in missing_drops

    if "Tizona剑" in expanded:
        assert "Tizona剑" in items
