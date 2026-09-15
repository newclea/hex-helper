from __future__ import annotations

import unittest

from scripts.phase4 import sidecar_window as sidecar


def active_recommendation() -> dict[str, object]:
    return {
        "type": "recommendation",
        "status": "recommended",
        "hero": "阿狸",
        "stage": 2,
        "choices": ["左", "中", "右"],
        "recommended": "中",
        "offer_id": "offer-current",
        "source": {"kind": "screen_capture"},
    }


class SidecarHealthContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.active = sidecar.reduce_event(None, active_recommendation())
        assert self.active is not None
        self.assertEqual(self.active["recommended"], "中")
        self.assertTrue(self.active["hotkeys_enabled"])

    def test_stale_invalidation_retains_recommendation_but_disables_hotkeys(self) -> None:
        model = sidecar.reduce_event(
            self.active,
            {
                "type": "recommendation_invalidated",
                "status": "stale",
                "offer_id": "offer-current",
                "reason": "stdout_eof",
            },
        )
        self.assertEqual(model["recommended"], "中")
        self.assertEqual(len(model["choices"]), 3)
        self.assertEqual(model["active_offer_id"], "offer-current")
        self.assertFalse(model["hotkeys_enabled"])
        self.assertEqual(model["recommendation_freshness"], "stale")
        self.assertIn("推荐时效: STALE", model["render_text"])
        self.assertNotIn("旧推荐已清除", model["render_text"])

    def test_delayed_invalidation_for_old_offer_is_ignored(self) -> None:
        model = sidecar.reduce_event(
            self.active,
            {
                "type": "recommendation_invalidated",
                "status": "stale",
                "offer_id": "offer-old",
                "reason": "late_health_event",
            },
        )
        self.assertEqual(model["recommended"], "中")
        self.assertEqual(model["recommendation_freshness"], "current")
        self.assertTrue(model["hotkeys_enabled"])

    def test_restarting_clears_visible_decision(self) -> None:
        model = sidecar.reduce_event(
            self.active,
            {
                "type": "component_restart",
                "status": "RESTARTING",
                "component": "vision",
                "phase": "scheduled",
                "reason": "process_exit",
            },
        )
        self.assertEqual(model["health_state"], "RESTARTING")
        self.assertIsNone(model["recommended"])
        self.assertEqual(model["choices"], [])
        self.assertIn("RESTARTING", model["render_text"])

    def test_desktop_fallback_and_degraded_both_clear_old_recommendation(self) -> None:
        for state in ("DESKTOP_FALLBACK", "DEGRADED"):
            with self.subTest(state=state):
                model = sidecar.reduce_event(
                    self.active,
                    {
                        "type": "fallback_changed",
                        "status": state,
                        "state": state,
                        "mode": "desktop" if state == "DESKTOP_FALLBACK" else "NO_SAFE_RECOMMENDATION",
                        "reason": "capture_unavailable",
                    },
                )
                self.assertEqual(model["health_state"], state)
                self.assertIsNone(model["recommended"])
                self.assertEqual(model["choices"], [])
                self.assertFalse(model["hotkeys_enabled"])

    def test_healthy_additive_event_does_not_destroy_current_decision(self) -> None:
        model = sidecar.reduce_event(
            self.active,
            {
                "type": "capture_health",
                "status": "HEALTHY",
                "state": "HEALTHY",
                "reason": "process_spawned",
            },
        )
        self.assertEqual(model["health_state"], "HEALTHY")
        self.assertEqual(model["recommended"], "中")
        self.assertEqual(len(model["choices"]), 3)

    def test_capture_health_desktop_fallback_also_clears_old_decision(self) -> None:
        model = sidecar.reduce_event(
            self.active,
            {
                "type": "capture_health",
                "status": "DESKTOP_FALLBACK",
                "state": "DESKTOP_FALLBACK",
                "reason": "wgc_suspect",
            },
        )
        self.assertEqual(model["health_state"], "DESKTOP_FALLBACK")
        self.assertEqual(model["status"], "desktop_fallback")
        self.assertIsNone(model["recommended"])
        self.assertFalse(model["hotkeys_enabled"])

    def test_periodic_health_keeps_stale_until_offer_scoped_restore(self) -> None:
        fallback = sidecar.reduce_event(
            self.active,
            {
                "type": "capture_health",
                "status": "DESKTOP_FALLBACK",
                "state": "DESKTOP_FALLBACK",
                "reason": "wgc_stalled",
                "recommendation_freshness": "stale",
                "recommendation_invalidated": True,
                "active_capture_state": "DESKTOP_FALLBACK",
            },
        )
        self.assertEqual(fallback["recommended"], "中")
        self.assertEqual(fallback["recommendation_freshness"], "stale")
        self.assertFalse(fallback["hotkeys_enabled"])

        periodic = sidecar.reduce_event(
            fallback,
            {
                "type": "capture_health",
                "status": "HEALTHY",
                "state": "HEALTHY",
                "reason": "desktop_fresh",
                "recommendation_freshness": "current",
                "recommendation_invalidated": False,
                "active_capture_state": "DESKTOP_FALLBACK",
                "capture": {"state": "desktop_fallback"},
            },
        )
        self.assertEqual(periodic["health_state"], "HEALTHY")
        self.assertEqual(periodic["active_capture_state"], "DESKTOP_FALLBACK")
        self.assertEqual(periodic["recommended"], "中")
        self.assertEqual(periodic["recommendation_freshness"], "stale")
        self.assertFalse(periodic["hotkeys_enabled"])
        self.assertIn("当前捕获状态: DESKTOP_FALLBACK", periodic["render_text"])

        repeated_fallback = sidecar.reduce_event(
            periodic,
            {
                "type": "capture_health",
                "status": "DESKTOP_FALLBACK",
                "state": "DESKTOP_FALLBACK",
                "reason": "desktop_fresh",
                "recommendation_freshness": "current",
                "recommendation_invalidated": False,
                "active_capture_state": "DESKTOP_FALLBACK",
            },
        )
        self.assertEqual(repeated_fallback["recommended"], "中")
        self.assertEqual(repeated_fallback["recommendation_freshness"], "stale")
        self.assertFalse(repeated_fallback["hotkeys_enabled"])

    def test_real_suspect_fallback_healthy_sequence_requires_restored_recommendation(self) -> None:
        suspect = sidecar.reduce_event(
            self.active,
            {
                "type": "capture_health",
                "status": "SUSPECT",
                "state": "SUSPECT",
                "reason": "no_fresh_source",
                "recommendation_freshness": "stale",
                "recommendation_invalidated": True,
                "active_capture_state": "SUSPECT",
            },
        )
        invalidated = sidecar.reduce_event(
            suspect,
            {
                "type": "recommendation_invalidated",
                "status": "stale",
                "offer_id": "offer-current",
                "reason": "capture_health:suspect:no_fresh_source",
            },
        )
        fallback = sidecar.reduce_event(
            invalidated,
            {
                "type": "capture_health",
                "status": "DESKTOP_FALLBACK",
                "state": "DESKTOP_FALLBACK",
                "reason": "desktop_authoritative",
                "recommendation_freshness": "stale",
                "recommendation_invalidated": True,
                "active_capture_state": "DESKTOP_FALLBACK",
            },
        )
        health_recovered = sidecar.reduce_event(
            fallback,
            {
                "type": "capture_health",
                "status": "HEALTHY",
                "state": "HEALTHY",
                "reason": "desktop_authoritative",
                "recommendation_freshness": "current",
                "recommendation_invalidated": False,
                "active_capture_state": "DESKTOP_FALLBACK",
            },
        )

        for stale in (suspect, invalidated, fallback):
            self.assertEqual(stale["recommended"], "中")
            self.assertEqual(len(stale["choices"]), 3)
            self.assertEqual(stale["recommendation_freshness"], "stale")
            self.assertFalse(stale["hotkeys_enabled"])
        self.assertEqual(health_recovered["recommended"], "中")
        self.assertEqual(health_recovered["recommendation_freshness"], "stale")
        self.assertFalse(health_recovered["hotkeys_enabled"])

        restored_event = active_recommendation()
        restored_event.update(
            {
                "restored": True,
                "recommendation_freshness": "current",
                "hotkeys_enabled": True,
            }
        )
        restored = sidecar.reduce_event(health_recovered, restored_event)
        self.assertEqual(restored["recommended"], "中")
        self.assertEqual(restored["recommendation_freshness"], "current")
        self.assertTrue(restored["hotkeys_enabled"])
        self.assertIn("推荐时效: CURRENT", restored["render_text"])

    def test_degraded_current_does_not_revalidate_cached_offer(self) -> None:
        stale = sidecar.reduce_event(
            self.active,
            {
                "type": "capture_health",
                "state": "DEGRADED",
                "reason": "no_fresh_source",
                "recommendation_freshness": "stale",
                "recommendation_invalidated": True,
            },
        )
        recovered = sidecar.reduce_event(
            stale,
            {
                "type": "capture_health",
                "state": "DEGRADED",
                "reason": "desktop_unavailable_wgc_fresh",
                "recommendation_freshness": "current",
                "recommendation_invalidated": False,
                "active_capture_state": "DEGRADED",
            },
        )
        self.assertEqual(recovered["health_state"], "DEGRADED")
        self.assertEqual(recovered["recommended"], "中")
        self.assertEqual(recovered["recommendation_freshness"], "stale")
        self.assertFalse(recovered["hotkeys_enabled"])

    def test_repeated_freshness_events_are_idempotent(self) -> None:
        stale_event = {
            "type": "capture_health",
            "state": "SUSPECT",
            "reason": "no_fresh_source",
            "recommendation_freshness": "stale",
            "recommendation_invalidated": True,
        }
        first_stale = sidecar.reduce_event(self.active, stale_event)
        second_stale = sidecar.reduce_event(first_stale, stale_event)
        self.assertEqual(second_stale["recommended"], "中")
        self.assertEqual(second_stale["recommendation_freshness"], "stale")

        current_event = {
            "type": "capture_health",
            "state": "HEALTHY",
            "reason": "desktop_authoritative",
            "recommendation_freshness": "current",
            "recommendation_invalidated": False,
        }
        first_current = sidecar.reduce_event(second_stale, current_event)
        second_current = sidecar.reduce_event(first_current, current_event)
        self.assertEqual(second_current["recommended"], "中")
        self.assertEqual(second_current["recommendation_freshness"], "stale")
        self.assertFalse(second_current["hotkeys_enabled"])


if __name__ == "__main__":
    unittest.main()
