import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_data import (  # noqa: E402
    build_bosses_from_mirror,
    load_boss_catalog_from_overview,
    parse_boss_page_file,
)


def test_load_boss_catalog_has_pre_hardmode_bosses():
    catalog = load_boss_catalog_from_overview()
    assert len(catalog) >= 30
    titles = {entry["wiki_title"] for entry in catalog}
    assert "克苏鲁之眼" in titles
    assert "月亮领主" in titles


def test_parse_moon_lord_boss():
    parsed = parse_boss_page_file("月亮领主")
    assert parsed is not None
    assert parsed["name"] == "月亮领主"
    assert parsed["page_type"] == "boss"
    assert parsed.get("stats")
    stat_labels = {row["label"] for row in parsed["stats"]}
    assert "免疫" not in stat_labels
    assert "最大生命值" in stat_labels
    assert len(parsed.get("parts") or []) == 3
    part_names = {part["name"] for part in parsed["parts"]}
    assert "月亮领主" in part_names
    assert "月亮领主手" in part_names
    assert "月亮领主心脏" in part_names
    drops = parsed.get("drops") or {}
    items = drops.get("items") or {}
    assert len(items.get("normal") or []) >= 10
    assert len(items.get("expert") or []) >= 10
    assert len(items.get("master") or []) >= 10


def test_parse_eye_of_cthulhu_boss():
    parsed = parse_boss_page_file("克苏鲁之眼")
    assert parsed is not None
    assert parsed["name"] == "克苏鲁之眼"
    assert parsed.get("stats")
    assert not parsed.get("parts")
    hp = next(row for row in parsed["stats"] if row["label"] == "最大生命值")
    assert hp["modes"].get("normal")
    assert hp["modes"].get("expert")
    assert hp["modes"].get("master")


def test_build_bosses_from_mirror():
    bosses = build_bosses_from_mirror()
    assert len(bosses) >= 30
    assert "月亮领主" in bosses
    assert "克苏鲁之眼" in bosses
