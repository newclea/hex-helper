#!/usr/bin/env python3
"""Copy the KIWI hexcore knowledge base and emit the overlay/C++ catalog."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


RARITY = {"白银": "kSilver", "黄金": "kGold", "棱彩": "kPrismatic"}
SOURCE = Path(
    r"D:\Arahat0\文档\WXWork\1688857308866615\Cache\File\2026-09\海克斯大乱斗知识库\海克斯大乱斗知识库\海克斯知识.json"
)
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "knowledge" / "kiwi_hexcores.json"
CATALOG = ROOT / "data" / "knowledge" / "kiwi_augments.zh-CN.json"


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing source knowledge base: {SOURCE}")
    RAW.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, RAW)
    payload = json.loads(RAW.read_text(encoding="utf-8"))
    rows = payload.get("augments")
    if not isinstance(rows, list) or not rows:
        raise SystemExit("knowledge base has no augments")
    catalog_rows = []
    for item in rows:
        name = str(item["name"]).strip()
        ident = str(item["internal_id"]).strip()
        rarity = RARITY[str(item["rarity"])]
        icon = str(item.get("icon") or "assets/ux/kiwi/augments/icons/none.tex")
        catalog_rows.append(
            {
                "id": ident,
                "numeric_id": int(item["platform_id"]),
                "display_name": name,
                "default_name": str(item.get("name_en") or name).strip(),
                "icon": icon,
                "rarity": rarity,
                "modes": ["KIWI"],
                "enabled": bool(item.get("enabled", True)),
                "effect": item.get("effect") or "",
                "effect_detail": item.get("effect_detail") or "",
            }
        )
    catalog_rows.sort(key=lambda row: row["id"])
    catalog = {
        "schema_version": 1,
        "catalog_version": "kiwi-hexcores-2026-09-12",
        "locale": "zh-CN",
        "augments": catalog_rows,
    }
    CATALOG.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {RAW} ({len(rows)})")
    print(f"wrote {CATALOG} ({len(catalog_rows)})")


if __name__ == "__main__":
    main()
