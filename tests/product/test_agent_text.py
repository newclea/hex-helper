from __future__ import annotations

import json
import unittest

from agent_config import AgentSettings
from agent_text import (
    AgentHttpResponse,
    AgentTransportFailure,
    MAX_AGENT_TEXT_CHARACTERS,
    TaijiDirectAgentProvider,
    build_agent_provider,
)


class FakeTransport:
    def __init__(
        self,
        response: AgentHttpResponse | None = None,
        failure: AgentTransportFailure | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.calls = []

    def post(self, request):
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        if self.response is None:
            raise AssertionError("fake response is required")
        return self.response


def settings(**changes: object) -> AgentSettings:
    values = {
        "provider": "taiji_direct",
        "endpoint": "http://agent.example/openapi/app_platform/app_create",
        "forward_service": "hyaide-application-22835",
        "token": "secret-token",
        "timeout_seconds": 10.0,
    }
    values.update(changes)
    return AgentSettings(**values)


class TaijiDirectAgentProviderTests(unittest.TestCase):
    def test_generate_builds_non_streaming_taiji_request(self) -> None:
        transport = FakeTransport(AgentHttpResponse(
            status_code=200,
            body=b'{"message":"","result":"  hello\\nworld  ","retcode":0}',
        ))
        provider = TaijiDirectAgentProvider(settings(), transport=transport)

        result = provider.generate("say hello", {"champion": "Ahri"})

        self.assertTrue(result.ok)
        self.assertEqual("hello world", result.text)
        request = transport.calls[0]
        self.assertEqual("Bearer secret-token", request.headers["Authorization"])
        self.assertEqual("application/json; charset=utf-8", request.headers["Content-Type"])
        payload = json.loads(request.body.decode("utf-8"))
        self.assertFalse(payload["stream"])
        self.assertEqual("hyaide-application-22835", payload["forward_service"])
        self.assertEqual("say hello", payload["query"])
        self.assertEqual("say hello", payload["messages"][0]["content"])
        self.assertEqual('{"champion":"Ahri"}', payload["context"])
        self.assertEqual(result.query_id, payload["query_id"])

    def test_each_generate_uses_a_unique_query_id(self) -> None:
        response = AgentHttpResponse(200, b'{"result":"ok","retcode":0}')
        transport = FakeTransport(response)
        provider = TaijiDirectAgentProvider(settings(), transport=transport)

        first = provider.generate("first")
        second = provider.generate("second")

        self.assertTrue(first.query_id)
        self.assertNotEqual(first.query_id, second.query_id)

    def test_invalid_prompt_does_not_call_transport(self) -> None:
        transport = FakeTransport()
        provider = TaijiDirectAgentProvider(settings(), transport=transport)

        result = provider.generate("  ")

        self.assertEqual("invalid_request", result.error_code)
        self.assertEqual([], transport.calls)

    def test_unencodable_context_does_not_call_transport(self) -> None:
        transport = FakeTransport()
        provider = TaijiDirectAgentProvider(settings(), transport=transport)

        result = provider.generate("hello", {"value": object()})

        self.assertEqual("invalid_request", result.error_code)
        self.assertEqual([], transport.calls)

    def test_http_statuses_are_classified(self) -> None:
        cases = ((401, "authentication_error"), (403, "authentication_error"), (500, "http_error"))
        for status_code, expected in cases:
            with self.subTest(status_code=status_code):
                transport = FakeTransport(AgentHttpResponse(status_code, b"ignored"))
                result = TaijiDirectAgentProvider(settings(), transport).generate("hello")
                self.assertEqual(expected, result.error_code)

    def test_transport_failures_are_classified(self) -> None:
        for error_code in ("timeout", "network_error"):
            with self.subTest(error_code=error_code):
                transport = FakeTransport(failure=AgentTransportFailure(error_code))
                result = TaijiDirectAgentProvider(settings(), transport).generate("hello")
                self.assertEqual(error_code, result.error_code)

    def test_invalid_responses_are_rejected(self) -> None:
        cases = (
            (b"\xff", "invalid_response"),
            (b"{not-json", "invalid_response"),
            (b"[]", "invalid_response"),
            (b'{"retcode":1,"result":"no"}', "agent_error"),
            (b'{"retcode":0}', "empty_result"),
            (b'{"retcode":0,"result":3}', "empty_result"),
            (b'{"retcode":0,"result":"  "}', "empty_result"),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                transport = FakeTransport(AgentHttpResponse(200, body))
                result = TaijiDirectAgentProvider(settings(), transport).generate("hello")
                self.assertEqual(expected, result.error_code)

    def test_result_is_cleaned_and_limited(self) -> None:
        raw = "  hello\x00\n\tworld  " + ("好" * MAX_AGENT_TEXT_CHARACTERS)
        body = json.dumps({"retcode": 0, "result": raw}).encode("utf-8")
        transport = FakeTransport(AgentHttpResponse(200, body))

        result = TaijiDirectAgentProvider(settings(), transport).generate("hello")

        self.assertTrue(result.ok)
        self.assertTrue(result.text.startswith("hello world 好"))
        self.assertEqual(MAX_AGENT_TEXT_CHARACTERS, len(result.text))

    def test_http_error_body_and_provider_repr_do_not_expose_token(self) -> None:
        transport = FakeTransport(AgentHttpResponse(500, b"Authorization: Bearer secret-token"))
        provider = TaijiDirectAgentProvider(settings(), transport)

        with self.assertLogs("agent_text", level="INFO") as captured:
            result = provider.generate("hello")

        self.assertFalse(result.ok)
        self.assertNotIn("secret-token", repr(provider))
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_unconfigured_provider_does_not_access_network(self) -> None:
        provider = build_agent_provider(AgentSettings())

        result = provider.generate("hello")

        self.assertFalse(result.ok)
        self.assertEqual("not_configured", result.error_code)


if __name__ == "__main__":
    unittest.main()
