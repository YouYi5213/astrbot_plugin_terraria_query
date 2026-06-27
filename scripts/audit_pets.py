"""Audit pet summon items against 宠物.html wiki tables."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prepare_data import load_pet_overview_catalog  # noqa: E402

PETS_JSON = ROOT / "data" / "terraria_query" / "pets.json"
ITEMS_JSON = ROOT / "data" / "terraria_query" / "items.json"


def searchable_names(item: dict, key: str) -> set[str]:
    names = {key, item.get("name", ""), item.get("wiki_title", ""), item.get("en_name", "")}
    names.update(item.get("search_terms") or [])
    names.update(item.get("aliases") or [])
    buff = item.get("buff") or {}
    pet = item.get("pet") or {}
    names.add(buff.get("name", ""))
    names.add(pet.get("name", ""))
    return {n for n in names if n}


def main() -> int:
    catalog = load_pet_overview_catalog()
    wiki_items = sorted(catalog.keys())

    if not PETS_JSON.is_file():
        print(f"Missing {PETS_JSON}")
        return 1

    pets_data = json.loads(PETS_JSON.read_text(encoding="utf-8"))
    items_data = json.loads(ITEMS_JSON.read_text(encoding="utf-8")) if ITEMS_JSON.is_file() else {}

    print(f"Wiki catalog items: {len(wiki_items)}")
    print(f"pets.json entries: {len(pets_data)}")

    missing = [x for x in wiki_items if x not in pets_data and x not in items_data]
    in_items_only = [x for x in wiki_items if x in items_data and x not in pets_data]
    in_pets = [x for x in wiki_items if x in pets_data]
    extra_pets = [x for x in pets_data if x not in wiki_items]

    print(f"In pets.json: {len(in_pets)}")
    print(f"In items.json only: {in_items_only}")
    print(f"Missing entirely: {missing}")
    print(f"Extra in pets.json: {extra_pets}")
    print()

    alias_gaps: list[tuple[str, str, str]] = []
    for item_name, meta in catalog.items():
        item = pets_data.get(item_name) or items_data.get(item_name)
        if not item:
            alias_gaps.append((meta.get("pet_display", ""), item_name, "NOT FOUND"))
            continue
        names = searchable_names(item, item_name)
        pet_display = meta.get("pet_display", "")
        if pet_display and pet_display not in names:
            buff = (item.get("buff") or {}).get("name", "")
            pet = (item.get("pet") or {}).get("name", "")
            alias_gaps.append((pet_display, item_name, f"buff={buff!r} pet={pet!r}"))

    print(f"Pet display name not searchable: {len(alias_gaps)}")
    for row in alias_gaps:
        print(f"  {row[0]!r} -> item {row[1]!r}: {row[2]}")

    incomplete = []
    for item_name in in_pets:
        item = pets_data[item_name]
        if not item.get("buff") or not item.get("pet"):
            incomplete.append(
                (item_name, bool(item.get("buff")), bool(item.get("pet")))
            )
    if incomplete:
        print()
        print("Incomplete pet cards (missing buff/pet section):")
        for row in incomplete:
            print(f"  {row[0]}: buff={row[1]} pet={row[2]}")

    out = ROOT / "scripts" / "audit_pets_result.json"
    out.write_text(
        json.dumps(
            {
                "wiki_items": wiki_items,
                "missing": missing,
                "in_items_only": in_items_only,
                "extra_pets": extra_pets,
                "alias_gaps": [
                    {"pet": p, "item": i, "detail": d} for p, i, d in alias_gaps
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
