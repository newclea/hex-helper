from __future__ import annotations

import argparse
import io
import json
import queue
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from scripts.phase3 import realtime_recommendation as bridge
from tests.phase3_realtime.test_realtime_recommendation import (
    FakeWorker,
    OFFER_IDS,
    game_state,
    selection_observed,
)


class OneShotFailureWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    def recommend(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.failures:
            self.failures -= 1
            self.calls.append(dict(request))
            raise TimeoutError("injected transient timeout")
        return super().recommend(request)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeVisionProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = None
        self.stderr = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise bridge.subprocess.TimeoutExpired("vision", timeout)
        return self.returncode


def live_args() -> argparse.Namespace:
    return argparse.Namespace(replay=None)


def cpp_stale_health() -> dict[str, Any]:
    return {
        "type": "capture_health",
        "status": "DESKTOP_FALLBACK",
        "state": "DESKTOP_FALLBACK",
        "reason": "wgc_stalled",
        "recommendation_freshness": "stale",
        "recommendation_invalidated": True,
        "active_capture_state": "DESKTOP_FALLBACK",
        "event_seq": 17,
        "capture": {
            "state": "desktop_fallback",
            "selected_source": "desktop",
        },
    }


class TransactionalOfferTests(unittest.TestCase):
    def test_worker_transient_failure_does_not_commit_seen_offer(self) -> None:
        worker = OneShotFailureWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        payload = game_state(1, OFFER_IDS[0])

        failed = coordinator.handle_game_state(payload)
        self.assertEqual(failed["error"]["code"], "worker_ipc_error")
        self.assertEqual(coordinator.state.seen_offers, set())

        recovered = coordinator.handle_game_state(payload)
        self.assertEqual(recovered["status"], "recommended")
        self.assertEqual(len(worker.calls), 2)
        self.assertEqual(
            coordinator.state.seen_offers,
            {recovered["offer_id"]},
        )

    def test_stage_mismatch_does_not_poison_later_valid_offer(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        premature = game_state(2, OFFER_IDS[1])

        failed = coordinator.handle_game_state(premature)
        self.assertEqual(failed["error"]["code"], "stage_sequence_invalid")
        self.assertEqual(coordinator.state.seen_offers, set())

        coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        coordinator.confirm_selection(1)
        accepted = coordinator.handle_game_state(premature)
        self.assertEqual(accepted["status"], "recommended")

    def test_unconfirmed_new_offer_is_quarantined_not_seen(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        first = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        second_payload = game_state(2, OFFER_IDS[1])
        rejected = coordinator.handle_game_state(second_payload)

        second_id = bridge._parse_offer(second_payload, "阿狸").fingerprint
        self.assertEqual(rejected["error"]["code"], "selection_unconfirmed")
        self.assertNotIn(second_id, coordinator.state.seen_offers)
        self.assertTrue(coordinator.state.reconcile_required)

        coordinator.confirm_selection(
            1,
            offer_identity=first["offer_id"],
            basis="manual_reconcile",
        )
        accepted = coordinator.handle_game_state(second_payload)
        self.assertEqual(accepted["status"], "recommended")

    def test_cpp_fallback_restores_same_offer_without_worker_reentry(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        payload = game_state(1, OFFER_IDS[0])
        recommendation = coordinator.handle_game_state(payload)

        invalidation = coordinator.apply_capture_health(cpp_stale_health())
        self.assertEqual(invalidation["status"], "stale")
        self.assertTrue(invalidation["preserved_recommendation"])
        self.assertTrue(invalidation["awaiting_offer_revalidation"])
        self.assertEqual(
            invalidation["recommended"], recommendation["recommended"]
        )
        self.assertEqual(invalidation["ranking"], recommendation["ranking"])
        self.assertEqual(invalidation["recommendation_freshness"], "stale")
        self.assertFalse(invalidation["hotkeys_enabled"])
        self.assertIsNone(coordinator.active_offer_identity)
        self.assertEqual(coordinator.state.current_offer.freshness, "stale")

        restored = coordinator.handle_game_state(payload)
        self.assertEqual(restored["type"], "recommendation")
        self.assertEqual(restored["offer_id"], recommendation["offer_id"])
        self.assertTrue(restored["restored"])
        self.assertTrue(restored["restored_after_capture_fallback"])
        self.assertFalse(restored["restored_after_vision_restart"])
        self.assertTrue(restored["hotkeys_enabled"])
        self.assertEqual(len(worker.calls), 1)

    def test_different_offer_after_cpp_fallback_remains_quarantined(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        coordinator.apply_capture_health(cpp_stale_health())

        rejected = coordinator.handle_game_state(game_state(2, OFFER_IDS[1]))
        self.assertEqual(rejected["error"]["code"], "selection_unconfirmed")
        self.assertTrue(rejected["reconcile_required"])
        self.assertIsNone(coordinator.active_offer_identity)
        self.assertEqual(len(worker.calls), 1)

    def test_every_cpp_stale_signal_transactionally_disables_binding(self) -> None:
        health_cases = [
            {"state": "HEALTHY", "recommendation_freshness": "stale"},
            {"state": "HEALTHY", "recommendation_invalidated": True},
            {},
            {"state": None, "recommendation_freshness": "current"},
            {"state": "UNKNOWN", "recommendation_freshness": "current"},
            {"state": "FUTURE_STATE", "recommendation_freshness": "current"},
            *[
                {"state": state, "recommendation_freshness": "current"}
                for state in ("RESTARTING", "SUSPECT", "STOPPED")
            ],
        ]
        for health in health_cases:
            with self.subTest(health=health):
                coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
                coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
                event = coordinator.apply_capture_health(health)
                self.assertIsNotNone(event)
                self.assertEqual(event["status"], "stale")
                self.assertIsNone(coordinator.active_offer_identity)
                self.assertEqual(coordinator.state.current_offer.freshness, "stale")
                self.assertIsNone(coordinator.apply_capture_health(health))

    def test_explicit_current_wgc_fallback_does_not_invalidate_binding(self) -> None:
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        recommendation = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        health = {
            "type": "capture_health",
            "state": "DEGRADED",
            "reason": "desktop_unavailable_wgc_fresh",
            "recommendation_freshness": "current",
            "recommendation_invalidated": False,
            "capture": {
                "selected_source": "wgc",
                "selected_epoch": 2,
            },
        }

        self.assertIsNone(coordinator.apply_capture_health(health))
        self.assertEqual(
            coordinator.active_offer_identity, recommendation["offer_id"]
        )
        self.assertFalse(coordinator.state.vision_suspended)

    def test_current_health_alone_does_not_revalidate_stale_offer_slots(self) -> None:
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        recommendation = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        coordinator.apply_capture_health(cpp_stale_health())

        recovered_health = {
            "type": "capture_health",
            "state": "HEALTHY",
            "reason": "desktop_authoritative",
            "recommendation_freshness": "current",
            "recommendation_invalidated": False,
        }
        self.assertIsNone(coordinator.apply_capture_health(recovered_health))
        self.assertIsNone(coordinator.active_offer_identity)
        self.assertTrue(coordinator.state.vision_suspended)

        restored = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(restored["offer_id"], recommendation["offer_id"])
        self.assertTrue(restored["restored"])

    def test_stale_selection_observation_cannot_mutate_next_pending_offer(self) -> None:
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        first = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        first_observation = selection_observed(
            first["offer_id"], "CENTER", OFFER_IDS[0][1]
        )
        coordinator.handle_selection_observed(first_observation)
        second = coordinator.handle_game_state(game_state(2, OFFER_IDS[1]))

        stale = coordinator.handle_selection_observed(first_observation)[0]

        self.assertEqual(stale["status"], "fail_closed")
        self.assertEqual(stale["error"]["code"], "stale_selection_observation")
        self.assertEqual(stale["offer_id"], second["offer_id"])
        self.assertEqual(stale["confirmed_selection"]["offer_id"], first["offer_id"])
        self.assertEqual(coordinator.state.owned_augments, [OFFER_IDS[0][1]])
        self.assertEqual(coordinator.state.current_offer.fingerprint, second["offer_id"])

    def test_selection_observed_feeds_exact_choice_into_next_round_combo(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        first = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        coordinator.handle_selection_observed(
            selection_observed(first["offer_id"], "RIGHT", OFFER_IDS[0][2])
        )

        second = coordinator.handle_game_state(game_state(2, OFFER_IDS[1]))

        self.assertEqual(second["status"], "recommended")
        self.assertEqual(worker.calls[-1]["owned_augments"], [OFFER_IDS[0][2]])
        confirmation = coordinator.handle_selection_observed(
            selection_observed(second["offer_id"], "CENTER", OFFER_IDS[1][1])
        )[0]
        self.assertEqual(confirmation["status"], "choice_confirmed")
        self.assertEqual(
            coordinator.state.owned_augments,
            [OFFER_IDS[0][2], OFFER_IDS[1][1]],
        )


class VisionGenerationSupervisorTests(unittest.TestCase):
    def make_supervisor(self) -> tuple[
        bridge.VisionGenerationSupervisor,
        list[FakeVisionProcess],
        list[dict[str, Any]],
        bridge.RecommendationCoordinator,
        FakeClock,
    ]:
        processes: list[FakeVisionProcess] = []
        emitted: list[dict[str, Any]] = []
        clock = FakeClock()
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())

        def factory(_: argparse.Namespace) -> FakeVisionProcess:
            process = FakeVisionProcess()
            processes.append(process)
            return process

        def invalidate(reason: str) -> None:
            event = coordinator.invalidate_vision(reason)
            if event is not None:
                emitted.append(event)

        supervisor = bridge.VisionGenerationSupervisor(
            live_args(),
            emit=lambda event: emitted.append(dict(event)),
            on_restart=invalidate,
            stage_offset=lambda: len(coordinator.state.owned_augments),
            process_factory=factory,  # type: ignore[arg-type]
            clock=clock,
            reader_starter=lambda *_: None,
        )
        return supervisor, processes, emitted, coordinator, clock

    def test_hard_exit_restarts_generation_and_preserves_coordinator(self) -> None:
        supervisor, processes, emitted, coordinator, _ = self.make_supervisor()
        supervisor.start()
        recommendation = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        pending = coordinator.state.current_offer
        session_id = coordinator.state.session_id

        processes[0].returncode = 9
        supervisor.tick()

        self.assertEqual(supervisor.generation, 2)
        self.assertIs(coordinator.state.current_offer, pending)
        self.assertEqual(coordinator.state.session_id, session_id)
        self.assertEqual(recommendation["offer_id"], pending.fingerprint)
        self.assertTrue(coordinator.state.vision_suspended)
        self.assertTrue(
            any(event.get("type") == "recommendation_invalidated" for event in emitted)
        )
        restored = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(restored["type"], "recommendation")
        self.assertTrue(restored["restored_after_vision_restart"])
        self.assertEqual(len(coordinator.worker.calls), 1)

    def test_restart_seeds_cpp_timing_from_confirmed_rounds(self) -> None:
        processes: list[FakeVisionProcess] = []
        completed_seeds: list[int] = []
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())

        def factory(args: argparse.Namespace) -> FakeVisionProcess:
            completed_seeds.append(args.vision_completed_offers)
            process = FakeVisionProcess()
            processes.append(process)
            return process

        supervisor = bridge.VisionGenerationSupervisor(
            live_args(),
            emit=lambda _: None,
            on_restart=lambda reason: coordinator.invalidate_vision(reason),
            stage_offset=lambda: len(coordinator.state.owned_augments),
            process_factory=factory,  # type: ignore[arg-type]
            reader_starter=lambda *_: None,
        )
        supervisor.start()
        coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        confirmed = coordinator.confirm_selection(1)
        self.assertEqual(confirmed[0]["status"], "choice_confirmed")

        processes[0].returncode = 9
        supervisor.tick()
        self.assertEqual(completed_seeds, [0, 1])
        self.assertEqual(supervisor.current_stage_offset, 1)

    def test_heartbeat_capability_uses_3_and_8_second_live_watchdog(self) -> None:
        supervisor, processes, emitted, coordinator, clock = self.make_supervisor()
        supervisor.start()
        coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))

        clock.advance(5.0)
        supervisor.tick()
        self.assertEqual(supervisor.state, "HEALTHY")
        self.assertEqual(supervisor.generation, 1)
        self.assertFalse(supervisor.heartbeat_capable)

        supervisor.accept(
            bridge.VisionStreamEvent(
                1,
                "line",
                json.dumps(
                    {
                        "type": "capture_health",
                        "event_seq": 41,
                        "state": "HEALTHY",
                    }
                ),
            )
        )
        self.assertTrue(supervisor.heartbeat_capable)
        self.assertEqual(supervisor.last_heartbeat_event_seq, 41)
        self.assertEqual(supervisor.last_heartbeat, 5.0)

        clock.advance(2.999)
        supervisor.tick()
        self.assertEqual(supervisor.state, "HEALTHY")
        self.assertIsNotNone(coordinator.active_offer_identity)

        clock.advance(0.001)
        supervisor.tick()
        self.assertEqual(supervisor.state, "SUSPECT")
        self.assertIsNone(coordinator.active_offer_identity)
        self.assertTrue(coordinator.state.vision_suspended)
        self.assertTrue(
            any(
                event.get("type") == "capture_health"
                and event.get("state") == "SUSPECT"
                for event in emitted
            )
        )
        ignored = supervisor.accept(
            bridge.VisionStreamEvent(1, "line", json.dumps(game_state(1, OFFER_IDS[0])))
        )
        self.assertIsNone(ignored)
        self.assertIsNone(coordinator.active_offer_identity)

        clock.advance(5.0)
        supervisor.tick()
        self.assertEqual(supervisor.state, "RESTARTING")
        self.assertEqual(supervisor.generation, 1)
        self.assertTrue(processes[0].terminated)

        supervisor.tick()
        self.assertEqual(supervisor.generation, 2)
        self.assertEqual(supervisor.state, "HEALTHY")
        self.assertFalse(supervisor.heartbeat_capable)

    def test_replay_never_restarts_from_heartbeat_silence(self) -> None:
        process = FakeVisionProcess()
        clock = FakeClock()
        invalidations: list[str] = []
        supervisor = bridge.VisionGenerationSupervisor(
            argparse.Namespace(replay=Path("offer.png")),
            emit=lambda _: None,
            on_restart=invalidations.append,
            stage_offset=lambda: 0,
            process_factory=lambda _: process,  # type: ignore[arg-type]
            clock=clock,
            reader_starter=lambda *_: None,
        )
        supervisor.start()
        supervisor.accept(
            bridge.VisionStreamEvent(
                1, "line", json.dumps({"type": "capture_health", "event_seq": 1})
            )
        )
        clock.advance(2.0)
        supervisor.tick()
        self.assertEqual(supervisor.state, "HEALTHY")
        self.assertEqual(supervisor.generation, 1)
        self.assertFalse(process.terminated)
        self.assertEqual(invalidations, [])

    def test_heartbeat_recovery_rearms_watchdog_in_same_generation(self) -> None:
        supervisor, _, _, coordinator, clock = self.make_supervisor()
        supervisor.start()
        payload = game_state(1, OFFER_IDS[0])
        coordinator.handle_game_state(payload)
        supervisor.accept(
            bridge.VisionStreamEvent(
                1, "line", json.dumps({"type": "capture_health", "event_seq": 1})
            )
        )
        clock.advance(3.0)
        supervisor.tick()
        self.assertEqual(supervisor.state, "SUSPECT")

        supervisor.accept(
            bridge.VisionStreamEvent(
                1, "line", json.dumps({"type": "capture_health", "event_seq": 2})
            )
        )
        self.assertEqual(supervisor.state, "HEALTHY")
        self.assertEqual(supervisor.last_heartbeat_event_seq, 2)
        restored = coordinator.handle_game_state(payload)
        self.assertTrue(restored["restored_after_vision_restart"])

        clock.advance(3.0)
        supervisor.tick()
        self.assertEqual(supervisor.state, "SUSPECT")
        self.assertIsNone(coordinator.active_offer_identity)

    def test_eof_uses_backoff_and_old_generation_is_quarantined(self) -> None:
        supervisor, processes, emitted, coordinator, clock = self.make_supervisor()
        supervisor.start()
        coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))

        supervisor.accept(bridge.VisionStreamEvent(1, "eof"))
        supervisor.tick()
        self.assertEqual(supervisor.generation, 2)

        supervisor.accept(bridge.VisionStreamEvent(2, "eof"))
        supervisor.tick()
        self.assertEqual(supervisor.generation, 2)
        clock.advance(0.250)
        supervisor.tick()
        self.assertEqual(supervisor.generation, 3)

        old_line = supervisor.accept(
            bridge.VisionStreamEvent(1, "line", '{"champion":"污染"}\n')
        )
        self.assertIsNone(old_line)
        self.assertEqual(coordinator.state.hero, "阿狸")
        restart_events = [
            event for event in emitted if event.get("type") == "component_restart"
        ]
        self.assertEqual([event["backoff_ms"] for event in restart_events], [0, 250])
        self.assertTrue(
            any(
                event.get("error", {}).get("code") == "old_vision_generation"
                for event in emitted
            )
        )

    def test_restart_budget_is_bounded_and_enters_degraded_fallback(self) -> None:
        supervisor, _, emitted, coordinator, clock = self.make_supervisor()
        supervisor.start()
        coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        session_id = coordinator.state.session_id

        for backoff in (0.0, 0.250, 1.000):
            supervisor.accept(
                bridge.VisionStreamEvent(supervisor.generation, "eof")
            )
            clock.advance(backoff)
            supervisor.tick()

        supervisor.accept(
            bridge.VisionStreamEvent(supervisor.generation, "eof")
        )

        self.assertTrue(supervisor.finished)
        self.assertTrue(supervisor.degraded)
        self.assertEqual(coordinator.state.session_id, session_id)
        self.assertIsNotNone(coordinator.state.current_offer)
        restart_events = [
            event for event in emitted if event.get("type") == "component_restart"
        ]
        self.assertEqual(
            [event["backoff_ms"] for event in restart_events],
            [0, 250, 1000],
        )
        self.assertTrue(
            any(
                event.get("type") == "fallback_changed"
                and event.get("state") == "DEGRADED"
                for event in emitted
            )
        )

    def test_replay_eof_waits_for_exit_without_restarting(self) -> None:
        processes: list[FakeVisionProcess] = []
        emitted: list[dict[str, Any]] = []

        def factory(_: argparse.Namespace) -> FakeVisionProcess:
            process = FakeVisionProcess()
            processes.append(process)
            return process

        supervisor = bridge.VisionGenerationSupervisor(
            argparse.Namespace(replay=Path("offer.png")),
            emit=lambda event: emitted.append(dict(event)),
            on_restart=lambda _: self.fail("Replay must not restart"),
            stage_offset=lambda: 0,
            process_factory=factory,  # type: ignore[arg-type]
            reader_starter=lambda *_: None,
        )
        supervisor.start()
        supervisor.accept(bridge.VisionStreamEvent(1, "eof"))
        self.assertFalse(supervisor.finished)
        self.assertEqual(supervisor.state, "STOPPING")

        processes[0].returncode = 0
        supervisor.tick()
        self.assertTrue(supervisor.finished)
        self.assertEqual(supervisor.last_exit_code, 0)
        self.assertEqual(supervisor.generation, 1)
        self.assertFalse(
            any(event.get("type") == "component_restart" for event in emitted)
        )


class _WorkerPipe:
    def __init__(self) -> None:
        self.closed = False
        self.writes: list[str] = []

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _RecommendationProcess:
    def __init__(self) -> None:
        self.stdin = _WorkerPipe()
        self.stdout = None
        self.stderr = None
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise bridge.subprocess.TimeoutExpired("worker", timeout)
        return self.returncode


class WorkerGenerationIsolationTests(unittest.TestCase):
    @staticmethod
    def make_client(
        process: _RecommendationProcess,
        responses: queue.Queue[str | BaseException | None],
    ) -> bridge.RecommendationWorkerClient:
        client = object.__new__(bridge.RecommendationWorkerClient)
        client._timeout = 0.001
        client._responses = responses
        client._process = process
        client._command = ["fake-worker"]
        client._cwd = Path(".")
        client._generation = 1
        client._ready = dict(FakeWorker().ready)
        client._faulted = False
        client._fault_reason = None
        client._closed = False
        return client

    @staticmethod
    def install_fresh_generation(
        client: bridge.RecommendationWorkerClient,
        process: _RecommendationProcess,
        response: Mapping[str, Any] | None = None,
    ) -> None:
        ready = dict(FakeWorker().ready)
        bridge.RecommendationWorkerClient._validate_ready(ready)
        client._generation += 1
        client._process = process
        client._responses = queue.Queue()
        if response is not None:
            client._responses.put(json.dumps(response))
        client._ready = ready
        client._faulted = False
        client._fault_reason = None

    def test_timeout_detaches_old_fifo_before_next_request(self) -> None:
        old_process = _RecommendationProcess()
        old_responses: queue.Queue[str | BaseException | None] = queue.Queue()
        client = self.make_client(old_process, old_responses)
        with self.assertRaises(TimeoutError):
            client._read_message()
        old_responses.put(
            json.dumps(
                {
                    "type": "recommendation_response",
                    "request_id": "old-request",
                    "ok": False,
                }
            )
        )

        fresh_process = _RecommendationProcess()
        matching = {
            "type": "recommendation_response",
            "request_id": "new-request",
            "ok": False,
        }
        with mock.patch.object(
            client,
            "_start_generation",
            side_effect=lambda: self.install_fresh_generation(
                client, fresh_process, matching
            ),
        ):
            response = client.recommend(
                {"type": "recommend", "request_id": "new-request"}
            )

        self.assertEqual(response["request_id"], "new-request")
        self.assertEqual(client.generation, 2)
        self.assertTrue(old_process.terminated)
        self.assertIs(client._process, fresh_process)
        self.assertEqual(old_responses.qsize(), 1)

    def test_protocol_desync_replaces_generation_without_cascade(self) -> None:
        old_process = _RecommendationProcess()
        responses: queue.Queue[str | BaseException | None] = queue.Queue()
        responses.put(
            json.dumps(
                {
                    "type": "recommendation_response",
                    "request_id": "late-old-request",
                    "ok": False,
                }
            )
        )
        client = self.make_client(old_process, responses)
        fresh_process = _RecommendationProcess()
        with mock.patch.object(
            client,
            "_start_generation",
            side_effect=lambda: self.install_fresh_generation(client, fresh_process),
        ):
            isolated = client.recommend(
                {"type": "recommend", "request_id": "request-a"}
            )
            self.assertEqual(isolated["request_id"], "request-a")
            self.assertEqual(
                isolated["error"]["code"], "worker_protocol_desync"
            )
            client._responses.put(
                json.dumps(
                    {
                        "type": "recommendation_response",
                        "request_id": "request-b",
                        "ok": False,
                    }
                )
            )
            current = client.recommend(
                {"type": "recommend", "request_id": "request-b"}
            )
        self.assertEqual(current["request_id"], "request-b")
        self.assertEqual(client.generation, 2)


class _QueueRunSupervisor:
    latest: "_QueueRunSupervisor | None" = None

    def __init__(self, *_: object, **__: object) -> None:
        type(self).latest = self
        self.events: queue.Queue[bridge.VisionStreamEvent] = queue.Queue()
        self.events.put(
            bridge.VisionStreamEvent(
                1, "line", json.dumps(game_state(1, OFFER_IDS[0]))
            )
        )
        self.finished = False
        self.degraded = False
        self.last_exit_code = None
        self.generation = 1
        self.current_stage_offset = 0
        self.state = "HEALTHY"
        self.ticks = 0

    def start(self) -> None:
        return None

    def tick(self) -> None:
        self.ticks += 1
        if self.ticks >= 4 and self.events.empty():
            self.finished = True

    def accept(self, event: bridge.VisionStreamEvent) -> str | None:
        return event.value if event.kind == "line" and isinstance(event.value, str) else None

    def close(self) -> None:
        return None


class _HealthDuringWorker(FakeWorker):
    def recommend(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        supervisor = _QueueRunSupervisor.latest
        assert supervisor is not None
        supervisor.events.put(
            bridge.VisionStreamEvent(1, "line", json.dumps(cpp_stale_health()))
        )
        return super().recommend(request)

    def close(self) -> None:
        return None


class _FirstPollHotkey:
    def __init__(self) -> None:
        self.polled = False

    def poll(self) -> int | None:
        if self.polled:
            return None
        self.polled = True
        return 1


class RunOrderingTests(unittest.TestCase):
    def test_health_accumulated_during_worker_is_drained_before_hotkey(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="phase5-run-ordering-", dir=bridge.WORKSPACE_ROOT / "outputs" / "tmp"
        ) as directory:
            log_path = Path(directory) / "session.jsonl"
            args = argparse.Namespace(
                replay=None,
                log_path=log_path,
                runtime_root=Path(directory),
                python=Path("python.exe"),
                scrape_root=Path("Scrape"),
                visual_catalog=Path("catalog.json"),
                worker_timeout=0.1,
                hero="阿狸",
                resume_owned=[],
                no_hotkeys=False,
                no_sidecar=True,
                vision_debug=False,
                max_seconds=1.0,
            )
            original_sink = bridge.EventSink
            quiet_stdout = io.StringIO()
            with (
                mock.patch.object(
                    bridge,
                    "EventSink",
                    side_effect=lambda path, **kwargs: original_sink(
                        path, stdout=quiet_stdout, **kwargs
                    ),
                ),
                mock.patch.object(
                    bridge,
                    "RecommendationWorkerClient",
                    return_value=_HealthDuringWorker(),
                ),
                mock.patch.object(
                    bridge, "VisionGenerationSupervisor", _QueueRunSupervisor
                ),
                mock.patch.object(bridge, "HotkeyPoller", _FirstPollHotkey),
            ):
                self.assertEqual(bridge.run(args), 0)

            events = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
        confirmed = [
            event
            for event in events
            if event.get("type") == "choice_confirmation"
            and event.get("status") == "choice_confirmed"
        ]
        self.assertEqual(confirmed, [])
        self.assertTrue(
            any(
                event.get("type") == "recommendation_invalidated"
                and event.get("invalidation_source") == "cpp_capture_health"
                for event in events
            )
        )
        self.assertEqual(events[-1]["confirmed_rounds"], 0)


if __name__ == "__main__":
    unittest.main()
