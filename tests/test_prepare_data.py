import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Optional out-of-repo Wiki mirror (sibling dev checkout ../terraria_data,
# overridable with TERRARIA_WIKI_MIRROR). Tests that need it must skip
# explicitly when it is absent instead of returning silently and passing.
_MIRROR_ROOT = Path(
    os.environ.get("TERRARIA_WIKI_MIRROR") or (ROOT.parent / "terraria_data")
)
MIRROR_PAGES = _MIRROR_ROOT / "wiki" / "zh" / "pages"


def _mirror_page(title: str) -> Path:
    """Page file inside the optional out-of-repo Wiki mirror."""
    return MIRROR_PAGES / f"{title}.html"

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
    classify_item_recipes,
    apply_item_recipes_from_tables,
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


def test_resolve_local_entity_image_falls_back_to_boss():
    bosses = {"奥库瑞姆": {"name": "奥库瑞姆", "image": "Ocram_(Phase_1).gif"}}
    # 必须用一个本地确定不存在的文件名才能走到 Boss 回退分支。原先用的
    # "Ocram.png" 已随 08-12 的数据同步入库，会被优先返回而绕过回退。
    assert (
        resolve_local_entity_image("奥库瑞姆", "Ocram_Missing_Variant.png", bosses=bosses)
        == "Ocram_(Phase_1).gif"
    )


def test_resolve_local_entity_image_swaps_gif_to_png():
    images = ROOT / "data" / "terraria_query" / "images"
    png_only = next(
        (
            p
            for p in sorted(images.glob("*.png"))
            if p.stem.isascii()
            and all(c.isalnum() or c == "_" for c in p.stem)
            and not p.with_suffix(".gif").is_file()
        ),
        None,
    )
    if png_only is None:
        pytest.skip("images/ 中没有「仅有 png、无同名 gif」的素材可供验证 gif→png 回退")
    assert (
        resolve_local_entity_image("不存在的实体", f"{png_only.stem}.gif")
        == png_only.name
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
                                # 本地不存在的占位名，才会触发按 Boss 注册表规范化
                                "image": "Ocram_Missing_Variant.png",
                                "quantity": "15–25",
                                "chance": "100%",
                            }
                        ],
                    }
                ]
            }
        }
    }
    boss_gif = ROOT / "data" / "terraria_query" / "images" / "Ocram_(Phase_1).gif"
    if not boss_gif.is_file():
        pytest.skip(f"缺少素材 {boss_gif.name}，无法验证掉落图规范化")
    count = normalize_drop_images_in_items(items, bosses=bosses)
    assert count == 1
    entry = items["枯萎之魂"]["drops"]["modes"][0]["entries"][0]
    assert entry["image"] == "Ocram_(Phase_1).gif"


def test_classify_item_recipes_splits_craft_and_used_in():
    recipes = [
        {
            "station": "砧",
            "ingredients": [{"name": "力量之魂", "image": "a.png", "amount": "5"}],
            "result": {"name": "光辉飞盘", "image": "b.png"},
        },
        {
            "station": "砧",
            "ingredients": [{"name": "力量之魂", "image": "a.png", "amount": "3"}],
            "result": {"name": "巨兽之刃", "image": "c.png"},
        },
        {
            "station": "砧",
            "ingredients": [{"name": "铁锭", "image": "d.png"}],
            "result": {"name": "力量之魂", "image": "a.png"},
        },
    ]
    craft, used_in = classify_item_recipes("力量之魂", recipes)
    assert craft and craft["result"]["name"] == "力量之魂"
    assert len(used_in) == 2
    assert {r["result"]["name"] for r in used_in} == {"光辉飞盘", "巨兽之刃"}


def test_apply_item_recipes_from_tables_sets_used_in():
    item = {"name": "力量之魂", "recipe": None}
    recipes = [
        {
            "station": "砧",
            "ingredients": [{"name": "力量之魂"}],
            "result": {"name": "光辉飞盘"},
        },
        {
            "station": "砧",
            "ingredients": [{"name": "力量之魂"}],
            "result": {"name": "巨兽之刃"},
        },
    ]
    apply_item_recipes_from_tables(item, recipes)
    assert item.get("recipe") is None
    assert len(item.get("used_in") or []) == 2


def test_recipe_needs_used_in_backfill_when_craft_recipe_exists():
    from prepare_data import _recipe_needs_used_in_backfill

    obsidian = {
        "name": "黑曜石",
        "stats": [{"label": "类型", "value": "矿石制作材料"}],
        "recipe": {
            "station": "徒手",
            "ingredients": [{"name": "黑曜石墙"}],
            "result": {"name": "黑曜石"},
        },
    }
    assert _recipe_needs_used_in_backfill(obsidian) is True
    obsidian["used_in"] = [{"result": {"name": "狱石锭"}}]
    assert _recipe_needs_used_in_backfill(obsidian) is False


def test_parse_recipe_table_inherits_rowspan_result():
    from prepare_data import parse_item_page, _wiki_mirror_page_path

    path = _wiki_mirror_page_path("力量之魂")
    html = open(path, encoding="utf-8").read()
    item = parse_item_page(html, "力量之魂")
    avenger_rows = [
        r
        for r in item.get("used_in") or []
        if any(ing.get("name") == "游侠徽章" for ing in r.get("ingredients") or [])
        or any(ing.get("name") == "巫士徽章" for ing in r.get("ingredients") or [])
    ]
    assert len(avenger_rows) == 2
    assert all((r.get("result") or {}).get("name") == "复仇者徽章" for r in avenger_rows)


def test_item_needs_used_in_mirror_refresh_when_result_empty():
    from prepare_data import _item_needs_used_in_mirror_refresh

    item = {
        "name": "白马掌气球",
        "used_in": [
            {
                "station": "工匠作坊",
                "ingredients": [{"name": "白马掌气球"}],
                "result": {"name": "", "image": ""},
            }
        ],
    }
    assert _item_needs_used_in_mirror_refresh(item) is True


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


def test_statue_items_are_claimed_before_furniture():
    """雕像只能靠名称识别，必须排在 furniture 之前，否则会被先截获。"""
    from category_data import assign_items_to_categories

    items = {
        "天使雕像": {"name": "天使雕像", "wiki_title": "天使雕像"},
        "木椅": {"name": "木椅", "wiki_title": "木椅"},
    }
    title_to_keys = {
        "天使雕像": frozenset({"furniture", "statues"}),
        "木椅": frozenset({"furniture"}),
    }
    buckets = assign_items_to_categories(items, title_to_keys)
    assert "天使雕像" in buckets["statues"]
    assert "天使雕像" not in buckets["furniture"]
    assert "木椅" in buckets["furniture"]


def test_refresh_armor_sets_preserves_plugin_managed_fields(monkeypatch):
    """刷新套装页不得丢掉其它模块维护的字段。

    `refresh_armor_sets` 原先用 `items[key] = parsed` 整体替换，会把
    legacy_metadata 写入的 internal_tags / legacy_scope 洗掉；实测一次增量
    同步静默剥掉 46 个套装部件的标记，撤销了 v1.8.4 的 legacy 修复。
    """
    import asyncio

    import prepare_data

    items = {
        "龙盔甲": {
            "name": "龙盔甲",
            "page_type": "armor_set",
            "wiki_title": "龙盔甲",
            "internal_tags": ["legacy", "old_gen"],
            "legacy_scope": "old_gen",
            "set_pieces": [],
        },
        "龙胸甲": {
            "name": "龙胸甲",
            "page_type": "armor_piece",
            "wiki_title": "龙胸甲",
            "internal_tags": ["legacy", "old_gen"],
            "legacy_scope": "old_gen",
        },
    }
    parsed = {
        "name": "龙盔甲",
        "stats": [{"label": "类型", "value": "盔甲套装"}],
        "set_pieces": [{"name": "龙胸甲", "stats": []}],
    }

    async def fake_fetch(session, title, api_url=None):  # noqa: ANN001
        return "<html></html>"

    monkeypatch.setattr(prepare_data, "fetch_page_html", fake_fetch)
    monkeypatch.setattr(
        prepare_data, "parse_item_page", lambda html, title: dict(parsed)
    )

    asyncio.run(prepare_data.refresh_armor_sets(object(), items))

    for name in ("龙盔甲", "龙胸甲"):
        assert items[name]["legacy_scope"] == "old_gen", name
        assert items[name]["internal_tags"] == ["legacy", "old_gen"], name


def test_parse_frozen_shield_multiline_tooltip():
    html_path = _mirror_page("冰冻护盾")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
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
    html_path = _mirror_page("虾松露")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
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
    html_path = _mirror_page("蒙尘牛皮鞍")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
    item = parse_item_page(html_path.read_text(encoding="utf-8"), "蒙尘牛皮鞍")
    assert item is not None
    assert item["name"] == "蒙尘牛皮鞍"
    assert item["buff"]["name"] == "花马坐骑"
    assert item["mount"]["image"] == "Painted_Horse_Mount.png"


def test_parse_mount_roller_skates_blue():
    html_path = _mirror_page("蓝轮滑鞋")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
    item = parse_item_page(html_path.read_text(encoding="utf-8"), "蓝轮滑鞋")
    assert item is not None
    assert item["name"] == "蓝轮滑鞋"
    assert item["buff"]["name"] == "蓝轮滑鞋"
    assert "Blue_Roller_Skates" in item["mount"]["image"]


def test_parse_pet_page_mosquito_amber():
    html_path = _mirror_page("蚊子琥珀")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
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
    html_path = _mirror_page("翅膀")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
    from prepare_data import parse_wings_from_soup  # noqa: E402

    wings = parse_wings_from_soup(
        BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    )
    angel = wings["天使之翼"]
    assert angel["recipe"] is not None
    assert len(angel["recipe"]["ingredients"]) == 3
    assert {i["name"] for i in angel["recipe"]["ingredients"]} == {
        "羽毛",
        "飞翔之魂",
        "光明之魂",
    }
    assert "source" not in angel
    # 「光明之魂」是总览表配方列（第 4 格）的内容，只应进 recipe；description
    # 仅来自备注列。此前的断言把它当成描述，因此在备注列为空时 KeyError。
    assert "光明之魂" not in angel.get("description", "")

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
    html_path = _mirror_page("月光护身符")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
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
    html_path = _mirror_page("烈火手套")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
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
    html_path = _mirror_page("碎岩龟")
    if not html_path.is_file():
        pytest.skip(f"optional Wiki mirror page missing: {html_path}")
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
