"""Agent text generation contracts and the direct Taiji provider."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
import socket
import time
from typing import Callable, Mapping, Protocol
import urllib.error
import urllib.request
import uuid

from agent_config import AgentSettings
from scoped_debug import scoped_debug


LOGGER = logging.getLogger(__name__)
MAX_AGENT_TEXT_CHARACTERS = 240
MAX_AGENT_RESPONSE_BYTES = 1024 * 1024
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    text: str
    query_id: str
    error_code: str | None


@dataclass(frozen=True)
class AgentHttpRequest:
    endpoint: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float


@dataclass(frozen=True)
class AgentHttpResponse:
    status_code: int
    body: bytes = field(repr=False)


class AgentTransportFailure(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class AgentTextProvider(Protocol):
    def generate(
        self,
        prompt: str,
        context: Mapping[str, object] | None = None,
    ) -> AgentResult:
        ...


class AgentHttpTransport(Protocol):
    def post(self, request: AgentHttpRequest) -> AgentHttpResponse:
        ...


class UrllibAgentHttpTransport:
    def post(self, request: AgentHttpRequest) -> AgentHttpResponse:
        http_request = urllib.request.Request(
            request.endpoint,
            data=request.body,
            headers=dict(request.headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=request.timeout_seconds) as response:
                body = response.read(MAX_AGENT_RESPONSE_BYTES + 1)
                return AgentHttpResponse(response.getcode(), body)
        except urllib.error.HTTPError as error:
            return AgentHttpResponse(error.code, b"")
        except urllib.error.URLError as error:
            if _is_timeout(error.reason):
                raise AgentTransportFailure("timeout") from None
            raise AgentTransportFailure("network_error") from None
        except (socket.timeout, TimeoutError):
            raise AgentTransportFailure("timeout") from None
        except OSError:
            raise AgentTransportFailure("network_error") from None


class DisabledAgentTextProvider:
    def generate(
        self,
        prompt: str,
        context: Mapping[str, object] | None = None,
    ) -> AgentResult:
        del prompt, context
        return AgentResult(False, "", "", "not_configured")


class TaijiDirectAgentProvider:
    def __init__(
        self,
        settings: AgentSettings,
        transport: AgentHttpTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport or UrllibAgentHttpTransport()
        self._clock = clock

    def __repr__(self) -> str:
        return "<TaijiDirectAgentProvider>"

    def generate(
        self,
        prompt: str,
        context: Mapping[str, object] | None = None,
    ) -> AgentResult:
        query_id = uuid.uuid4().hex
        request = self._build_request(prompt, context, query_id)
        if request is None:
            return _failure(query_id, "invalid_request")
        started_at = self._clock()
        scoped_debug(
            "speech", "agent request started query_id=%s timeout_ms=%d",
            query_id, int(self._settings.timeout_seconds * 1000),
        )
        try:
            response = self._transport.post(request)
        except AgentTransportFailure as error:
            self._log_result(query_id, None, started_at, error.error_code)
            return _failure(query_id, error.error_code)
        error_code = _http_error(response.status_code)
        if error_code is not None:
            self._log_result(query_id, response.status_code, started_at, error_code)
            return _failure(query_id, error_code)
        result = _parse_response(response.body, query_id)
        self._log_result(query_id, response.status_code, started_at, result.error_code)
        return result

    def _build_request(
        self,
        prompt: str,
        context: Mapping[str, object] | None,
        query_id: str,
    ) -> AgentHttpRequest | None:
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        payload: dict[str, object] = {
            "query_id": query_id,
            "query": prompt,
            "forward_service": self._settings.forward_service,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            if context is not None:
                payload["context"] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return None
        headers = {
            "Authorization": f"Bearer {self._settings.token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        return AgentHttpRequest(
            self._settings.endpoint,
            headers,
            body,
            self._settings.timeout_seconds,
        )

    def _log_result(
        self,
        query_id: str,
        status_code: int | None,
        started_at: float,
        error_code: str | None,
    ) -> None:
        elapsed_ms = max(0, int((self._clock() - started_at) * 1000))
        log = LOGGER.warning if error_code is not None else LOGGER.info
        log(
            "agent request finished provider=taiji_direct query_id=%s status=%s "
            "elapsed_ms=%d error=%s",
            query_id,
            status_code,
            elapsed_ms,
            error_code or "none",
        )
        scoped_debug(
            "speech", "agent request finished query_id=%s status=%s elapsed_ms=%d error=%s",
            query_id, status_code, elapsed_ms, error_code or "none",
        )


def build_agent_provider(
    settings: AgentSettings,
    transport: AgentHttpTransport | None = None,
) -> AgentTextProvider:
    if not settings.configured:
        return DisabledAgentTextProvider()
    return TaijiDirectAgentProvider(settings, transport)


def _parse_response(body: bytes, query_id: str) -> AgentResult:
    if len(body) > MAX_AGENT_RESPONSE_BYTES:
        return _failure(query_id, "invalid_response")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _failure(query_id, "invalid_response")
    if not isinstance(payload, dict):
        return _failure(query_id, "invalid_response")
    retcode = payload.get("retcode")
    if not isinstance(retcode, int) or isinstance(retcode, bool) or retcode != 0:
        return _failure(query_id, "agent_error")
    raw_text = payload.get("result")
    if not isinstance(raw_text, str):
        return _failure(query_id, "empty_result")
    text = _clean_text(raw_text)
    if not text:
        return _failure(query_id, "empty_result")
    return AgentResult(True, text, query_id, None)


def _clean_text(value: str) -> str:
    without_controls = CONTROL_CHARACTERS.sub(" ", value)
    normalized = WHITESPACE.sub(" ", without_controls).strip()
    return normalized[:MAX_AGENT_TEXT_CHARACTERS]


def _http_error(status_code: int) -> str | None:
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code < 200 or status_code >= 300:
        return "http_error"
    return None


def _failure(query_id: str, error_code: str) -> AgentResult:
    return AgentResult(False, "", query_id, error_code)


def _is_timeout(reason: object) -> bool:
    return isinstance(reason, (socket.timeout, TimeoutError))
