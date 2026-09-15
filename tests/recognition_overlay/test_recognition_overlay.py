from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
OVERLAY_DIR = WORKSPACE_ROOT / "scripts" / "recognition_overlay"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if str(OVERLAY_DIR) not in sys.path:
    sys.path.insert(0, str(OVERLAY_DIR))
hexcore_database = _load("overlay_hexcore_database", OVERLAY_DIR / "hexcore_database.py")
augment_catalog = _load("overlay_augment_catalog", OVERLAY_DIR / "augment_catalog.py")
history_store = _load("overlay_history_store", OVERLAY_DIR / "history_store.py")
view_model = _load("overlay_view_model", OVERLAY_DIR / "view_model.py")
vision_client = _load("overlay_vision_client", OVERLAY_DIR / "vision_client.py")
click_flag = _load("overlay_click_flag", OVERLAY_DIR / "click_flag.py")
hid_mouse = _load("overlay_hid_mouse", OVERLAY_DIR / "hid_mouse.py")
live_client = _load("overlay_live_client", OVERLAY_DIR / "live_client.py")
hexcore_gate = _load("overlay_hexcore_gate", OVERLAY_DIR / "hexcore_gate.py")
league_root = _load("overlay_league_root", OVERLAY_DIR / "league_root.py")


class MemoryStore:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, record: dict) -> dict:
        payload = dict(record)
        payload.setdefault("recorded_at_utc", "2026-09-08T00:00:00Z")
        self.records.append(payload)
        return payload


def _open_hexcore(model: view_model.RecognitionViewModel) -> None:
    model.phase = "InProgress"
    model.live_level = 3
    model.live_is_dead = True


def _model(store: MemoryStore | None = None) -> view_model.RecognitionViewModel:
    catalog = augment_catalog.AugmentCatalog(
        {
            "ARAM_ADAPt": "物理转魔法",
            "ARAM_AllForYou": "全心为你",
            "ARAM_ApexInventor": "尖端发明家",
        }
    )
    return view_model.RecognitionViewModel(
        catalog=catalog,
        store=store or MemoryStore(),
        match_id="test-match",
        champion_label=lambda champion_id: {78: "波比", 51: "凯特琳"}.get(
            champion_id, f"#{champion_id}"
        ),
        champion_alias=lambda name: {"Malzahar": "玛尔扎哈", "Jayce": "杰斯"}.get(
            name
        ),
    )


class AugmentCatalogTests(unittest.TestCase):
    def test_loads_chinese_display_names(self) -> None:
        path = WORKSPACE_ROOT / "data" / "knowledge" / "augments.zh-CN.json"
        catalog = augment_catalog.AugmentCatalog.load(path)
        self.assertEqual(catalog.label("ARAM_ADAPt"), "物理转魔法")
        database = catalog.database
        self.assertGreater(len(database), 100)
        self.assertIsNotNone(database.resolve(display_name="有始有终"))
        self.assertIsNotNone(database.resolve(display_name="急速之追求"))
        self.assertIsNotNone(database.resolve(display_name="心灵净化"))
        self.assertIsNone(database.resolve(raw_text="假海克斯乱码"))
        offer = database.complete_offer(
            [
                {"slot": "LEFT", "display_name": "有始有终"},
                {"slot": "CENTER", "raw_text": "急速之追求"},
                {
                    "slot": "RIGHT",
                    "augment_id": "ARAM_SpiritualPurification",
                    "display_name": "心灵净化",
                },
            ]
        )
        self.assertIsNotNone(offer)
        assert offer is not None
        self.assertEqual([card["name"] for card in offer], ["有始有终", "急速之追求", "心灵净化"])
        self.assertIsNone(
            database.complete_offer(
                [
                    {"slot": "LEFT", "raw_text": "假海克斯乱码"},
                    {"slot": "CENTER", "display_name": "急速之追求"},
                    {"slot": "RIGHT", "display_name": "心灵净化"},
                ]
            )
        )
        frost = database.resolve(raw_text="冰霜幽灵")
        self.assertIsNotNone(frost)
        assert frost is not None
        self.assertEqual(frost.name, "冰霜 幽灵")
        tagged = database.resolve(raw_text="伤害唯快不破")
        self.assertIsNotNone(tagged)
        assert tagged is not None
        self.assertEqual(tagged.name, "唯快不破")
        self.assertIsNone(database.resolve(raw_text="伤害"))
        self.assertIsNone(database.resolve(display_name="伤害"))
        self.assertIsNotNone(database.resolve(display_name="双生火焰"))
        self.assertIsNotNone(database.resolve(display_name="海洋龙魂"))
        senna = database.complete_offer(
            [
                {"slot": "LEFT", "raw_text": "伤害老练狙神"},
                {"slot": "CENTER", "raw_text": "伤害升级：无尽之刃"},
                {"slot": "RIGHT", "raw_text": "复原力坦克引擎"},
            ]
        )
        self.assertIsNotNone(senna)
        assert senna is not None
        self.assertEqual(
            [card["name"] for card in senna],
            ["老练狙神", "升级：无尽之刃", "坦克引擎"],
        )
        partial = database.align_offer(
            [
                {"slot": "LEFT", "raw_text": "假海克斯乱码"},
                {"slot": "CENTER", "raw_text": "急速之追求"},
            ]
        )
        self.assertEqual([card["name"] for card in partial], ["急速之追求"])
        self.assertEqual(partial[0]["slot"], "CENTER")
        messy = database.complete_offer(
            [
                {"slot": "LEFT", "raw_text": "老 练 狙 神"},
                {"slot": "CENTER", "raw_text": "升级无尽之刃"},
                {"slot": "RIGHT", "raw_text": "坦克引擎！"},
            ]
        )
        self.assertIsNotNone(messy)
        assert messy is not None
        self.assertEqual(
            [card["name"] for card in messy],
            ["老练狙神", "升级：无尽之刃", "坦克引擎"],
        )
        spaced_upgrade = database.resolve(raw_text="升级 无尽之刃")
        self.assertIsNotNone(spaced_upgrade)
        assert spaced_upgrade is not None
        self.assertEqual(spaced_upgrade.name, "升级：无尽之刃")
        self.assertEqual(database.resolve(raw_text="会心治").name, "会心治疗")
        self.assertEqual(database.resolve(raw_text="神圣干").name, "神圣干预")
        self.assertEqual(database.resolve(raw_text="狙神飞").name, "狙神飞星")
        self.assertEqual(
            database.resolve(raw_text="abc会心治疗!!!").name, "会心治疗"
        )
        self.assertEqual(
            database.resolve(raw_text="复原力会心治疗").name, "会心治疗"
        )
        self.assertIsNone(database.resolve(raw_text="复苏力"))
        self.assertIsNone(database.resolve(raw_text="圣毅力"))
        self.assertIsNone(database.resolve(raw_text="功能"))
        self.assertEqual(
            database.resolve(raw_text="物 理　转　魔 法").name, "物理转魔法"
        )
        self.assertEqual(
            database.resolve(raw_text="【伤害】物理转魔法！！").name, "物理转魔法"
        )
        self.assertEqual(database.resolve(raw_text="ＡＤＡＰｔ").name, "物理转魔法")
        self.assertEqual(database.resolve(raw_text="adapt").name, "物理转魔法")
        self.assertEqual(
            database.resolve(raw_text="ａｌｌ ｆｏｒ ｙｏｕ").name, "全心为你"
        )
        self.assertEqual(
            database.resolve(augment_id="aram_adapt").name, "物理转魔法"
        )
        self.assertEqual(
            database.resolve(raw_text="升级︰无尽之刃").name, "升级：无尽之刃"
        )

    def test_kiwi_knowledge_base_is_the_offer_library(self) -> None:
        path = WORKSPACE_ROOT / "data" / "knowledge" / "kiwi_augments.zh-CN.json"
        catalog = augment_catalog.AugmentCatalog.load(path)
        database = catalog.database
        self.assertEqual(len(database), 223)
        self.assertEqual(catalog.label("Quest_UltraHydra"), "终极九头蛇")
        self.assertEqual(catalog.label("Upgrade_DeathDance"), "升级：死亡之舞")
        self.assertEqual(catalog.label("Upgrade_Ravenous"), "升级：贪欲九头蛇")
        hydra = database.resolve(raw_text="终极九头蛇")
        self.assertIsNotNone(hydra)
        assert hydra is not None
        self.assertEqual(hydra.augment_id, "Quest_UltraHydra")
        self.assertEqual(database.resolve(raw_text="缩小引").name, "缩小引擎")
        self.assertEqual(database.resolve(raw_text="旋转至胜").name, "旋转至胜")
        self.assertEqual(database.resolve(raw_text="回力ok镖").name, "回力OK镖")
        self.assertEqual(database.resolve(raw_text="回力ＯＫ镖").name, "回力OK镖")
        self.assertEqual(database.resolve(raw_text="点亮他们").name, "点亮他们！")
        self.assertEqual(
            database.resolve(raw_text="伤害终极九头蛇!!!").name, "终极九头蛇"
        )
        offer = database.complete_offer(
            [
                {"slot": "LEFT", "raw_text": "伤害终极九头蛇"},
                {"slot": "CENTER", "raw_text": "升级 死亡之舞"},
                {"slot": "RIGHT", "raw_text": "复原力旋转至胜"},
            ]
        )
        self.assertIsNotNone(offer)
        assert offer is not None
        self.assertEqual(
            [card["name"] for card in offer],
            ["终极九头蛇", "升级：死亡之舞", "旋转至胜"],
        )


class HistoryStoreTests(unittest.TestCase):
    def test_appends_and_reads_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "selected_hexcores.jsonl"
            store = history_store.HistoryStore(path)
            store.append({"stage": 1, "name": "物理转魔法"})
            store.append({"stage": 2, "name": "全心为你"})
            recent = store.recent()
            self.assertEqual(len(recent), 2)
            self.assertEqual(recent[1]["name"], "全心为你")


class ViewModelTests(unittest.TestCase):
    def test_bench_and_champion_from_lcu(self) -> None:
        model = _model()
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {
                    "gameflowPhase": "ChampSelect",
                    "championId": 78,
                    "benchChampions": [
                        {"championId": 51, "name": "凯特琳"},
                        {"championId": 222, "name": "金克丝"},
                    ],
                },
            }
        )
        text = model.render()
        self.assertEqual(model.champion, "波比")
        self.assertIn("凯特琳", text)
        self.assertIn("金克丝", text)
        self.assertIn("待选席", text)

    def test_offer_and_selection_are_recorded(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_game_state(
            {
                "champion": "波比",
                "offer_round": 1,
                "current_offer": {
                    "recognitions": [
                        {
                            "state": "RECOGNIZED",
                            "slot": "LEFT",
                            "augment_id": "ARAM_ADAPt",
                            "display_name": "物理转魔法",
                        },
                        {
                            "state": "RECOGNIZED",
                            "slot": "CENTER",
                            "augment_id": "ARAM_AllForYou",
                            "display_name": "全心为你",
                        },
                        {
                            "state": "RECOGNIZED",
                            "slot": "RIGHT",
                            "augment_id": "ARAM_ApexInventor",
                            "display_name": "尖端发明家",
                        },
                    ]
                },
            }
        )
        self.assertEqual(len(model.offer), 3)
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "offer_stage": 1,
            }
        )
        self.assertEqual(model.selected[0]["name"], "物理转魔法")
        self.assertEqual(len(store.records), 1)
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "offer_stage": 1,
            }
        )
        self.assertEqual(len(store.records), 1)
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_AllForYou",
                "selected_slot": "CENTER",
                "source": "click_luma_flash",
            }
        )
        self.assertEqual(model.selected[1]["name"], "全心为你")
        self.assertIn("已自动记下", model.note)
        self.assertIn("全心为你", model.note)
        text = model.render()
        self.assertIn("已选海克斯", text)
        self.assertIn("物理转魔法", text)
        self.assertIn("当前海克斯", text)

    def test_click_glow_uses_offer_slot_not_engine_id(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.offer = [
            {"slot": "LEFT", "augment_id": "ARAM_FromBeginningToEnd", "name": "有始有终"},
            {"slot": "CENTER", "augment_id": "ARAM_SpecializedRecursion", "name": "急速之追求"},
            {"slot": "RIGHT", "augment_id": "ARAM_SpiritualPurification", "name": "心灵净化"},
        ]
        model.apply_selection_observed(
            {
                "selected_augment_id": "UNKNOWN",
                "selected_slot": "CENTER",
                "source": "click_luma_flash",
                "offer_stage": 1,
            }
        )
        self.assertEqual(model.selected[0]["name"], "急速之追求")
        self.assertEqual(model.selected[0]["stage"], 1)
        model.selected = []
        model._round_ocr_closed = False
        model.offer = [
            {"slot": "LEFT", "augment_id": "ARAM_FromBeginningToEnd", "name": "有始有终"},
            {"slot": "CENTER", "augment_id": "ARAM_SpecializedRecursion", "name": "急速之追求"},
            {"slot": "RIGHT", "augment_id": "ARAM_SpiritualPurification", "name": "心灵净化"},
        ]
        model.apply_selection_observed(
            {
                "selected_augment_id": "GlassCannon",
                "selected_slot": "RIGHT",
                "source": "click_luma_flash",
                "offer_stage": 1,
            }
        )
        self.assertEqual(model.selected[0]["name"], "心灵净化")
        self.assertEqual(model.selected[0]["slot"], "RIGHT")
        self.assertEqual(model.selected[0]["stage"], 1)
        self.assertIn("已自动记下", model.note)
        self.assertIn("心灵净化", model.note)

    def test_flash_luma_records_selected_offer_card(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.offer = [
            {"slot": "LEFT", "augment_id": "ARAM_HighRoller", "name": "掷骰狂人"},
            {"slot": "CENTER", "augment_id": "ARAM_EndlessHunt", "name": "吃过路兵"},
            {"slot": "RIGHT", "augment_id": "GlassCannon", "name": "玻璃大炮"},
        ]
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_HighRoller",
                "selected_slot": "LEFT",
                "source": "sole_remaining",
                "offer_stage": 3,
            }
        )
        self.assertEqual(model.selected[0]["name"], "掷骰狂人")
        self.assertEqual(model.selected[0]["slot"], "LEFT")
        self.assertEqual(model.selected[0]["stage"], 1)
        self.assertEqual(model.selected[0]["source"], "sole_remaining")
        self.assertIn("掷骰狂人", model.note)
        self.assertIn("左", model.click_banner)
        self.assertIn("已选 掷骰狂人", model.hexcore_change)
        self.assertEqual(model.confirmed_count(), 1)
        self.assertFalse(model.vision_allowed())
        self.assertTrue(model._round_ocr_closed)

    def test_flash_records_from_catalog_without_offer(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.offer = []
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "source": "flash_luma",
                "offer_stage": 1,
            }
        )
        self.assertEqual(model.selected[0]["name"], "物理转魔法")
        self.assertEqual(model.selected[0]["slot"], "LEFT")
        self.assertTrue(model._round_ocr_closed)

    def test_sole_remaining_uses_last_offer_when_current_offer_empty(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.last_offer = [
            {"slot": "LEFT", "augment_id": "ARAM_HighRoller", "name": "掷骰狂人"},
            {"slot": "CENTER", "augment_id": "ARAM_EndlessHunt", "name": "吃过路兵"},
            {"slot": "RIGHT", "augment_id": "GlassCannon", "name": "玻璃大炮"},
        ]
        model.offer = []
        model.apply_selection_observed(
            {
                "selected_augment_id": "UNKNOWN",
                "selected_slot": "LEFT",
                "source": "sole_remaining",
                "offer_stage": 1,
            }
        )
        self.assertEqual(model.selected[0]["name"], "掷骰狂人")
        self.assertTrue(model._round_ocr_closed)

    def test_sole_remaining_uses_payload_offer_ids(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.offer = []
        model.last_offer = []
        model.apply_selection_observed(
            {
                "selected_augment_id": "UNKNOWN",
                "selected_slot": "CENTER",
                "source": "sole_remaining",
                "offer_stage": 2,
                "offer_augment_ids": [
                    "ARAM_ADAPt",
                    "ARAM_AllForYou",
                    "ARAM_ApexInventor",
                ],
            }
        )
        self.assertEqual(model.selected[0]["slot"], "CENTER")
        self.assertEqual(model.selected[0]["name"], "全心为你")
        self.assertEqual(model.selected[0]["stage"], 1)
        self.assertTrue(model._round_ocr_closed)

    def test_hover_slot_is_ignored_for_offer(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.offer = [
            {"slot": "LEFT", "augment_id": "ARAM_FromBeginningToEnd", "name": "有始有终"},
            {"slot": "CENTER", "augment_id": "ARAM_SpecializedRecursion", "name": "急速之追求"},
            {"slot": "RIGHT", "augment_id": "ARAM_SpiritualPurification", "name": "心灵净化"},
        ]
        model.apply_frame_result(
            {
                "reason": "ocr_executed",
                "hover_slot": "CENTER",
                "recognition_debug": {"cards": []},
            }
        )
        self.assertIsNone(model.hover_slot)
        text = model.render()
        self.assertNotIn("发光", text)
        self.assertNotIn("悬停发光", text)

    def test_unknown_names_never_become_current_offer(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.apply_game_state(
            {
                "offer_round": 1,
                "current_offer": {
                    "recognitions": [
                        {
                            "state": "RECOGNIZED",
                            "slot": "LEFT",
                            "augment_id": "FAKE_ONE",
                            "display_name": "假海克斯乱码",
                        },
                        {
                            "state": "RECOGNIZED",
                            "slot": "CENTER",
                            "augment_id": "ARAM_AllForYou",
                            "display_name": "全心为你",
                        },
                        {
                            "state": "RECOGNIZED",
                            "slot": "RIGHT",
                            "augment_id": "ARAM_ApexInventor",
                            "display_name": "尖端发明家",
                        },
                    ]
                },
            }
        )
        self.assertEqual(model.offer, [])
        self.assertIn("OCR 左[假海克斯乱码]", model.note)
        self.assertIn("中[全心为你]", model.note)
        model.apply_selection_observed(
            {
                "selected_augment_id": "NOT_A_HEXCORE",
                "selected_slot": "LEFT",
                "source": "click_luma_flash",
            }
        )
        self.assertEqual(model.selected, [])

    def test_game_state_selected_augments_are_ignored(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_game_state(
            {
                "champion": "波比",
                "offer_round": 2,
                "current_offer": None,
                "selected_augments": [
                    {
                        "state": "RECOGNIZED",
                        "slot": "LEFT",
                        "augment_id": "ARAM_ADAPt",
                        "display_name": "物理转魔法",
                    }
                ],
            }
        )
        self.assertEqual(model.selected, [])
        self.assertEqual(len(store.records), 0)

    def test_left_click_ocr_rebuilds_offer_from_kiwi_library(self) -> None:
        path = WORKSPACE_ROOT / "data" / "knowledge" / "kiwi_augments.zh-CN.json"
        model = view_model.RecognitionViewModel(
            catalog=augment_catalog.AugmentCatalog.load(path),
            store=MemoryStore(),
            match_id="test-match",
        )
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "ocr_executed",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "伤害缩小引擎"},
                        {"slot": "CENTER", "raw_text": "旋转 至胜"},
                        {"slot": "RIGHT", "raw_text": "复原力万用瞄准镜"},
                    ]
                },
            }
        )
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["缩小引擎", "旋转至胜", "万用瞄准镜"],
        )
        self.assertIn("三选一", model.note)
        text = model.render()
        self.assertIn("左. 缩小引擎", text)
        self.assertIn("中. 旋转至胜", text)
        self.assertIn("右. 万用瞄准镜", text)

    def test_left_click_marks_reread_even_when_offer_unchanged(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.vision_status = "识别中"
        first = model.render()
        model.mark_left_click()
        self.assertEqual(model.click_seq, 1)
        self.assertIn("第 1 次重识", model.note)
        self.assertIn("left_click #1", model.render())
        self.assertTrue(model.render().splitlines()[1].startswith("▶ 左键 #1"))
        self.assertTrue(model.vision_allowed())
        self.assertNotEqual(first, model.render())
        model.apply_frame_result(
            {
                "reason": "low_confidence",
                "reread_offer": True,
                "frames": [{"width": 3840, "height": 2160}],
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "乱码甲"},
                        {"slot": "CENTER", "raw_text": "乱码乙"},
                        {"slot": "RIGHT", "raw_text": "乱码丙"},
                    ]
                },
            }
        )
        self.assertIn("第 1 次左键重识", model.note)
        self.assertIn("left_click #1", model.vision_detail)
        text = model.render()
        self.assertIn("3840x2160", text)
        self.assertIn("读取原文:", text)
        self.assertIn("「乱码甲」→ 未对齐", text)
        self.assertIn("「乱码乙」→ 未对齐", text)
        self.assertIn("「乱码丙」→ 未对齐", text)
        self.assertFalse(model.click_pending)
        self.assertIn("第 1 次左键重识", model.click_banner)

    def test_click_ack_and_status_do_not_hide_left_click(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.vision_status = "识别中"
        model.mark_left_click()
        model.apply_vision_status("识别中", "进局后每 0.2 秒自动整屏识字。")
        self.assertIn("正在整屏识字", model.note)
        model.apply_click_ack({"reason": "click_received"})
        self.assertIn("识别进程已接到", model.click_banner)
        model.apply_click_ack({"reason": "no_current_frame"})
        self.assertIn("还没抓到当前帧", model.click_banner)
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "left_click",
            }
        )
        self.assertIn("没有海克斯三选一", model.note)
        self.assertIn("没有海克斯三选一", model.click_banner)

    def test_interval_always_shows_each_frame(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.vision_status = "识别中"
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "interval",
            }
        )
        first = model.recognize_seq
        first_banner = model.click_banner
        self.assertEqual(first, 1)
        self.assertNotIn("最近识别", model.render())
        self.assertNotIn("识别 #1", model.render())
        self.assertIn("本帧没有海克斯三选一", model.note)
        model.apply_frame_result(
            {
                "reason": "duplicate_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        model.apply_frame_result(
            {
                "reason": "duplicate_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        self.assertEqual(model.recognize_seq, 3)
        self.assertIn("自动识别", model.click_banner)
        self.assertNotIn("识别 #3", model.click_banner)
        self.assertNotIn("最近识别", model.render())
        self.assertIn("本帧仍是同一组三选一", model.note)
        self.assertIn("海克斯未变化", model.hexcore_change)
        self.assertIn("海克斯变化:", model.render())
        self.assertNotEqual(first_banner, model.click_banner)

    def test_empty_ocr_shows_detector_reason(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "interval",
                "raw_detector": {"visible": False, "reason": "insufficient_luma"},
            }
        )
        self.assertIn("本帧没有海克斯三选一", model.note)
        self.assertIn("insufficient_luma", model.note)
        self.assertIn("检出: insufficient_luma", model.render())

    def test_each_ocr_updates_hexcore_change(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "accepted_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        self.assertIn("海克斯已变化：出现", model.hexcore_change)
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "全心为你", "尖端发明家"],
        )
        model.apply_frame_result(
            {
                "reason": "same_content_already_processed",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        self.assertIn("海克斯未变化", model.hexcore_change)
        model.apply_frame_result(
            {
                "reason": "accepted_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "尖端发明家"},
                        {"slot": "RIGHT", "raw_text": "全心为你"},
                    ]
                },
            }
        )
        self.assertIn("海克斯已变化", model.hexcore_change)
        self.assertIn("→", model.hexcore_change)
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "interval",
            }
        )
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "尖端发明家", "全心为你"],
        )
        self.assertIn("海克斯未变化", model.hexcore_change)
        self.assertIn("暂时离开画面", model.note)
        model.apply_frame_result(
            {
                "reason": "accepted_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        model.apply_frame_result(
            {
                "reason": "recognition_unknown",
                "reread_offer": True,
                "reread_cause": "interval",
            }
        )
        self.assertIn("海克斯未变化", model.hexcore_change)
        self.assertEqual(len(model.offer), 3)
        text = model.render()
        self.assertIn("读取原文:", text)
        self.assertIn("「物理转魔法」→ 物理转魔法", text)

    def test_interval_reread_updates_note(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.vision_status = "识别中"
        model.apply_frame_result(
            {
                "reason": "ocr_executed",
                "reread_offer": False,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        self.assertIn("本帧已读到三选一", model.note)
        self.assertIn("0.05s", model.vision_detail)
        self.assertIn("自动识别", model.click_banner)
        self.assertNotIn("识别 #", model.click_banner)
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "全心为你", "尖端发明家"],
        )

    def test_offer_band_change_updates_note(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.vision_status = "识别中"
        model.apply_frame_result(
            {
                "reason": "ocr_executed",
                "reread_offer": True,
                "reread_cause": "offer_band",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "物理转魔法"},
                        {"slot": "CENTER", "raw_text": "全心为你"},
                        {"slot": "RIGHT", "raw_text": "尖端发明家"},
                    ]
                },
            }
        )
        self.assertIn("文字区变化", model.note)
        self.assertIn("画面变化", model.vision_detail)
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "全心为你", "尖端发明家"],
        )

    def test_auto_reread_interval_is_50ms(self) -> None:
        self.assertAlmostEqual(click_flag.AUTO_REREAD_INTERVAL_SECONDS, 0.05)
        monitor = click_flag.AutoRereadMonitor(Path("."))
        self.assertAlmostEqual(monitor._interval, 0.05)

    def test_click_event_name_is_local_namespace(self) -> None:
        self.assertTrue(click_flag.CLICK_EVENT_NAME.startswith("Local\\"))

    def test_raw_input_sink_reaches_foreground_game(self) -> None:
        self.assertEqual(click_flag.RAW_MOUSE_SINK_FLAGS, click_flag.RIDEV_INPUTSINK)
        self.assertEqual(
            click_flag.RAW_MOUSE_SINK_FLAGS & click_flag.RIDEV_EXINPUTSINK, 0
        )

    def test_click_hit_distinguishes_overlay_from_game(self) -> None:
        self.assertEqual(
            click_flag.describe_click_hit(
                title="▶ 左键 #3",
                class_name="LoLRecognitionOverlay_1",
            ),
            "overlay",
        )
        self.assertEqual(
            click_flag.describe_click_hit(
                title="League of Legends (TM) Client",
                class_name="RiotWindowClass",
            ),
            "game",
        )
        self.assertEqual(
            click_flag.describe_click_hit(title="QQ", class_name="Chrome_WidgetWin_1"),
            "other:QQ",
        )
        self.assertEqual(click_flag.HWND_MESSAGE, -3)
        monitor = click_flag.LeftClickMonitor(Path("."), lambda: None)
        self.assertLessEqual(monitor._interval, 0.002)

    def test_any_window_left_click_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hits: list[int] = []
            monitor = click_flag.LeftClickMonitor(
                Path(tmp), lambda: hits.append(1)
            )
            self.assertTrue(monitor.emit("unit"))
            self.assertEqual(hits, [1])
            self.assertGreater(
                int((Path(tmp) / click_flag.FLAG_NAME).read_text(encoding="ascii")),
                0,
            )
            self.assertFalse(monitor.emit("unit"))

    def test_league_window_helpers_still_recognize_client(self) -> None:
        self.assertTrue(
            click_flag.looks_like_league_window(
                "League of Legends (TM) Client", "RiotWindowClass"
            )
        )
        self.assertTrue(
            click_flag.looks_like_league_window(None, "RiotWindowClass")
        )
        self.assertTrue(
            click_flag.looks_like_league_window("League of Legends", "RCLIENT")
        )
        self.assertTrue(click_flag.looks_like_league_window("英雄联盟", "RCLIENT"))
        self.assertFalse(
            click_flag.looks_like_league_window("LoL 识别（置顶）", "LoLRecognitionOverlay")
        )
        self.assertFalse(click_flag.looks_like_league_window("QQ", "Chrome_WidgetWin_1"))
        windows = [
            (
                "League of Legends (TM) Client",
                "RiotWindowClass",
                (0, 0, 1920, 1080),
            ),
            (
                "▶ 左键 #1",
                "LoLRecognitionOverlay_1",
                (1484, 16, 1904, 556),
            ),
        ]
        self.assertEqual(
            click_flag.league_contains_point(400, 400, windows)[0],
            "League of Legends (TM) Client",
        )
        self.assertEqual(
            click_flag.league_contains_point(1600, 200, windows)[0],
            "League of Legends (TM) Client",
        )
        self.assertIsNone(click_flag.league_contains_point(2000, 200, windows))
        self.assertGreaterEqual(click_flag.LEAGUE_WINDOWS_CACHE_SECONDS, 0.25)

    def test_click_flag_writes_monotonic_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            token = click_flag.write_left_click(workspace)
            text = (workspace / click_flag.FLAG_NAME).read_text(encoding="ascii")
            self.assertEqual(int(text), token)
            later = click_flag.write_left_click(workspace)
            self.assertGreater(later, token)
            hold = workspace / click_flag.OCR_HOLD_NAME
            click_flag.write_ocr_hold(workspace, True)
            self.assertEqual(hold.read_text(encoding="ascii"), "1")
            click_flag.write_ocr_hold(workspace, False)
            self.assertFalse(hold.exists())

    def test_raw_mouse_left_down_reads_64bit_payload(self) -> None:
        payload = bytearray(48)
        payload[28:30] = (1).to_bytes(2, "little")
        self.assertTrue(click_flag.raw_mouse_left_down(bytes(payload)))
        payload[28:30] = (2).to_bytes(2, "little")
        self.assertFalse(click_flag.raw_mouse_left_down(bytes(payload)))
        gate = click_flag.ClickCoalesce(0.1)
        self.assertTrue(gate.accept(1.0))
        self.assertFalse(gate.accept(1.05))
        self.assertTrue(gate.accept(1.2))

    def test_hid_report_left_button_bit(self) -> None:
        self.assertTrue(hid_mouse.hid_left_button_down(bytes([0x01, 0x00, 0x00])))
        self.assertFalse(hid_mouse.hid_left_button_down(bytes([0x02, 0x00, 0x00])))
        self.assertTrue(
            hid_mouse.hid_left_button_down(bytes([0x01, 0x01, 0x00, 0x00, 0x00]))
        )
        self.assertFalse(
            hid_mouse.hid_left_button_down(bytes([0x01, 0x00, 0x00, 0x00, 0x00]))
        )

    def test_frame_result_hides_partial_or_unknown_names(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "type": "frame_result",
                "recognition_debug": {
                    "cards": [
                        {
                            "slot": "LEFT",
                            "state": "UNKNOWN",
                            "raw_text": "假海克斯乱码",
                            "display_name": None,
                            "augment_id": None,
                        },
                        {
                            "slot": "CENTER",
                            "state": "RECOGNIZED",
                            "raw_text": "全心为你",
                            "display_name": "全心为你",
                            "augment_id": "ARAM_AllForYou",
                        },
                    ]
                },
            }
        )
        self.assertEqual(len(model.offer), 1)
        self.assertEqual(model.offer[0]["slot"], "CENTER")
        self.assertEqual(model.offer[0]["name"], "全心为你")
        text = model.render()
        current = text.split("当前海克斯:")[1].split("海克斯变化:")[0]
        self.assertNotIn("假海克斯乱码", current)
        self.assertIn("全心为你", current)
        self.assertIn("读取原文:", text)
        self.assertIn("「假海克斯乱码」→ 未对齐", text)
        self.assertIn("「全心为你」→ 全心为你", text)
        self.assertIn("已更新海克斯", model.note)
        self.assertIn("全心为你", model.note)

    def test_unaligned_ocr_raw_stays_visible_when_offer_empty(self) -> None:
        model = _model()
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "low_confidence",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "乱码甲"},
                        {"slot": "CENTER", "raw_text": "乱码乙"},
                        {"slot": "RIGHT", "raw_text": "乱码丙"},
                    ]
                },
            }
        )
        self.assertEqual(model.offer, [])
        text = model.render()
        current = text.split("当前海克斯:")[1].split("读取原文:")[0]
        self.assertNotIn("乱码甲", current)
        self.assertIn("读取原文:", text)
        self.assertIn("左. 「乱码甲」→ 未对齐", text)
        self.assertIn("中. 「乱码乙」→ 未对齐", text)
        self.assertIn("右. 「乱码丙」→ 未对齐", text)
        model.apply_frame_result(
            {
                "type": "frame_result",
                "recognition_debug": {
                    "cards": [
                        {
                            "slot": "LEFT",
                            "state": "RECOGNIZED",
                            "display_name": "物理转魔法",
                            "augment_id": "ARAM_ADAPt",
                        },
                        {
                            "slot": "CENTER",
                            "state": "RECOGNIZED",
                            "display_name": "全心为你",
                            "augment_id": "ARAM_AllForYou",
                        },
                        {
                            "slot": "RIGHT",
                            "state": "RECOGNIZED",
                            "display_name": "尖端发明家",
                            "augment_id": "ARAM_ApexInventor",
                        },
                    ]
                },
            }
        )
        self.assertEqual(len(model.offer), 3)
        self.assertIn("物理转魔法", model.render())
        model.apply_frame_result(
            {
                "type": "frame_result",
                "reason": "unsupported_aspect_ratio",
                "frames": [{"width": 1024, "height": 550}],
                "recognition_debug": None,
            }
        )
        self.assertIn("unsupported_aspect_ratio", model.render())
        self.assertIn("1024x550", model.render())

    def test_interval_ocr_merges_names_then_selection_uses_them(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "recognition_unknown",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {
                            "slot": "LEFT",
                            "raw_text": "物理转魔法",
                            "display_name": "物理转魔法",
                            "augment_id": "ARAM_ADAPt",
                        },
                        {
                            "slot": "CENTER",
                            "raw_text": "乱码乙",
                            "augment_id": None,
                        },
                    ]
                },
            }
        )
        self.assertEqual([card["name"] for card in model.offer], ["物理转魔法"])
        self.assertIn("左. 物理转魔法", model.render())
        self.assertIn("「乱码乙」→ 未对齐", model.render())
        model.apply_frame_result(
            {
                "reason": "recognition_unknown",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {
                            "slot": "CENTER",
                            "raw_text": "全心为你",
                            "display_name": "全心为你",
                            "augment_id": "ARAM_AllForYou",
                        },
                        {
                            "slot": "RIGHT",
                            "raw_text": "尖端发明家",
                            "display_name": "尖端发明家",
                            "augment_id": "ARAM_ApexInventor",
                        },
                    ]
                },
            }
        )
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "全心为你", "尖端发明家"],
        )
        self.assertIn("中. 全心为你", model.render())
        self.assertIn("右. 尖端发明家", model.render())
        model.offer = []
        model.apply_selection_observed(
            {
                "selected_augment_id": "UNKNOWN",
                "selected_slot": "RIGHT",
                "source": "sole_remaining",
                "offer_stage": 1,
            }
        )
        self.assertEqual(model.selected[0]["name"], "尖端发明家")
        self.assertEqual(model.selected[0]["slot"], "RIGHT")
        self.assertEqual(len(store.records), 1)

    def test_interval_ocr_records_sole_remaining_pick(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        three = {
            "reason": "accepted_offer",
            "reread_offer": True,
            "reread_cause": "interval",
            "recognition_debug": {
                "cards": [
                    {
                        "slot": "LEFT",
                        "raw_text": "物理转魔法",
                        "display_name": "物理转魔法",
                        "augment_id": "ARAM_ADAPt",
                    },
                    {
                        "slot": "CENTER",
                        "raw_text": "全心为你",
                        "display_name": "全心为你",
                        "augment_id": "ARAM_AllForYou",
                    },
                    {
                        "slot": "RIGHT",
                        "raw_text": "尖端发明家",
                        "display_name": "尖端发明家",
                        "augment_id": "ARAM_ApexInventor",
                    },
                ]
            },
        }
        model.apply_frame_result(three)
        remaining = {
            "reason": "recognition_unknown",
            "reread_offer": True,
            "reread_cause": "interval",
            "recognition_debug": {
                "cards": [
                    {"slot": "LEFT", "raw_text": "", "augment_id": None},
                    {
                        "slot": "CENTER",
                        "raw_text": "全心为你",
                        "display_name": "全心为你",
                        "augment_id": "ARAM_AllForYou",
                    },
                    {"slot": "RIGHT", "raw_text": "", "augment_id": None},
                ]
            },
        }
        model.apply_frame_result(remaining)
        self.assertEqual(model.selected, [])
        self.assertIn("「全心为你」→ 全心为你", model.render())
        model.apply_frame_result(remaining)
        self.assertEqual(model.selected[0]["name"], "全心为你")
        self.assertEqual(model.selected[0]["slot"], "CENTER")
        self.assertEqual(model.selected[0]["source"], "sole_remaining")
        self.assertEqual(len(store.records), 1)

    def test_interval_ocr_pick_ignores_ghost_text_on_vanished_cards(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "accepted_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {
                            "slot": "LEFT",
                            "raw_text": "物理转魔法",
                            "display_name": "物理转魔法",
                            "augment_id": "ARAM_ADAPt",
                        },
                        {
                            "slot": "CENTER",
                            "raw_text": "全心为你",
                            "display_name": "全心为你",
                            "augment_id": "ARAM_AllForYou",
                        },
                        {
                            "slot": "RIGHT",
                            "raw_text": "尖端发明家",
                            "display_name": "尖端发明家",
                            "augment_id": "ARAM_ApexInventor",
                        },
                    ]
                },
            }
        )
        ghost = {
            "reason": "recognition_unknown",
            "reread_offer": True,
            "reread_cause": "interval",
            "recognition_debug": {
                "cards": [
                    {"slot": "LEFT", "raw_text": "乱码甲"},
                    {
                        "slot": "CENTER",
                        "raw_text": "全心为你",
                        "display_name": "全心为你",
                        "augment_id": "ARAM_AllForYou",
                    },
                    {"slot": "RIGHT", "raw_text": "乱码丙"},
                ]
            },
        }
        model.apply_frame_result(ghost)
        self.assertEqual(model.selected, [])
        model.apply_frame_result(ghost)
        self.assertEqual(model.selected[0]["name"], "全心为你")
        self.assertEqual(model.selected[0]["slot"], "CENTER")

    def test_interval_ocr_pick_after_one_remaining_then_empty(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_frame_result(
            {
                "reason": "accepted_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {
                            "slot": "LEFT",
                            "raw_text": "物理转魔法",
                            "display_name": "物理转魔法",
                            "augment_id": "ARAM_ADAPt",
                        },
                        {
                            "slot": "CENTER",
                            "raw_text": "全心为你",
                            "display_name": "全心为你",
                            "augment_id": "ARAM_AllForYou",
                        },
                        {
                            "slot": "RIGHT",
                            "raw_text": "尖端发明家",
                            "display_name": "尖端发明家",
                            "augment_id": "ARAM_ApexInventor",
                        },
                    ]
                },
            }
        )
        model.apply_frame_result(
            {
                "reason": "recognition_unknown",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": {
                    "cards": [
                        {"slot": "LEFT", "raw_text": "", "augment_id": None},
                        {
                            "slot": "CENTER",
                            "raw_text": "全心为你",
                            "display_name": "全心为你",
                            "augment_id": "ARAM_AllForYou",
                        },
                        {"slot": "RIGHT", "raw_text": "", "augment_id": None},
                    ]
                },
            }
        )
        self.assertEqual(model.selected, [])
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "interval",
            }
        )
        self.assertEqual(model.selected[0]["name"], "全心为你")
        self.assertEqual(model.selected[0]["slot"], "CENTER")
        self.assertEqual(len(store.records), 1)

    def test_unknown_selection_uses_payload_display_name(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_selection_observed(
            {
                "selected_augment_id": "UNKNOWN",
                "selected_slot": "LEFT",
                "source": "sole_remaining",
                "offer_stage": 1,
                "display_name": "物理转魔法",
            }
        )
        self.assertEqual(model.selected[0]["name"], "物理转魔法")
        self.assertEqual(model.selected[0]["slot"], "LEFT")

    def test_selection_uses_payload_display_name_at_click(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.offer = []
        model.last_offer = []
        model.apply_selection_observed(
            {
                "selected_augment_id": "UNKNOWN",
                "selected_slot": "LEFT",
                "source": "click_luma_flash",
                "offer_stage": 1,
                "display_name": "物理转魔法",
            }
        )
        self.assertEqual(model.selected[0]["name"], "物理转魔法")
        self.assertEqual(model.selected[0]["slot"], "LEFT")
        self.assertIn("物理转魔法", model.note)

    def test_live_client_overrides_stale_champ_select_champion(self) -> None:
        model = _model()
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "ChampSelect", "championId": 126},
            }
        )
        self.assertEqual(model.champion, "#126")
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "InProgress"},
            }
        )
        self.assertIsNone(model.champion)
        model.apply_live_client(
            {
                "status": "READY",
                "championName": "Malzahar",
                "gameMode": "ARAM",
            }
        )
        self.assertEqual(model.champion, "玛尔扎哈")
        self.assertEqual(model.game_mode, "ARAM")

    def test_cpp_live_client_state_and_mayhem_timing(self) -> None:
        model = _model()
        model.apply_live_client(
            {
                "type": "live_client_state",
                "status": "READY",
                "player": {"championName": "Malzahar", "level": 8, "isDead": True},
            }
        )
        self.assertEqual(model.champion, "玛尔扎哈")
        self.assertEqual(model.live_level, 8)
        model.apply_mayhem_selection(
            {
                "status": "ARMED",
                "phase": "ARMED",
                "stage": 2,
                "thresholdLevel": 7,
            }
        )
        text = model.render()
        self.assertIn("自动识别中", text)
        self.assertIn("第 1 轮", text)

    def test_in_progress_does_not_keep_champ_select_hover(self) -> None:
        model = _model()
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "ChampSelect", "championId": 42},
            }
        )
        self.assertEqual(model.champion, "#42")
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "InProgress"},
            }
        )
        self.assertIsNone(model.champion)
        self.assertIn("英雄: —", model.render())
        self.assertTrue(model.vision_allowed())

    def test_vision_only_during_hexcore_window(self) -> None:
        model = _model()
        model.phase = "InProgress"
        model.live_level = 1
        model.live_is_dead = False
        self.assertTrue(model.vision_allowed())
        model.offer = [
            {"slot": "LEFT", "augment_id": "ARAM_ADAPt", "name": "物理转魔法"},
            {"slot": "CENTER", "augment_id": "ARAM_AllForYou", "name": "全心为你"},
            {"slot": "RIGHT", "augment_id": "ARAM_ApexInventor", "name": "尖端发明家"},
        ]
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "offer_stage": 1,
                "source": "flash_luma",
            }
        )
        self.assertFalse(model.vision_allowed())
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 200.0,
                "player": {
                    "championName": "Rumble",
                    "level": 7,
                    "isDead": False,
                },
            }
        )
        self.assertFalse(model.vision_allowed())
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 200.0,
                "player": {
                    "championName": "Rumble",
                    "level": 7,
                    "isDead": True,
                },
            }
        )
        self.assertTrue(model.vision_allowed())
        self.assertFalse(model._round_ocr_closed)
        self.assertTrue(model.vision_process_wanted())

    def test_vision_process_stays_warm_after_pick(self) -> None:
        model = _model()
        model.phase = "InProgress"
        model.live_level = 3
        model.live_is_dead = False
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "offer_stage": 1,
                "source": "sole_remaining",
            }
        )
        self.assertFalse(model.vision_allowed())
        self.assertTrue(model.vision_process_wanted())
        model.live_is_dead = True
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 220.0,
                "player": {
                    "championName": "Rumble",
                    "level": 7,
                    "isDead": False,
                },
            }
        )
        self.assertTrue(model.vision_allowed())
        self.assertFalse(model._round_ocr_closed)
        model.phase = "EndOfGame"
        self.assertFalse(model.vision_process_wanted())

    def test_reconnect_does_not_wipe_selected_hexcores(self) -> None:
        store = MemoryStore()
        model = _model(store)
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "InProgress", "championId": 78},
            }
        )
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "offer_stage": 1,
                "source": "sole_remaining",
            }
        )
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "Reconnect"},
            }
        )
        self.assertEqual(model.selected[0]["name"], "物理转魔法")
        self.assertEqual(len(store.records), 1)

    def test_vision_starts_when_match_starts(self) -> None:
        model = _model()
        self.assertTrue(model.vision_allowed())
        model.phase = "GameStart"
        self.assertTrue(model.vision_allowed())
        model.phase = "InProgress"
        self.assertTrue(model.vision_allowed())
        model.phase = "Reconnect"
        self.assertTrue(model.vision_allowed())
        model.phase = "ChampSelect"
        self.assertFalse(model.vision_allowed())
        model.phase = "Lobby"
        self.assertFalse(model.vision_allowed())
        model.phase = "EndOfGame"
        self.assertFalse(model.vision_allowed())

    def test_looks_like_game_window_ignores_launcher(self) -> None:
        self.assertTrue(
            vision_client.looks_like_game_window(
                "League of Legends (TM) Client", "RiotWindowClass"
            )
        )
        self.assertTrue(
            vision_client.looks_like_game_window(None, "RiotWindowClass")
        )
        self.assertTrue(
            vision_client.looks_like_game_window("League of Legends", None)
        )
        self.assertFalse(
            vision_client.looks_like_game_window("英雄联盟", "RCLIENT")
        )
        self.assertFalse(
            vision_client.looks_like_game_window("LoL 识别 · 0.2s", "LoLRecognitionOverlay")
        )

    def test_unconfirmed_waits_through_eight_ten_eleven(self) -> None:
        model = _model()
        model.phase = "InProgress"
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 40.0,
                "player": {
                    "championName": "Akali",
                    "level": 1,
                    "isDead": False,
                },
            }
        )
        model._probe_until = 0.0
        self.assertTrue(model.vision_allowed())
        self.assertEqual(model.confirmed_count(), 0)
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 280.0,
                "player": {
                    "championName": "Akali",
                    "level": 8,
                    "isDead": True,
                },
            }
        )
        model._probe_until = 0.0
        self.assertTrue(model.vision_allowed())
        self.assertEqual(model.owed_unconfirmed(), 2)
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 360.0,
                "player": {
                    "championName": "Akali",
                    "level": 10,
                    "isDead": False,
                },
            }
        )
        model._probe_until = 0.0
        self.assertTrue(model.vision_allowed())
        model.live_level = None
        self.assertTrue(model.vision_allowed())
        model.apply_live_client(
            {
                "status": "READY",
                "gameTime": 420.0,
                "player": {
                    "championName": "Akali",
                    "level": 11,
                    "isDead": False,
                },
            }
        )
        model._probe_until = 0.0
        self.assertTrue(model.vision_allowed())
        self.assertEqual(model.owed_unconfirmed(), 3)
        self.assertEqual(model.confirmed_count(), 0)

    def test_new_match_resets_in_window_history(self) -> None:
        store = MemoryStore()
        model = _model(store)
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "InProgress", "championId": 78},
            }
        )
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "offer_stage": 1,
            }
        )
        model.apply_lcu(
            {
                "status": "READY",
                "reason": "ok",
                "context": {
                    "gameflowPhase": "ChampSelect",
                    "championId": 51,
                    "benchChampions": [{"championId": 78, "name": "波比"}],
                },
            }
        )
        self.assertEqual(model.selected, [])
        self.assertEqual(len(store.records), 1)
        self.assertEqual(model.champion, "凯特琳")

    def test_unknown_selection_is_ignored(self) -> None:
        store = MemoryStore()
        model = _model(store)
        model.apply_selection_observed(
            {
                "recognition_status": "UNKNOWN",
                "selected_augment_id": "UNKNOWN",
                "offer_stage": 1,
            }
        )
        self.assertEqual(store.records, [])

    def test_popup_keeps_offer_until_pick_then_tracks_rounds(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        first = {
            "reason": "accepted_offer",
            "reread_offer": True,
            "reread_cause": "interval",
            "recognition_debug": {
                "cards": [
                    {
                        "slot": "LEFT",
                        "raw_text": "物理转魔法",
                        "display_name": "物理转魔法",
                        "augment_id": "ARAM_ADAPt",
                    },
                    {
                        "slot": "CENTER",
                        "raw_text": "全心为你",
                        "display_name": "全心为你",
                        "augment_id": "ARAM_AllForYou",
                    },
                    {
                        "slot": "RIGHT",
                        "raw_text": "尖端发明家",
                        "display_name": "尖端发明家",
                        "augment_id": "ARAM_ApexInventor",
                    },
                ]
            },
        }
        model.apply_frame_result(first)
        text = model.render()
        self.assertIn("当前海克斯: 第 1 轮", text)
        self.assertIn("左. 物理转魔法", text)
        self.assertIn("中. 全心为你", text)
        self.assertIn("右. 尖端发明家", text)
        self.assertIn("读取原文:", text)
        changed = {
            "reason": "accepted_offer",
            "reread_offer": True,
            "reread_cause": "interval",
            "recognition_debug": {
                "cards": [
                    {
                        "slot": "LEFT",
                        "raw_text": "物理转魔法",
                        "display_name": "物理转魔法",
                        "augment_id": "ARAM_ADAPt",
                    },
                    {
                        "slot": "CENTER",
                        "raw_text": "尖端发明家",
                        "display_name": "尖端发明家",
                        "augment_id": "ARAM_ApexInventor",
                    },
                    {
                        "slot": "RIGHT",
                        "raw_text": "全心为你",
                        "display_name": "全心为你",
                        "augment_id": "ARAM_AllForYou",
                    },
                ]
            },
        }
        model.apply_frame_result(changed)
        text = model.render()
        self.assertIn("中. 尖端发明家", text)
        self.assertIn("右. 全心为你", text)
        self.assertIn("海克斯已变化", model.hexcore_change)
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "interval",
            }
        )
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "尖端发明家", "全心为你"],
        )
        self.assertIn("左. 物理转魔法", model.render())
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "source": "sole_remaining",
                "offer_stage": 9,
            }
        )
        self.assertEqual(model.selected[0]["stage"], 1)
        self.assertEqual(model.selected[0]["name"], "物理转魔法")
        self.assertEqual(model.offer, [])
        text = model.render()
        current = text.split("当前海克斯:")[1].split("已选海克斯:")[0]
        self.assertIn("—", current)
        self.assertNotIn("读取原文:", text)
        self.assertIn("第 1 轮 物理转魔法", text)
        model.apply_frame_result(
            {
                "reason": "no_offer_on_screen",
                "reread_offer": True,
                "reread_cause": "interval",
            }
        )
        self.assertEqual(model.offer, [])
        self.assertTrue(model._round_ocr_closed)
        model.apply_frame_result(
            {
                "reason": "session_invalid_offer",
                "reread_offer": True,
                "reread_cause": "interval",
                "recognition_debug": first["recognition_debug"],
            }
        )
        self.assertFalse(model._round_ocr_closed)
        self.assertIn("当前海克斯: 第 2 轮", model.render())
        self.assertIn("左. 物理转魔法", model.render())
        self.assertIn("第 1 轮 物理转魔法", model.render())
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_AllForYou",
                "selected_slot": "CENTER",
                "source": "flash_luma",
                "offer_stage": 99,
            }
        )
        self.assertEqual([item["stage"] for item in model.selected], [1, 2])
        self.assertEqual(model.selected[1]["name"], "全心为你")
        text = model.render()
        self.assertIn("第 1 轮 物理转魔法", text)
        self.assertIn("第 2 轮 全心为你", text)
        current = text.split("当前海克斯:")[1].split("已选海克斯:")[0]
        self.assertIn("—", current)
        self.assertEqual(len(store.records), 2)

    def test_second_round_game_state_reopens_empty_popup(self) -> None:
        store = MemoryStore()
        model = _model(store)
        _open_hexcore(model)
        model.apply_selection_observed(
            {
                "selected_augment_id": "ARAM_ADAPt",
                "selected_slot": "LEFT",
                "source": "sole_remaining",
            }
        )
        self.assertTrue(model._round_ocr_closed)
        self.assertEqual(model.offer, [])
        model.apply_game_state(
            {
                "offer_round": 3,
                "current_offer": {
                    "recognitions": [
                        {
                            "state": "RECOGNIZED",
                            "slot": "LEFT",
                            "augment_id": "ARAM_ADAPt",
                            "display_name": "物理转魔法",
                        },
                        {
                            "state": "RECOGNIZED",
                            "slot": "CENTER",
                            "augment_id": "ARAM_AllForYou",
                            "display_name": "全心为你",
                        },
                        {
                            "state": "RECOGNIZED",
                            "slot": "RIGHT",
                            "augment_id": "ARAM_ApexInventor",
                            "display_name": "尖端发明家",
                        },
                    ]
                },
            }
        )
        self.assertFalse(model._round_ocr_closed)
        self.assertEqual(
            [card["name"] for card in model.offer],
            ["物理转魔法", "全心为你", "尖端发明家"],
        )
        self.assertIn("当前海克斯: 第 2 轮", model.render())
        self.assertIn("第 1 轮 物理转魔法", model.render())


class VisionClientTests(unittest.TestCase):
    def test_classifies_game_state_and_selection(self) -> None:
        game_state = vision_client.parse_vision_line(
            json.dumps(
                {
                    "champion": "波比",
                    "offer_round": 1,
                    "current_offer": {"recognitions": []},
                }
            )
        )
        selection = vision_client.parse_vision_line(
            '{"type":"selection_observed","selected_augment_id":"ARAM_ADAPt"}'
        )
        self.assertIsNotNone(game_state)
        self.assertIsNotNone(selection)
        assert game_state is not None and selection is not None
        self.assertEqual(game_state[0], "game_state")
        self.assertEqual(selection[0], "selection_observed")

    def test_classifies_frame_result(self) -> None:
        parsed = vision_client.parse_vision_line(
            '{"type":"frame_result","recognition_debug":{"cards":[]}}'
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], "frame_result")

    def test_classifies_click_ack(self) -> None:
        parsed = vision_client.parse_vision_line(
            '{"type":"click_ack","reason":"click_received"}'
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], "click_ack")

    def test_vision_command_disables_cpp_lcu(self) -> None:
        command = vision_client.build_vision_command(
            exe=Path("lol_augment_assistant.exe"),
            knowledge=Path("augments.zh-CN.json"),
            workspace=Path("runtime"),
            champion="波比",
            completed_offers=1,
        )
        self.assertIn("--lcu-context", command)
        self.assertEqual(command[command.index("--lcu-context") + 1], "off")
        self.assertEqual(command[command.index("--live-client") + 1], "auto")
        self.assertEqual(command[command.index("--champion") + 1], "波比")
        self.assertEqual(command[command.index("--completed-offers") + 1], "1")
        self.assertEqual(command[command.index("--mode") + 1], "KIWI")
        self.assertEqual(command[command.index("--capture-backend") + 1], "wgc")
        self.assertIn("--no-hotkey", command)
        self.assertNotIn("--no-force-recognition-hotkey", command)
        self.assertEqual(command[command.index("--window-title") + 1], "League of Legends (TM) Client")
        self.assertNotIn("--hwnd", command)
        self.assertNotIn("--hero", command)

    def test_vision_command_prefers_hwnd(self) -> None:
        command = vision_client.build_vision_command(
            exe=Path("lol_augment_assistant.exe"),
            knowledge=Path("augments.zh-CN.json"),
            workspace=Path("runtime"),
            hwnd=0xB0B14,
        )
        self.assertEqual(command[command.index("--hwnd") + 1], "0xb0b14")
        self.assertNotIn("--window-title", command)
        self.assertNotIn("--collect-samples", command)

    def test_parses_cli_error_from_stderr(self) -> None:
        self.assertEqual(
            vision_client.parse_vision_error_line(
                '{"type":"cli_error","error":"Collection options require --collect-samples"}'
            ),
            "Collection options require --collect-samples",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vision_stderr.log"
            path.write_text(
                '{"type":"cli_error","error":"Collection options require --collect-samples"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                vision_client.last_vision_error(path),
                "Collection options require --collect-samples",
            )

    def test_classifies_mayhem_and_live_client_state(self) -> None:
        mayhem = vision_client.parse_vision_line(
            '{"type":"mayhem_selection_state","status":"DEATH_TRIGGERED","stage":2}'
        )
        live = vision_client.parse_vision_line(
            '{"type":"live_client_state","status":"READY","player":{"championName":"Malzahar","level":8}}'
        )
        self.assertIsNotNone(mayhem)
        self.assertIsNotNone(live)
        assert mayhem is not None and live is not None
        self.assertEqual(mayhem[0], "mayhem_selection_state")
        self.assertEqual(live[0], "live_client_state")


class LiveClientTests(unittest.TestCase):
    def test_parses_champion_and_game_mode(self) -> None:
        name = live_client.parse_active_player_name('"Player#123"')
        champion = live_client.parse_champion_name(
            json.dumps(
                [
                    {
                        "summonerName": "Player#123",
                        "championName": "Malzahar",
                    }
                ]
            ),
            name or "",
        )
        mode = live_client.parse_game_mode('{"gameMode":"ARAM","gameTime":12.5}')
        game_time = live_client.parse_game_time('{"gameMode":"ARAM","gameTime":12.5}')
        player = live_client.parse_player_state(
            json.dumps(
                [
                    {
                        "summonerName": "Player#123",
                        "championName": "Malzahar",
                        "level": 7,
                        "isDead": True,
                        "respawnTimer": 8.5,
                    }
                ]
            ),
            name or "",
        )
        self.assertEqual(name, "Player#123")
        self.assertEqual(champion, "Malzahar")
        self.assertEqual(mode, "ARAM")
        self.assertEqual(game_time, 12.5)
        self.assertEqual(player["level"], 7)
        self.assertTrue(player["isDead"])


class HexcoreGateTests(unittest.TestCase):
    def test_levels_and_fountain(self) -> None:
        self.assertEqual(hexcore_gate.next_hexcore_level(0), 1)
        self.assertEqual(hexcore_gate.next_hexcore_level(1), 7)
        self.assertEqual(hexcore_gate.next_hexcore_level(3), 15)
        self.assertIsNone(hexcore_gate.next_hexcore_level(4))
        self.assertEqual(hexcore_gate.eligible_offer_count(5), 1)
        self.assertEqual(hexcore_gate.eligible_offer_count(7), 2)
        self.assertEqual(hexcore_gate.pending_offer_count(5, 1), 0)
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=1,
                is_dead=False,
                game_time=200.0,
                completed=0,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=5,
                is_dead=False,
                game_time=240.0,
                completed=0,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=7,
                is_dead=False,
                game_time=200.0,
                completed=1,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=3,
                is_dead=False,
                game_time=200.0,
                completed=0,
                seconds_since_respawn=None,
                probe_active=True,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=6,
                is_dead=False,
                game_time=200.0,
                completed=0,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=8,
                is_dead=True,
                game_time=300.0,
                completed=0,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=10,
                is_dead=False,
                game_time=400.0,
                completed=0,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=11,
                is_dead=False,
                game_time=480.0,
                completed=0,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_screen_open(
                level=None,
                is_dead=False,
                game_time=400.0,
                completed=0,
                seconds_since_respawn=None,
                owed=2,
            )
        )
        self.assertFalse(
            hexcore_gate.hexcore_screen_open(
                level=6,
                is_dead=True,
                game_time=200.0,
                completed=1,
                seconds_since_respawn=None,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_ocr_open(
                completed=0,
                level=1,
                is_dead=False,
                round_closed=False,
            )
        )
        self.assertFalse(
            hexcore_gate.hexcore_ocr_open(
                completed=1,
                level=7,
                is_dead=False,
                round_closed=True,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_ocr_open(
                completed=1,
                level=7,
                is_dead=True,
                round_closed=True,
            )
        )
        self.assertFalse(
            hexcore_gate.hexcore_ocr_open(
                completed=1,
                level=5,
                is_dead=True,
                round_closed=True,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_ocr_open(
                completed=1,
                level=None,
                is_dead=False,
                round_closed=True,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_ocr_open(
                completed=1,
                level=7,
                is_dead=None,
                round_closed=True,
            )
        )
        self.assertTrue(
            hexcore_gate.hexcore_ocr_open(
                completed=1,
                level=7,
                is_dead=False,
                round_closed=True,
                seconds_since_respawn=10.0,
            )
        )


class PathsTests(unittest.TestCase):
    def test_explicit_vision_exe_wins(self) -> None:
        paths = _load("overlay_paths", OVERLAY_DIR / "paths.py")
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "lol_augment_assistant.exe"
            fake.write_bytes(b"mz")
            found = paths.find_vision_exe(fake)
            self.assertEqual(found, fake)


class LeagueRootTests(unittest.TestCase):
    def test_accepts_directory_with_leagueclient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "WeGameApps" / "英雄联盟"
            (root / "LeagueClient").mkdir(parents=True)
            self.assertTrue(league_root.is_league_root(root))
            self.assertFalse(league_root.is_league_root(root.parent))


if __name__ == "__main__":
    unittest.main()
