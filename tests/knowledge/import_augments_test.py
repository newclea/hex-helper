#!/usr/bin/env python3.11
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    if sys.version_info[:2] != (3, 11):
        print(f"[FAIL] CPython 3.11 required, got {sys.version.split()[0]}")
        return 1

    root = Path(__file__).resolve().parents[2]
    source_root = root / "outputs/tmp/game_assets"
    script = root / "scripts/import_augments.py"
    checked_in = root / "data/knowledge/augments.zh-CN.json"
    worker = root / "outputs/tmp/vision_worker"
    worker.mkdir(parents=True, exist_ok=True)
    zh_augments = source_root / "extract_lcu_zh/plugins/rcp-be-lol-game-data/global/zh_cn/v1/cherry-augments.json"
    default_augments = source_root / "extract_lcu_default2/plugins/rcp-be-lol-game-data/global/default/v1/cherry-augments.json"
    zh_lists = source_root / "extract_lcu_zh/plugins/rcp-be-lol-game-data/global/zh_cn/v1/augment-lists.json"
    default_lists = source_root / "extract_lcu_default2/plugins/rcp-be-lol-game-data/global/default/v1/augment-lists.json"
    base_args = [
        sys.executable,
        "-B",
        str(script),
        "--zh-augments",
        str(zh_augments),
        "--default-augments",
        str(default_augments),
        "--zh-lists",
        str(zh_lists),
        "--default-lists",
        str(default_lists),
    ]

    with tempfile.TemporaryDirectory(prefix="import-test-", dir=worker) as temp:
        first = Path(temp) / "first.json"
        second = Path(temp) / "second.json"
        for output in (first, second):
            completed = subprocess.run(
                base_args
                + ["--output", str(output), "--check-against", str(checked_in)],
                cwd=root,
                check=False,
                text=True,
                capture_output=True,
            )
            if completed.returncode != 0:
                print(completed.stdout)
                print(completed.stderr)
                return completed.returncode
        if first.read_bytes() != second.read_bytes() or first.read_bytes() != checked_in.read_bytes():
            print("[FAIL] deterministic byte comparison failed")
            return 1

        catalog = json.loads(first.read_text(encoding="utf-8"))
        counts = {
            mode: sum(mode in record["modes"] for record in catalog["augments"])
            for mode in ("CHERRY", "KIWI", "KIWI_JADE")
        }
        if len(catalog["augments"]) != 655 or counts != {
            "CHERRY": 44,
            "KIWI": 220,
            "KIWI_JADE": 188,
        }:
            print(f"[FAIL] catalog counts differ: records={len(catalog['augments'])} modes={counts}")
            return 1

        zh_rows = json.loads(zh_augments.read_text(encoding="utf-8"))
        default_rows = json.loads(default_augments.read_text(encoding="utf-8"))
        bad_inputs: list[tuple[str, str, list[dict[str, object]]]] = []

        missing_default_id = list(default_rows)
        missing_default_id.pop()
        bad_inputs.append(("id-difference", "--default-augments", missing_default_id))

        duplicate_technical = [dict(row) for row in zh_rows]
        duplicate_technical.append(dict(duplicate_technical[0]))
        bad_inputs.append(("duplicate-technical", "--zh-augments", duplicate_technical))

        empty_name = [dict(row) for row in zh_rows]
        empty_name[0]["nameTRA"] = ""
        bad_inputs.append(("empty-name", "--zh-augments", empty_name))

        empty_icon = [dict(row) for row in zh_rows]
        empty_icon[0]["augmentSmallIconPath"] = ""
        bad_inputs.append(("empty-icon", "--zh-augments", empty_icon))

        for name, option, records in bad_inputs:
            bad_source = Path(temp) / f"{name}.json"
            bad_source.write_text(
                json.dumps(records, ensure_ascii=False), encoding="utf-8"
            )
            command = list(base_args)
            command[command.index(option) + 1] = str(bad_source)
            completed = subprocess.run(
                command + ["--output", str(Path(temp) / f"{name}-output.json")],
                cwd=root,
                check=False,
                text=True,
                capture_output=True,
            )
            if completed.returncode == 0:
                print(f"[FAIL] malformed source was accepted: {name}")
                return 1

    print("import determinism/validation checks=7 failures=0 records=655 modes=44/220/188")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
