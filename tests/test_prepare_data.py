import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prepare_data import (  # noqa: E402
    _description_is_tooltip_only,
    _description_missing_intro_list,
    _description_needs_coin_refresh,
    _description_needs_zh_refresh,
    _is_set_item,
    _merge_set_pieces,
    _normalize_image_filename,
    _parse_description_paragraph_rich,
    load_mount_overview_catalog,
    load_pet_overview_catalog,
    migrate_item_image_filenames,
    parse_description_from_soup,
    parse_item_page,
    resync_set_piece_locales,
    resolve_local_item_image,
    resolve_local_entity_image,
    normalize_drop_images_in_items,
    strip_english_fields,
)
from bs4 import BeautifulSoup  # noqa: E402


def test_normalize_image_filename_strips_px_prefix():
    assert _normalize_image_filename("17px-Titanium_Mask.png") == "Titanium_Mask.png"
    assert _normalize_image_filename("Amethyst_Staff.png") == "Amethyst_Staff.png"


def test_resolve_local_item_image_falls_back_to_item_image():
    items = {
        "钴头盔": {"name": "钴头盔", "image": "Cobalt_Helmet.png"},
    }
    assert (
        resolve_local_item_image("钴头盔", items, "Missing_Old_Variant_(old).png")
        == "Cobalt_Helmet.png"
    )


def test_resolve_local_entity_image_falls_back_to_boss_and_png():
    bosses = {"奥库瑞姆": {"name": "奥库瑞姆", "image": "Ocram_(Phase_1).gif"}}
    assert (
        resolve_local_entity_image("奥库瑞姆", "Ocram.png", bosses=bosses)
        == "Ocram_(Phase_1).gif"
    )
    items = {"脑子": {"name": "脑子", "image": "Brain.png"}}
    # The_Groom.png must exist in images/ for this test
    groom_png = ROOT / "data" / "terraria_query" / "images" / "The_Groom.png"
    if groom_png.is_file():
        assert (
            resolve_local_entity_image("僵尸新郎", "The_Groom.gif", items=items)
            == "The_Groom.png"
        )


def test_normalize_drop_images_in_items_updates_ocram_entry():
    bosses = {"奥库瑞姆": {"name": "奥库瑞姆", "image": "Ocram_(Phase_1).gif"}}
    items = {
        "枯萎之魂": {
            "drops": {
                "modes": [
                    {
                        "mode": "normal",
                        "label": "经典",
                        "entries": [
                            {
                                "name": "奥库瑞姆",
                                "image": "Ocram.png",
                                "quantity": "15–25",
                                "chance": "100%",
                            }
                        ],
                    }
                ]
            }
        }
    }
    groom_png = ROOT / "data" / "terraria_query" / "images" / "Ocram_(Phase_1).gif"
    if not groom_png.is_file():
        return
    count = normalize_drop_images_in_items(items, bosses=bosses)
    assert count == 1
    entry = items["枯萎之魂"]["drops"]["modes"][0]["entries"][0]
    assert entry["image"] == "Ocram_(Phase_1).gif"


def test_is_set_item_detects_armor_and_vanity():
    assert _is_set_item({"stats": [{"label": "类型", "value": "盔甲套装"}]})
    assert _is_set_item({"stats": [{"label": "Type", "value": "Vanity set"}]})
    assert _is_set_item({"stats": [{"label": "Type", "value": "ArmorSet"}]})
    assert _is_set_item({"stats": [{"label": "Type", "value": "VanitySet"}]})
    assert _is_set_item({"stats": [{"label": "类型", "value": "时装套装"}]})
    assert not _is_set_item({"stats": [{"label": "类型", "value": "武器"}]})


def test_merge_set_pieces_creates_armor_piece():
    items: dict = {}
    set_item = {
        "set_pieces": [
            {"name": "钛金面具", "image": "Titanium_Mask.png", "stats": [], "recipe": None},
        ],
    }
    count = _merge_set_pieces(items, "钛金盔甲", set_item)
    assert count == 1
    assert items["钛金面具"]["name"] == "钛金面具"
    assert "en_name" not in items["钛金面具"]


def test_resync_set_piece_locales():
    items = {
        "钛金盔甲": {
            "page_type": "armor_set",
            "set_pieces": [
                {"name": "寒霜头盔", "image": "Frost_Helmet.png", "stats": [], "recipe": None},
            ],
        }
    }
    updated = resync_set_piece_locales(items)
    assert updated == 1
    assert items["寒霜头盔"]["name"] == "寒霜头盔"
    assert "en_name" not in items["寒霜头盔"]


def test_strip_english_fields():
    items = {
        "环境改造枪": {
            "name": "环境改造枪",
            "en": {"name": "Clentaminator", "stats": []},
            "en_name": "Clentaminator",
            "aliases": ["Wings"],
        }
    }
    assert strip_english_fields(items) == 1
    assert "en" not in items["环境改造枪"]
    assert "en_name" not in items["环境改造枪"]
    assert "aliases" not in items["环境改造枪"]


def test_migrate_item_image_filenames():
    items = {
        "测试": {
            "image": "30px-Amethyst_Staff.png",
            "stats": [
                {
                    "label": "防御",
                    "value": "10",
                    "value_image": "17px-Titanium_Mask.png",
                }
            ],
        }
    }
    assert migrate_item_image_filenames(items) == 2
    assert items["测试"]["image"] == "Amethyst_Staff.png"
    assert items["测试"]["stats"][0]["value_image"] == "Titanium_Mask.png"


def test_parse_description_coin_segment():
    html = (
        '<p>可以从松露人处以<span class="coin">'
        '<span class="pc">1<i> PC</i></span></span>购买它。</p>'
    )
    p = BeautifulSoup(html, "lxml").p
    segments = _parse_description_paragraph_rich(p)
    assert any(s.get("type") == "coin" and s.get("coin_type") == "pc" for s in segments)


def test_description_needs_coin_refresh():
    item = {
        "description": "可以从松露人处以1购买它。",
        "description_rich": [[{"type": "text", "text": "可以从松露人处以1购买它。"}]],
    }
    assert _description_needs_coin_refresh(item)
    item["description_rich"] = [[{"type": "coin", "amount": "1", "coin_type": "pc"}]]
    assert not _description_needs_coin_refresh(item)


def test_description_needs_zh_refresh_detects_tooltip_only():
    item = {
        "name": "环境改造枪",
        "description": "喷射时生成和摧毁生物群系使用彩色溶液",
        "stats": [{"label": "工具提示", "value": "喷射时生成和摧毁生物群系使用彩色溶液"}],
    }
    assert _description_is_tooltip_only(item)
    assert _description_needs_zh_refresh(item)


def test_description_needs_zh_refresh_detects_english_on_chinese_item():
    item = {
        "name": "环境改造枪",
        "description": "The Clentaminator is a Hardmode tool.",
    }
    assert _description_needs_zh_refresh(item)
    item["description"] = "环境改造枪是一种困难模式工具。"
    assert not _description_needs_zh_refresh(item)


def test_items_data_is_valid():
    categories_dir = ROOT / "data" / "terraria_query" / "categories"
    manifest = categories_dir / "manifest.json"
    assert manifest.is_file(), "categories/manifest.json 不存在"
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("total", 0) > 1000


def test_assign_items_to_categories_priority():
    from category_data import assign_items_to_categories

    items = {
        "铜币": {"name": "铜币", "wiki_title": "铜币"},
        "天顶剑": {"name": "天顶剑", "wiki_title": "天顶剑"},
        "天使翅膀": {"name": "天使翅膀", "page_type": "wing", "from_wings_table": True},
    }
    title_to_keys = {
        "铜币": frozenset({"coins", "misc"}),
        "天顶剑": frozenset({"weapons", "misc"}),
    }
    buckets = assign_items_to_categories(items, title_to_keys)
    assert "铜币" in buckets["coins"]
    assert "天顶剑" in buckets["weapons"]
    assert "天使翅膀" in buckets["wings"]


def test_parse_frozen_shield_multiline_tooltip():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "冰冻护盾.html"
    if not html_path.is_file():
        return
    item = parse_item_page(html_path.read_text(encoding="utf-8"), "冰冻护盾")
    assert item is not None
    tooltip = next(s for s in item["stats"] if s["label"] == "工具提示")
    assert "\n" in tooltip["value"]
    assert tooltip["value"].splitlines() == [
        "对击退免疫",
        "生命值低于50%时，在所有者周围放置可减少25%伤害的护罩",
        "当生命值高于25%时，吸收团队中其他玩家所受伤害的25%",
    ]
    assert tooltip.get("segments")


def test_parse_mount_page_shrimpy_truffle():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "虾松露.html"
    if not html_path.is_file():
        return
    html = html_path.read_text(encoding="utf-8")
    item = parse_item_page(html, "虾松露")
    assert item is not None
    assert item["name"] == "虾松露"
    assert item.get("page_type") == "mount"
    assert item["buff"]["name"] == "可爱猪龙鱼坐骑"
    assert item["buff"]["tooltip"] == "不要让它爬行。"
    assert item["mount"]["name"] == "可爱猪龙鱼坐骑"
    assert item["mount"]["image"] == "Cute_Fishron_Mount.gif"
    stat_labels = {s["label"] for s in item["stats"]}
    assert "类型" in stat_labels
    assert not any(s.get("label") == "使用" for s in item["stats"])


def test_mount_overview_catalog_has_37_items():
    catalog = load_mount_overview_catalog()
    assert len(catalog) == 37
    assert "虾松露" in catalog
    assert catalog["粘鞍"]["mount_display"] == "史莱姆"


def test_parse_mount_variant_dusty_saddle():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "蒙尘牛皮鞍.html"
    if not html_path.is_file():
        return
    item = parse_item_page(html_path.read_text(encoding="utf-8"), "蒙尘牛皮鞍")
    assert item is not None
    assert item["name"] == "蒙尘牛皮鞍"
    assert item["buff"]["name"] == "花马坐骑"
    assert item["mount"]["image"] == "Painted_Horse_Mount.png"


def test_parse_mount_roller_skates_blue():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "蓝轮滑鞋.html"
    if not html_path.is_file():
        return
    item = parse_item_page(html_path.read_text(encoding="utf-8"), "蓝轮滑鞋")
    assert item is not None
    assert item["name"] == "蓝轮滑鞋"
    assert item["buff"]["name"] == "蓝轮滑鞋"
    assert "Blue_Roller_Skates" in item["mount"]["image"]


def test_parse_pet_page_mosquito_amber():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "蚊子琥珀.html"
    if not html_path.is_file():
        return
    item = parse_item_page(html_path.read_text(encoding="utf-8"), "蚊子琥珀")
    assert item is not None
    assert item["name"] == "蚊子琥珀"
    assert item.get("page_type") == "pet"
    assert item["buff"]["name"] == "恐龙宝宝"
    assert item["pet"]["name"] == "恐龙宝宝"


def test_pet_overview_catalog_has_items():
    catalog = load_pet_overview_catalog()
    assert len(catalog) >= 80
    assert "蚊子琥珀" in catalog
    assert catalog["蚊子琥珀"]["pet_display"] == "恐龙宝宝"
    assert catalog["鱼"]["wiki_page"] == "鱼（物品）"
    assert catalog["暗影珠"]["wiki_page"] == "暗影珠（物品）"
    assert "碎岩龟" in catalog
    assert catalog["碎岩龟"]["pet_display"] == "碎岩龟"
    assert "Digtoise" in catalog["碎岩龟"]["pet_image"]


def test_parse_wings_source_from_overview_table():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "翅膀.html"
    if not html_path.is_file():
        return
    from prepare_data import parse_wings_from_soup  # noqa: E402

    wings = parse_wings_from_soup(
        BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    )
    angel = wings["天使之翼"]
    assert angel["recipe"] is not None
    assert len(angel["recipe"]["ingredients"]) == 3
    assert "source" not in angel
    assert "光明之魂" in angel["description"]

    fledgling = wings["雏翼"]
    assert fledgling.get("recipe") is None
    assert "旅行模式" in fledgling["source"]
    assert fledgling["source_rich"]

    fin = wings["鳍翼"]
    assert fin.get("recipe") is None
    assert "渔夫" in fin["source"]


def test_description_missing_intro_list_detects_truncated_accessory():
    item = {
        "name": "月光护身符",
        "description": "……\n\n狼人增益会为玩家提供如下奖励：",
    }
    assert _description_missing_intro_list(item)
    assert _description_needs_zh_refresh(item)


def test_parse_description_includes_intro_effect_list():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "月光护身符.html"
    if not html_path.is_file():
        return
    from prepare_data import parse_description_from_soup  # noqa: E402

    parsed = parse_description_from_soup(
        BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    )
    assert parsed is not None
    assert "近战暴击" in parsed["text"]
    assert "生命再生" in parsed["text"]
    assert "· +2%" in parsed["text"]
    assert len(parsed["rich"]) >= 3


def test_parse_description_fire_gauntlet_split_list():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "烈火手套.html"
    if not html_path.is_file():
        return
    from prepare_data import parse_description_from_soup  # noqa: E402

    parsed = parse_description_from_soup(
        BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    )
    assert parsed is not None
    assert "有以下强化" in parsed["text"]
    assert "狱炎" in parsed["text"]
    assert "自动挥舞" in parsed["text"]
    assert parsed["text"].count("· ") >= 7

    item = {
        "name": "烈火手套",
        "description": "烈火手套是一个困难模式配饰，在击败所有三个机械 Boss后可用。它对近战武器有以下强化：",
    }
    assert _description_missing_intro_list(item)


def test_strip_wiki_footnote_markers_from_description():
    html_path = ROOT.parent / "terraria_data" / "wiki" / "zh" / "pages" / "碎岩龟.html"
    if not html_path.is_file():
        return
    parsed = parse_description_from_soup(
        BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    )
    assert parsed is not None
    assert "[1]" not in parsed["text"]
    assert "[2]" not in parsed["text"]
    assert "[3]" not in parsed["text"]

    from prepare_data import strip_wiki_footnote_markers_from_items  # noqa: E402

    items = {
        "示例": {
            "description": "测试描述。[1]继续。[2]",
            "stats": [{"label": "工具提示", "value": "召唤宠物[3]"}],
        }
    }
    assert strip_wiki_footnote_markers_from_items(items) == 1
    assert items["示例"]["description"] == "测试描述。继续。"
    assert items["示例"]["stats"][0]["value"] == "召唤宠物"
