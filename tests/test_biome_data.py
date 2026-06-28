import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biome_data import (  # noqa: E402
    BIOME_SEARCH_ALIASES,
    build_biomes_from_mirror,
    load_biome_catalog_from_homepage,
    parse_biome_page_file,
)


def test_load_biome_catalog_has_21_entries():
    catalog = load_biome_catalog_from_homepage()
    assert len(catalog) == 21
    titles = {entry["wiki_title"] for entry in catalog}
    assert "森林" in titles
    assert "地牢" in titles


def test_parse_forest_biome_page():
    parsed = parse_biome_page_file("森林")
    assert parsed is not None
    assert parsed["name"] == "森林"
    assert parsed["image"] == "BiomeBannerForest.png"
    assert parsed["description"]
    assert parsed["page_type"] == "biome"
    assert "森林" in parsed["description"]


def test_underground_layer_alias():
    parsed = parse_biome_page_file("地下")
    assert parsed is not None
    assert "地下层" in parsed.get("search_terms", [])


def test_build_biomes_from_mirror():
    biomes = build_biomes_from_mirror()
    assert len(biomes) == 21
    for wiki_title in BIOME_SEARCH_ALIASES:
        key = next(
            (k for k, v in biomes.items() if v.get("wiki_title") == wiki_title),
            None,
        )
        assert key is not None, wiki_title
