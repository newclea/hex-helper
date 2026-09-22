from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from agent_text import AgentResult
import agent_probe


class AgentProbeTests(unittest.TestCase):
    def run_probe(self, provider: Mock, prompt: str = "只回复连接成功"):
        stdout = StringIO()
        stderr = StringIO()
        with patch("agent_probe.create_provider", return_value=provider):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = agent_probe.main(["--prompt", prompt])
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_probe_prints_safe_success_json(self) -> None:
        provider = Mock()
        provider.generate.return_value = AgentResult(
            ok=True,
            text="连接成功",
            query_id="query-1",
            error_code=None,
        )

        exit_code, payload, stderr = self.run_probe(provider)

        self.assertEqual(0, exit_code)
        self.assertEqual({
            "ok": True,
            "text": "连接成功",
            "query_id": "query-1",
            "error_code": None,
        }, payload)
        self.assertEqual("", stderr)
        provider.generate.assert_called_once_with("只回复连接成功")

    def test_probe_failure_uses_nonzero_exit_code(self) -> None:
        provider = Mock()
        provider.generate.return_value = AgentResult(
            ok=False,
            text="",
            query_id="query-2",
            error_code="authentication_error",
        )

        exit_code, payload, _ = self.run_probe(provider)

        self.assertEqual(1, exit_code)
        self.assertEqual("", payload["text"])
        self.assertEqual("authentication_error", payload["error_code"])

    def test_probe_reports_unconfigured_provider(self) -> None:
        provider = Mock()
        provider.generate.return_value = AgentResult(False, "", "", "not_configured")

        exit_code, payload, _ = self.run_probe(provider)

        self.assertEqual(1, exit_code)
        self.assertEqual("not_configured", payload["error_code"])

    def test_probe_hides_unexpected_exception_details(self) -> None:
        provider = Mock()
        provider.generate.side_effect = RuntimeError("secret-token")

        exit_code, payload, stderr = self.run_probe(provider)

        rendered = json.dumps(payload, ensure_ascii=False) + stderr
        self.assertEqual(1, exit_code)
        self.assertEqual("internal_error", payload["error_code"])
        self.assertNotIn("secret-token", rendered)


if __name__ == "__main__":
    unittest.main()
