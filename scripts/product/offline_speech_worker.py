from __future__ import annotations

import argparse
import json
import sys
import threading
from array import array
from pathlib import Path
from typing import Any, Callable, TextIO

from offline_speech_assets import OfflineSpeechPaths, resolve_offline_speech_paths


class PlaybackComplete(Exception):
    pass


class PlaybackAbort(Exception):
    pass


def build_sherpa_tts(paths: OfflineSpeechPaths) -> Any:
    import sherpa_onnx

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(paths.model),
        lexicon=str(paths.lexicon),
        tokens=str(paths.tokens),
        data_dir=str(paths.data_dir),
    )
    model = sherpa_onnx.OfflineTtsModelConfig(
        vits=vits,
        num_threads=2,
        debug=False,
        provider="cpu",
    )
    config = sherpa_onnx.OfflineTtsConfig(
        model=model,
        rule_fsts=",".join(str(path) for path in paths.rule_fsts),
    )
    return sherpa_onnx.OfflineTts(config)


def create_raw_output_stream(**kwargs: Any) -> Any:
    import sounddevice

    callback = kwargs.pop("callback")

    def sounddevice_callback(*args: Any) -> None:
        try:
            callback(*args)
        except PlaybackComplete as signal:
            raise sounddevice.CallbackStop from signal
        except PlaybackAbort as signal:
            raise sounddevice.CallbackAbort from signal

    return sounddevice.RawOutputStream(callback=sounddevice_callback, **kwargs)


class OfflineSpeechEngine:
    def __init__(
        self,
        paths: OfflineSpeechPaths,
        emit: Callable[[dict[str, Any]], None],
        tts_factory: Callable[[OfflineSpeechPaths], Any] = build_sherpa_tts,
        raw_output_stream_factory: Callable[..., Any] = create_raw_output_stream,
    ) -> None:
        self._paths = paths
        self._emit = emit
        self._tts_factory = tts_factory
        self._raw_output_stream_factory = raw_output_stream_factory
        self._lock = threading.RLock()
        self._synthesis_lock = threading.Lock()
        self._tts: Any = None
        self._current_request_id: int | None = None
        self._stream: Any = None
        self._playback_events: dict[int, threading.Event] = {}
        self._playback_threads: set[threading.Thread] = set()
        self._generation_threads: set[threading.Thread] = set()
        self._closed = False

    def start(self) -> bool:
        with self._lock:
            if self._tts is not None:
                return True
            if self._closed:
                return False
        try:
            tts = self._tts_factory(self._paths)
        except Exception as error:
            self._emit_error(None, f"offline speech model failed: {error}")
            return False
        with self._lock:
            if self._closed:
                return False
            self._tts = tts
        self._emit({"event": "ready"})
        return True

    def speak(self, request_id: int, text: str) -> bool:
        if type(request_id) is not int or not isinstance(text, str) or not text.strip():
            self._emit_error(request_id, "speech text must not be empty")
            return False
        with self._lock:
            if self._closed or self._tts is None:
                self._emit_error(request_id, "offline speech engine is not ready")
                return False
            old_stream = self._invalidate_current_locked()
            self._current_request_id = request_id
            thread = threading.Thread(
                target=self._generate,
                args=(request_id, text.strip()),
                name=f"offline-speech-generate-{request_id}",
                daemon=True,
            )
            self._generation_threads.add(thread)
        self._abort_stream(old_stream)
        thread.start()
        return True

    def cancel(self, request_id: int) -> None:
        with self._lock:
            if request_id != self._current_request_id:
                return
            stream = self._invalidate_current_locked()
        self._abort_stream(stream)
        self._emit({"event": "cancelled", "request_id": request_id})

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            stream = self._invalidate_current_locked()
            threads = tuple(self._generation_threads | self._playback_threads)
        self._abort_stream(stream)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(2.0)

    def _generate(self, request_id: int, text: str) -> None:
        try:
            with self._synthesis_lock:
                audio = self._tts.generate(text, sid=0, speed=1.0)
            pcm = array("f", audio.samples).tobytes()
            if not pcm:
                raise ValueError("speech synthesis returned no samples")
            self._begin_playback(request_id, pcm, int(audio.sample_rate))
        except Exception as error:
            if self._is_current(request_id):
                self._emit_error(request_id, f"offline speech failed: {error}")
                self._clear_request(request_id)
        finally:
            with self._lock:
                self._generation_threads.discard(threading.current_thread())

    def _begin_playback(self, request_id: int, pcm: bytes, sample_rate: int) -> None:
        cursor = 0
        playback_done = threading.Event()

        def callback(outdata: Any, frames: int, time_info: Any, status: Any) -> None:
            nonlocal cursor
            with self._lock:
                if request_id != self._current_request_id or self._closed:
                    outdata[:] = bytes(len(outdata))
                    raise PlaybackAbort()
                take = min(frames * 4, len(pcm) - cursor)
                outdata[:take] = pcm[cursor : cursor + take]
                outdata[take:] = bytes(len(outdata) - take)
                cursor += take
                if cursor >= len(pcm):
                    raise PlaybackComplete()

        stream = self._raw_output_stream_factory(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            callback=callback,
            finished_callback=playback_done.set,
        )
        monitor = threading.Thread(
            target=self._finish_playback,
            args=(request_id, stream, playback_done),
            name=f"offline-speech-playback-{request_id}",
            daemon=True,
        )
        with self._lock:
            if request_id != self._current_request_id or self._closed:
                self._abort_stream(stream)
                return
            self._stream = stream
            self._playback_events[request_id] = playback_done
            self._emit({"event": "started", "request_id": request_id})
            try:
                stream.start()
            except Exception:
                self._playback_events.pop(request_id, None)
                self._stream = None
                playback_done.set()
                raise
            self._playback_threads.add(monitor)
            monitor.start()

    def _finish_playback(self, request_id: int, stream: Any, playback_done: threading.Event) -> None:
        playback_done.wait()
        try:
            stream.close()
        except Exception:
            pass
        with self._lock:
            self._playback_events.pop(request_id, None)
            self._playback_threads.discard(threading.current_thread())
            if request_id != self._current_request_id or self._closed:
                return
            self._current_request_id = None
            self._stream = None
        self._emit({"event": "finished", "request_id": request_id})

    def _is_current(self, request_id: int) -> bool:
        with self._lock:
            return request_id == self._current_request_id and not self._closed

    def _clear_request(self, request_id: int) -> None:
        with self._lock:
            if request_id == self._current_request_id:
                self._current_request_id = None
                self._stream = None

    def _invalidate_current_locked(self) -> Any:
        request_id = self._current_request_id
        stream = self._stream
        self._current_request_id = None
        self._stream = None
        playback_done = self._playback_events.pop(request_id, None)
        if playback_done is not None:
            playback_done.set()
        return stream

    @staticmethod
    def _abort_stream(stream: Any) -> None:
        if stream is None:
            return
        try:
            stream.abort()
            stream.close()
        except Exception:
            pass

    def _emit_error(self, request_id: int | None, message: str) -> None:
        event: dict[str, Any] = {"event": "error", "message": message}
        if request_id is not None:
            event["request_id"] = request_id
        self._emit(event)


def _write_emitter(output_stream: TextIO) -> Callable[[dict[str, Any]], None]:
    lock = threading.Lock()

    def emit(event: dict[str, Any]) -> None:
        with lock:
            output_stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            output_stream.flush()

    return emit


def _dispatch(engine: OfflineSpeechEngine, command: dict[str, Any], emit: Callable[[dict], None]) -> bool:
    name = command.get("command")
    if name == "close":
        return False
    if name == "speak":
        engine.speak(command.get("request_id"), command.get("text"))
    elif name == "cancel":
        engine.cancel(command.get("request_id"))
    else:
        emit({"event": "error", "message": f"invalid command: {name}"})
    return True


def run_protocol(
    paths: OfflineSpeechPaths,
    input_stream: TextIO,
    output_stream: TextIO,
    engine_factory: Callable[..., OfflineSpeechEngine] = OfflineSpeechEngine,
) -> int:
    emit = _write_emitter(output_stream)
    engine = engine_factory(paths, emit)
    try:
        if not engine.start():
            return 1
        for line in input_stream:
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("command must be an object")
            except (json.JSONDecodeError, ValueError) as error:
                emit({"event": "error", "message": f"invalid JSON command: {error}"})
                continue
            if not _dispatch(engine, command, emit):
                break
        return 0
    finally:
        engine.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the GameBuddy offline speech worker")
    parser.add_argument("--bundle-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        paths = resolve_offline_speech_paths(arguments.bundle_root)
    except ValueError as error:
        emitter = _write_emitter(sys.stdout)
        emitter({"event": "error", "message": str(error)})
        return 1
    return run_protocol(paths, sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
