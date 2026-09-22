from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from array import array
from dataclasses import dataclass, field
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
    if not config.validate():
        raise ValueError("offline speech configuration is invalid")
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


@dataclass
class _RequestControl:
    request_id: int
    cancelled: threading.Event = field(default_factory=threading.Event)


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
        self._current: _RequestControl | None = None
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
            old_control = self._invalidate_current_locked()
            control = _RequestControl(request_id)
            self._current = control
            thread = threading.Thread(
                target=self._generate,
                args=(control, text.strip()),
                name=f"offline-speech-generate-{request_id}",
                daemon=True,
            )
            self._generation_threads.add(thread)
        self._request_cancel(old_control)
        thread.start()
        return True

    def cancel(self, request_id: int) -> None:
        with self._lock:
            if self._current is None or request_id != self._current.request_id:
                return
            control = self._invalidate_current_locked()
        self._request_cancel(control)
        self._emit({"event": "cancelled", "request_id": request_id})

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            control = self._invalidate_current_locked()
            threads = tuple(self._generation_threads | self._playback_threads)
        self._request_cancel(control)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(2.0)

    def _generate(self, control: _RequestControl, text: str) -> None:
        try:
            with self._synthesis_lock:
                audio = self._tts.generate(text, sid=0, speed=1.0)
            pcm = array("f", audio.samples).tobytes()
            if not pcm:
                raise ValueError("speech synthesis returned no samples")
            self._begin_playback(control, pcm, int(audio.sample_rate))
        except Exception as error:
            if self._is_current(control):
                self._emit_error(control.request_id, f"offline speech failed: {error}")
                self._clear_request(control)
        finally:
            with self._lock:
                self._generation_threads.discard(threading.current_thread())

    def _begin_playback(self, control: _RequestControl, pcm: bytes, sample_rate: int) -> None:
        with self._lock:
            if not self._is_current_locked(control):
                return
            owner = threading.Thread(
                target=self._playback_owner,
                args=(control, pcm, sample_rate),
                name=f"offline-speech-playback-{control.request_id}",
                daemon=True,
            )
            self._playback_threads.add(owner)
            owner.start()

    def _playback_owner(self, control: _RequestControl, pcm: bytes, sample_rate: int) -> None:
        stream = None
        if not self._is_current(control):
            self._discard_playback_thread()
            return
        playback_done = threading.Event()
        callback = self._make_audio_callback(control, pcm)
        try:
            stream = self._create_stream(sample_rate, callback, playback_done)
            if not self._start_stream(control, stream):
                return
            self._wait_for_playback(control, stream, playback_done)
        except Exception as error:
            if self._is_current(control):
                self._emit_error(control.request_id, f"offline speech failed: {error}")
                self._clear_request(control)
        finally:
            if stream is not None:
                self._close_stream(stream)
            self._discard_playback_thread()

    def _create_stream(
        self,
        sample_rate: int,
        callback: Callable[..., None],
        playback_done: threading.Event,
    ) -> Any:
        return self._raw_output_stream_factory(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=callback,
            finished_callback=playback_done.set,
        )

    @staticmethod
    def _make_audio_callback(control: _RequestControl, pcm: bytes) -> Callable[..., None]:
        blocks, silent_block = _prepare_audio_blocks(pcm)
        block_index = 0

        def callback(outdata: Any, frames: int, time_info: Any, status: Any) -> None:
            nonlocal block_index
            if control.cancelled.is_set():
                outdata[:] = silent_block
                raise PlaybackAbort()
            outdata[:] = blocks[block_index]
            block_index += 1
            if block_index == len(blocks):
                raise PlaybackComplete()

        return callback

    def _start_stream(self, control: _RequestControl, stream: Any) -> bool:
        with self._lock:
            if not self._is_current_locked(control):
                return False
            self._emit({"event": "started", "request_id": control.request_id})
            stream.start()
            return True

    def _wait_for_playback(
        self,
        control: _RequestControl,
        stream: Any,
        playback_done: threading.Event,
    ) -> None:
        while not playback_done.wait(0.02):
            if control.cancelled.is_set():
                stream.abort()
                return
        if self._clear_request(control):
            self._emit({"event": "finished", "request_id": control.request_id})

    def _is_current(self, control: _RequestControl) -> bool:
        with self._lock:
            return self._is_current_locked(control)

    def _is_current_locked(self, control: _RequestControl) -> bool:
        return self._current is control and not self._closed and not control.cancelled.is_set()

    def _clear_request(self, control: _RequestControl) -> bool:
        with self._lock:
            if self._current is not control or control.cancelled.is_set():
                return False
            self._current = None
            return True

    @staticmethod
    def _request_cancel(control: _RequestControl | None) -> None:
        if control is not None:
            control.cancelled.set()

    def _invalidate_current_locked(self) -> _RequestControl | None:
        control = self._current
        self._current = None
        return control

    @staticmethod
    def _close_stream(stream: Any) -> None:
        try:
            stream.close()
        except Exception:
            pass

    def _discard_playback_thread(self) -> None:
        with self._lock:
            self._playback_threads.discard(threading.current_thread())

    def _emit_error(self, request_id: int | None, message: str) -> None:
        event: dict[str, Any] = {"event": "error", "message": message}
        if request_id is not None:
            event["request_id"] = request_id
        self._emit(event)


def _prepare_audio_blocks(pcm: bytes) -> tuple[tuple[memoryview, ...], memoryview]:
    block_size = 1024 * 4
    padding = (-len(pcm)) % block_size
    padded_view = memoryview(pcm + bytes(padding))
    blocks = tuple(
        padded_view[offset : offset + block_size]
        for offset in range(0, len(padded_view), block_size)
    )
    return blocks, memoryview(bytes(block_size))


def _write_emitter(output_stream: TextIO) -> Callable[[dict[str, Any]], None]:
    lock = threading.Lock()

    def emit(event: dict[str, Any]) -> None:
        with lock:
            output_stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            output_stream.flush()

    return emit


def _isolate_protocol_output() -> TextIO:
    sys.stdout.flush()
    protocol_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    return os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1)


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
    protocol_stream = _isolate_protocol_output()
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
        try:
            paths = resolve_offline_speech_paths(arguments.bundle_root)
        except ValueError as error:
            emitter = _write_emitter(protocol_stream)
            emitter({"event": "error", "message": str(error)})
            return 1
        return run_protocol(paths, sys.stdin, protocol_stream)
    finally:
        protocol_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
