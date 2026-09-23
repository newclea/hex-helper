"""Offline checks run inside the shipped executables, without a system Python."""
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path


def _report(path, action):
    try:
        result = {"ok": True, **action()}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def check_voice(root, report):
    def run():
        from offline_speech_assets import resolve_offline_speech_paths
        from offline_speech_worker import initialize_native_dependencies, build_sherpa_tts
        from fixed_speech_cache import FixedSpeechCache
        from speech_policy import FIXED_SPEECH_TEXTS
        initialize_native_dependencies()
        paths = resolve_offline_speech_paths(root)
        cache = FixedSpeechCache(root / "assets/speech/fixed", paths.model)
        assert all(cache.read(text) is not None for text in FIXED_SPEECH_TEXTS)
        audio = build_sherpa_tts(paths).generate("你好，欢迎使用悠米。", sid=0, speed=1.0)
        assert len(audio.samples) > 0 and audio.sample_rate > 0
        return {"frozen": bool(getattr(sys, "frozen", False)),
                "samples": len(audio.samples), "sample_rate": int(audio.sample_rate),
                "fixed_cache_count": len(FIXED_SPEECH_TEXTS), "audio_played": False}
    return _report(report, run)


def check_app(report):
    def run():
        from paths import bundle_dir, find_vision_exe, champion_catalog_path, augment_catalog_path
        from offline_speech_assets import verify_manifest, resolve_offline_speech_paths
        from offline_speech import OfflineSpeechAdapter
        from recommendation_engine import RecommendationEngine
        from cat_animation import load_animation_manifest
        from cat_overlay import CatOverlayWindow
        root = bundle_dir()
        resolve_offline_speech_paths(root)
        errors = verify_manifest(root, root / "assets/speech/SHA256SUMS")
        assert not errors, errors
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        probe = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
            "[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }"],
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        languages = probe.stdout.decode("utf-8", errors="replace").split()
        assert probe.returncode == 0 and {"zh-CN", "zh-Hans-CN"}.intersection(languages), (
            "Windows simplified-Chinese OCR is unavailable. Install the simplified-Chinese "
            "OCR language component in Windows Settings; Python dependencies are already bundled.")
        for path in (champion_catalog_path(), augment_catalog_path()):
            json.loads(path.read_text(encoding="utf-8"))
        RecommendationEngine.load(root / "data/recommendation")
        assert load_animation_manifest(root / "assets/gamebuddy") is not None
        # Exercise the bundled Tcl/Tk, PNG decoder and native overlay setup.
        # Hide the window immediately and close it without starting LCU/Agent.
        window = CatOverlayWindow(cat_path=root / "assets/gamebuddy-cat.png",
            animation_root=root / "assets/gamebuddy", on_strategy=lambda _: None,
            voice_enabled=False)
        def ready():
            window._root.withdraw()
            window._root.after(100, window._close)
        window.on_ready = ready
        assert window.run() == 0
        assert window._cat is not None and window._cat_frames, "PNG/animation loading failed"
        vision = find_vision_exe()
        assert vision is not None
        result = subprocess.run([str(vision), "--help"], capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        worker = Path(sys.executable).with_name("OfflineSpeechWorker.exe")
        voice_report = report.with_name(report.stem + "-voice.json")
        result = subprocess.run([str(worker), "--self-test", str(root), str(voice_report)],
                                capture_output=True, timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert json.loads(voice_report.read_text(encoding="utf-8"))["ok"]
        adapter = OfflineSpeechAdapter(bundle_root=root)
        try:
            assert adapter.start(), "packaged speech protocol did not become ready"
        finally:
            adapter.close()
        return {"frozen": bool(getattr(sys, "frozen", False)), "bundle_root": str(root),
                "engine_sha256": hashlib.sha256(vision.read_bytes()).hexdigest(),
                "voice_protocol_ready": True, "gui_initialized": True,
                "windows_ocr_languages": languages,
                "voice_report": str(voice_report)}
    return _report(report, run)
