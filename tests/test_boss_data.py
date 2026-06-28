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


def test_parse_skeletron_boss_has_hand_part():
    parsed = parse_boss_page_file("骷髅王")
    assert parsed is not None
    assert parsed["name"] == "骷髅王"
    parts = parsed.get("parts") or []
    assert len(parts) == 1
    hand = parts[0]
    assert hand["name"] == "骷髅王之手"
    assert hand.get("image") == "Skeletron_Hand_(NPC).png"
    hand_hp = next(row for row in hand["stats"] if row["label"] == "最大生命值")
    assert hand_hp["modes"].get("normal") == "600"
    assert hand_hp["modes"].get("expert") == "1560"
    assert hand_hp["modes"].get("master") == "1989"
    hand_dmg = next(row for row in hand["stats"] if row["label"] == "伤害")
    assert hand_dmg["modes"].get("master") == "66"


def test_parse_skeletron_boss_debuffs():
    parsed = parse_boss_page_file("骷髅王")
    debuff = parsed.get("debuff") or {}
    assert debuff.get("name") == "流血"
    assert debuff.get("description") == "无法再生生命"
    assert debuff.get("image") == "Bleeding.png"
    assert (debuff.get("chance") or {}).get("expert") == "100%"
    hand = (parsed.get("parts") or [])[0]
    hand_debuff = hand.get("debuff") or {}
    assert hand_debuff.get("name") == "缓慢"
    assert hand_debuff.get("description") == "移动速度降低"
    assert hand_debuff.get("image") == "Slow.png"
    assert (hand_debuff.get("chance") or {}).get("expert") == "50%"


def test_boss_stat_values_not_duplicated():
    parsed = parse_boss_page_file("骷髅王")
    hp = next(row for row in parsed["stats"] if row["label"] == "最大生命值")
    assert hp["modes"]["normal"] == "4400"
    assert hp["modes"]["expert"] == "8800"
    assert hp["modes"]["master"] == "11220"
    dmg = next(row for row in parsed["stats"] if row["label"] == "伤害")
    assert dmg["modes"]["normal"].startswith("32")
    assert "9999" in dmg["modes"]["normal"]
    assert dmg["modes"]["expert"].startswith("70")
    assert "9999" in dmg["modes"]["expert"]
    assert dmg["modes"]["master"].startswith("106")
    assert "9999" in dmg["modes"]["master"]
    assert "106 106" not in dmg["modes"]["master"]

    moon = parse_boss_page_file("月亮领主")
    core = next(part for part in moon["parts"] if "心脏" in part["name"])
    core_hp = next(row for row in core["stats"] if row["label"] == "最大生命值")
    assert core_hp["modes"]["master"] == "95625"
    assert "95625 95625" not in core_hp["modes"]["master"]


def test_parse_moon_lord_boss_money():
    parsed = parse_boss_page_file("月亮领主")
    money = (parsed.get("drops") or {}).get("money") or {}
    normal = money.get("normal") or []
    assert len(normal) == 1
    assert normal[0]["type"] == "pc"
    assert normal[0]["amount"] == "1"
    expert = money.get("expert") or []
    assert len(expert) == 2
    assert expert[0] == {"type": "pc", "amount": "2", "image": "Platinum_Coin.png"}
    assert expert[1]["type"] == "gc"
    assert expert[1]["amount"] == "50"


def test_build_bosses_from_mirror():
    bosses = build_bosses_from_mirror()
    assert len(bosses) >= 30
    assert "月亮领主" in bosses
    assert "克苏鲁之眼" in bosses
