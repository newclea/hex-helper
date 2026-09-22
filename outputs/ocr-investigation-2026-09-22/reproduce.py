"""Reproduce alive-player OCR UI/voice admission without running the game."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

root = Path(__file__).resolve().parents[2]
for folder in ('product', 'recognition_overlay', 'phase4'):
    sys.path.insert(0, str(root / 'scripts' / folder))
from augment_catalog import AugmentCatalog
from history_store import HistoryStore
from view_model import RecognitionViewModel
from controller import ProductController
from recommendation_engine import RecommendationEngine
from strategy_store import StrategyStore
from speech_policy import CompanionSpeechPolicy

results = []
with TemporaryDirectory() as directory:
    for completed in (0, 1):
        model = RecognitionViewModel(catalog=AugmentCatalog({}),
                                     store=HistoryStore(Path(directory) / 'history.jsonl'))
        model.phase = 'InProgress'
        model.game_mode = 'KIWI'
        model.champion = '金克丝'
        model.selected = [{'stage': i + 1, 'source': 'hud_icon_template',
                           'augment_id': f'augment-{i}', 'name': 'previous'}
                          for i in range(completed)]
        model.apply_live_client({'status': 'READY', 'gameTime': 300,
                                 'player': {'level': 9, 'isDead': False}})
        gate_before = model.vision_allowed()
        model.apply_frame_result({
            'reason': 'recognition_unknown', 'accepted': False,
            'ocr_executed': True, 'reread_cause': 'interval',
            'raw_detector': {'visible': True, 'reason': 'visible'},
            'stable_detector': {'visible': True, 'reason': 'stable_visible'},
            'recognition_debug': {'cards': [
                {'slot': slot, 'state': 'UNKNOWN', 'raw_text': '', 'augment_id': None}
                for slot in ('LEFT', 'CENTER', 'RIGHT')]},
        })
        controller = ProductController(
            engine=RecommendationEngine.load(root / 'data/recommendation'),
            store=StrategyStore(Path(directory) / 'strategy.json'))
        view = controller.present(model.snapshot())
        policy = CompanionSpeechPolicy()
        policy.update(view, 100)
        speech = policy.tick(102.1)
        result = {'completed': completed, 'level': 9, 'is_dead': model.live_is_dead,
                  'death_sequence': model._death_sequence,
                  'ocr_allowed_before_frame': gate_before,
                  'ocr_allowed_after_frame': model.vision_allowed(),
                  'accepted_cards': len(model.offer), 'view': view,
                  'speech_after_two_seconds': [m.summary for m in speech]}
        results.append(result)
        assert model._death_sequence == 0
        assert not view['bubble_visible']
        assert not speech
        assert not model.vision_allowed()
    assert results[0]['ocr_allowed_before_frame'] is False
    assert results[1]['ocr_allowed_before_frame'] is False
path = Path(__file__).with_name('regression-after-fix.json')
path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=True, indent=2))
