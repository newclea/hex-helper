"""Parse local Agent provider settings without exposing credentials."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit


DEFAULT_AGENT_TIMEOUT_SECONDS = 10.0
MIN_AGENT_TIMEOUT_SECONDS = 1.0
MAX_AGENT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class AgentSettings:
    provider: str = ""
    endpoint: str = ""
    forward_service: str = ""
    token: str = field(default="", repr=False)
    timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return (
            self.provider == "taiji_direct"
            and _valid_endpoint(self.endpoint)
            and bool(self.forward_service)
            and bool(self.token)
        )


def parse_agent_settings(value: object) -> AgentSettings:
    if not isinstance(value, Mapping):
        return AgentSettings()
    provider = _required_string(value.get("provider"))
    endpoint = _required_string(value.get("endpoint"))
    forward_service = _required_string(value.get("forward_service"))
    token = _required_string(value.get("token"))
    timeout = _timeout(value.get("timeout_seconds", DEFAULT_AGENT_TIMEOUT_SECONDS))
    if None in (provider, endpoint, forward_service, token, timeout):
        return AgentSettings()
    if provider != "taiji_direct" or not _valid_endpoint(endpoint):
        return AgentSettings()
    return AgentSettings(provider, endpoint, forward_service, token, timeout)


def _required_string(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _timeout(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timeout = float(value)
    if not MIN_AGENT_TIMEOUT_SECONDS <= timeout <= MAX_AGENT_TIMEOUT_SECONDS:
        return None
    return timeout


def _valid_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return value == value.strip() and parsed.scheme in {"http", "https"} and bool(parsed.netloc)
