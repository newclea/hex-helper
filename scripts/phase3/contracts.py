#!/usr/bin/env python3
"""Strict, standard-library-only contracts for Phase3 web knowledge data.

The module separates three concerns:

* strict JSON parsing (duplicate keys and non-finite numbers are rejected);
* record-level shape and semantic validation;
* bundle-level provenance, patch, fusion, and player-safety validation.

Callers should construct records through the ``validate_*`` functions or
``from_mapping`` class methods so invalid dataclass instances are never used.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
import json
import math
from pathlib import Path
import re
from typing import Any, NoReturn
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
UNKNOWN_PATCH = "UNKNOWN"
INTERNAL_INFERENCE_SOURCE_NAME = "INTERNAL_INFERENCE"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATCH_RE = re.compile(r"^[1-9][0-9]?\.[1-9][0-9]?[a-z]$")
_PATCH_WITHOUT_SUFFIX_RE = re.compile(r"^[1-9][0-9]?\.[1-9][0-9]?$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_FORBIDDEN_QUERY_KEY_PARTS = ("winrate", "pickrate")
_FORBIDDEN_QUERY_KEYS = {"mustpick", "mustchoose", "mustselect"}
_FORBIDDEN_QUERY_PHRASES = (
    "必须选",
    "一定要选",
    "必选",
    "must pick",
    "must choose",
    "must select",
)


class ContractError(ValueError):
    """Raised when JSON or a Phase3 record violates the executable contract."""


class SourceType(str, Enum):
    """Allowed provenance classes for source records."""

    OFFICIAL = "OFFICIAL"
    STATISTICAL = "STATISTICAL"
    GUIDE = "GUIDE"
    COMMUNITY = "COMMUNITY"
    INFERENCE = "INFERENCE"


class ClaimType(str, Enum):
    """Atomic claim categories supported by Phase3."""

    ITEM_IMPACT = "ITEM_IMPACT"
    AUGMENT_SYNERGY = "AUGMENT_SYNERGY"


class FusionStatus(str, Enum):
    """How a knowledge record relates to its supporting claims."""

    SINGLE_SOURCE = "SINGLE_SOURCE"
    FUSED_CONSENSUS = "FUSED_CONSENSUS"
    CONFLICTED = "CONFLICTED"


def _fail(path: str, message: str) -> NoReturn:
    raise ContractError(f"{path}: {message}")


def _reject_json_constant(value: str) -> NoReturn:
    raise ContractError(f"JSON: non-finite number is forbidden: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ContractError(f"JSON: non-finite number is forbidden: {value}")
    return parsed


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"JSON: duplicate object key: {key!r}")
        result[key] = value
    return result


def _ensure_finite_tree(value: Any, path: str = "$") -> None:
    if type(value) is float and not math.isfinite(value):
        _fail(path, "non-finite number is forbidden")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _ensure_finite_tree(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            _ensure_finite_tree(child, f"{path}[{index}]")


def strict_json_loads(text: str | bytes | bytearray) -> Any:
    """Parse one JSON document while rejecting duplicates and non-finite numbers.

    Python's JSON decoder normally accepts ``NaN``/``Infinity`` and can turn a
    very large exponent such as ``1e999`` into infinity. Both paths are rejected.
    """

    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except ContractError:
        raise
    except (TypeError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"JSON: invalid document: {exc}") from exc
    _ensure_finite_tree(value)
    return value


def strict_json_load(path: str | Path) -> Any:
    """Read a UTF-8 JSON file and parse it with :func:`strict_json_loads`."""

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"JSON: cannot read {source}: {exc}") from exc
    return strict_json_loads(text)


def _require_object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    for key in value:
        if type(key) is not str:
            _fail(path, "object keys must be strings")
    return value


def _require_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    path: str,
) -> None:
    allowed = required | (optional or set())
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        _fail(path, f"missing required fields: {', '.join(missing)}")
    if unknown:
        _fail(path, f"unknown fields: {', '.join(unknown)}")


def _require_schema_version(value: Any, path: str) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        _fail(path, f"must be integer {SCHEMA_VERSION}")
    return value


def _require_text(value: Any, path: str, *, maximum: int) -> str:
    if type(value) is not str:
        _fail(path, "must be a string")
    if value != value.strip() or not value:
        _fail(path, "must be non-empty with no surrounding whitespace")
    if len(value) > maximum:
        _fail(path, f"must contain at most {maximum} characters")
    return value


def _require_id(value: Any, path: str) -> str:
    text = _require_text(value, path, maximum=128)
    if _ID_RE.fullmatch(text) is None:
        _fail(path, "must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    return text


def _require_patch(value: Any, path: str) -> str:
    if value == UNKNOWN_PATCH:
        return UNKNOWN_PATCH
    if type(value) is not str or not (
        _PATCH_RE.fullmatch(value) or _PATCH_WITHOUT_SUFFIX_RE.fullmatch(value)
    ):
        _fail(path, "must be UNKNOWN or an explicit patch such as 14.24 or 14.24b")
    return value


def _require_date(value: Any, path: str) -> str:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        _fail(path, "must use strict YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{path}: invalid calendar date: {value}") from exc
    if parsed.isoformat() != value:
        _fail(path, "must use canonical YYYY-MM-DD format")
    return value


def _require_confidence(value: Any, path: str) -> float:
    if type(value) not in (int, float):
        _fail(path, "must be a finite number, not a boolean")
    normalized = float(value)
    if not math.isfinite(normalized):
        _fail(path, "must be finite")
    if normalized < 0.0 or normalized > 1.0:
        _fail(path, "must be between 0.0 and 1.0 inclusive")
    return normalized


def _require_string_list(
    value: Any,
    path: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail(path, "must be an array")
    if len(value) < minimum or len(value) > maximum:
        _fail(path, f"must contain between {minimum} and {maximum} entries")
    result = tuple(
        _require_text(item, f"{path}[{index}]", maximum=128)
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        _fail(path, "must not contain duplicate entries")
    return result


def _require_source_type(value: Any, path: str) -> SourceType:
    if type(value) is not str:
        _fail(path, "must be a source type string")
    try:
        return SourceType(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceType)
        raise ContractError(f"{path}: unknown source type; allowed: {allowed}") from exc


def _require_claim_type(value: Any, path: str) -> ClaimType:
    if type(value) is not str:
        _fail(path, "must be a claim type string")
    try:
        return ClaimType(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ClaimType)
        raise ContractError(f"{path}: unknown claim type; allowed: {allowed}") from exc


def _require_fusion_status(value: Any, path: str) -> FusionStatus:
    if type(value) is not str:
        _fail(path, "must be a fusion status string")
    try:
        return FusionStatus(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in FusionStatus)
        raise ContractError(
            f"{path}: unknown fusion status; allowed: {allowed}"
        ) from exc


def _validate_external_url(value: Any, path: str) -> str:
    url = _require_text(value, path, maximum=2048)
    if any(character.isspace() for character in url):
        _fail(path, "must not contain whitespace")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ContractError(f"{path}: malformed URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        _fail(path, "must be an absolute http or https URL")
    if parsed.username is not None or parsed.password is not None:
        _fail(path, "must not contain embedded credentials")
    if parsed.fragment:
        _fail(path, "must not contain a fragment")
    return url


def _optional_conflict_group(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _require_id(value, path)


def _player_safe_scan(value: Any, path: str = "query_result") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                _fail(path, "object keys must be strings")
            normalized_key = "".join(character for character in key.casefold() if character.isalnum())
            if any(part in normalized_key for part in _FORBIDDEN_QUERY_KEY_PARTS):
                _fail(f"{path}.{key}", "win-rate and pick-rate fields are player-unsafe")
            if normalized_key in _FORBIDDEN_QUERY_KEYS or "必须选" in key:
                _fail(f"{path}.{key}", "mandatory-choice fields are player-unsafe")
            _player_safe_scan(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            _player_safe_scan(child, f"{path}[{index}]")
        return
    if type(value) is str:
        folded = " ".join(value.casefold().split())
        for phrase in _FORBIDDEN_QUERY_PHRASES:
            if phrase in folded:
                _fail(path, f"player-unsafe mandatory wording is forbidden: {phrase!r}")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A validated external source or explicitly internal inference source."""

    schema_version: int
    source_id: str
    source_type: SourceType
    source_url: str | None
    source_name: str
    publish_date: str | None
    crawl_date: str
    patch: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceRecord":
        """Validate and construct a source record from a mapping."""

        return validate_source_record(value)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible representation."""

        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "publish_date": self.publish_date,
            "crawl_date": self.crawl_date,
            "patch": self.patch,
        }


@dataclass(frozen=True, slots=True)
class Claim:
    """One atomic item-impact or augment-synergy statement."""

    schema_version: int
    claim_id: str
    source_id: str
    claim_type: ClaimType
    patch: str
    champion: str
    augment: str
    items: tuple[str, ...]
    summary: str
    confidence: float
    conflict_group: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Claim":
        """Validate and construct an atomic claim from a mapping."""

        return validate_claim(value)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible representation."""

        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "claim_type": self.claim_type.value,
            "patch": self.patch,
            "champion": self.champion,
            "augment": self.augment,
            "items": list(self.items),
            "summary": self.summary,
            "confidence": self.confidence,
            "conflict_group": self.conflict_group,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    """A normalized record backed by one or more atomic claim identifiers."""

    schema_version: int
    knowledge_id: str
    knowledge_type: ClaimType
    claim_ids: tuple[str, ...]
    fusion_status: FusionStatus
    patch: str
    champion: str
    augment: str
    items: tuple[str, ...]
    summary: str
    confidence: float
    conflict_group: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "KnowledgeRecord":
        """Validate and construct a fused or single-source knowledge record."""

        return validate_knowledge_record(value)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible representation."""

        return {
            "schema_version": self.schema_version,
            "knowledge_id": self.knowledge_id,
            "knowledge_type": self.knowledge_type.value,
            "claim_ids": list(self.claim_ids),
            "fusion_status": self.fusion_status.value,
            "patch": self.patch,
            "champion": self.champion,
            "augment": self.augment,
            "items": list(self.items),
            "summary": self.summary,
            "confidence": self.confidence,
            "conflict_group": self.conflict_group,
        }


@dataclass(frozen=True, slots=True)
class QueryResult:
    """A player-safe recommendation with explicit knowledge and source lineage."""

    schema_version: int
    query_id: str
    knowledge_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    patch: str
    champion: str
    augment: str
    items: tuple[str, ...]
    summary: str
    confidence: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QueryResult":
        """Validate and construct a player-safe query result."""

        return validate_query_result(value)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible representation."""

        return {
            "schema_version": self.schema_version,
            "query_id": self.query_id,
            "knowledge_ids": list(self.knowledge_ids),
            "source_ids": list(self.source_ids),
            "patch": self.patch,
            "champion": self.champion,
            "augment": self.augment,
            "items": list(self.items),
            "summary": self.summary,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class ContractBundle:
    """A fully cross-validated set of Phase3 contract records."""

    sources: tuple[SourceRecord, ...]
    claims: tuple[Claim, ...]
    knowledge_records: tuple[KnowledgeRecord, ...]
    query_results: tuple[QueryResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return all records as canonical JSON-compatible arrays."""

        return {
            "sources": [record.to_dict() for record in self.sources],
            "claims": [record.to_dict() for record in self.claims],
            "knowledge_records": [
                record.to_dict() for record in self.knowledge_records
            ],
            "query_results": [record.to_dict() for record in self.query_results],
        }


def validate_source_record(value: Mapping[str, Any] | SourceRecord) -> SourceRecord:
    """Validate one source record, including inference provenance separation."""

    if isinstance(value, SourceRecord):
        value = value.to_dict()
    record = _require_object(value, "source_record")
    required = {
        "schema_version",
        "source_id",
        "source_type",
        "source_url",
        "source_name",
        "publish_date",
        "crawl_date",
        "patch",
    }
    _require_keys(record, required=required, path="source_record")
    source_type = _require_source_type(record["source_type"], "source_record.source_type")
    crawl_date = _require_date(record["crawl_date"], "source_record.crawl_date")

    if source_type is SourceType.INFERENCE:
        if record["source_url"] is not None:
            _fail(
                "source_record.source_url",
                "INFERENCE must not masquerade as an external URL",
            )
        if record["publish_date"] is not None:
            _fail(
                "source_record.publish_date",
                "INFERENCE has no external publication date and must use null",
            )
        if record["source_name"] != INTERNAL_INFERENCE_SOURCE_NAME:
            _fail(
                "source_record.source_name",
                f"INFERENCE must use {INTERNAL_INFERENCE_SOURCE_NAME!r}",
            )
        source_url = None
        source_name = INTERNAL_INFERENCE_SOURCE_NAME
        publish_date = None
    else:
        source_url = _validate_external_url(
            record["source_url"], "source_record.source_url"
        )
        source_name = _require_text(
            record["source_name"], "source_record.source_name", maximum=200
        )
        publish_date = _require_date(
            record["publish_date"], "source_record.publish_date"
        )
        if publish_date > crawl_date:
            _fail(
                "source_record.publish_date",
                "must not be later than crawl_date",
            )

    return SourceRecord(
        schema_version=_require_schema_version(
            record["schema_version"], "source_record.schema_version"
        ),
        source_id=_require_id(record["source_id"], "source_record.source_id"),
        source_type=source_type,
        source_url=source_url,
        source_name=source_name,
        publish_date=publish_date,
        crawl_date=crawl_date,
        patch=_require_patch(record["patch"], "source_record.patch"),
    )


def validate_claim(value: Mapping[str, Any] | Claim) -> Claim:
    """Validate one atomic claim without resolving its source foreign key."""

    if isinstance(value, Claim):
        value = value.to_dict()
    record = _require_object(value, "claim")
    required = {
        "schema_version",
        "claim_id",
        "source_id",
        "claim_type",
        "patch",
        "champion",
        "augment",
        "items",
        "summary",
        "confidence",
        "conflict_group",
    }
    _require_keys(record, required=required, path="claim")
    claim_type = _require_claim_type(record["claim_type"], "claim.claim_type")
    items = _require_string_list(record["items"], "claim.items", minimum=0, maximum=20)
    if claim_type is ClaimType.ITEM_IMPACT and not items:
        _fail("claim.items", "ITEM_IMPACT requires at least one item")
    return Claim(
        schema_version=_require_schema_version(
            record["schema_version"], "claim.schema_version"
        ),
        claim_id=_require_id(record["claim_id"], "claim.claim_id"),
        source_id=_require_id(record["source_id"], "claim.source_id"),
        claim_type=claim_type,
        patch=_require_patch(record["patch"], "claim.patch"),
        champion=_require_text(record["champion"], "claim.champion", maximum=128),
        augment=_require_text(record["augment"], "claim.augment", maximum=128),
        items=items,
        summary=_require_text(record["summary"], "claim.summary", maximum=2000),
        confidence=_require_confidence(record["confidence"], "claim.confidence"),
        conflict_group=_optional_conflict_group(
            record["conflict_group"], "claim.conflict_group"
        ),
    )


def validate_knowledge_record(
    value: Mapping[str, Any] | KnowledgeRecord,
) -> KnowledgeRecord:
    """Validate one knowledge record without resolving claim foreign keys."""

    if isinstance(value, KnowledgeRecord):
        value = value.to_dict()
    record = _require_object(value, "knowledge_record")
    required = {
        "schema_version",
        "knowledge_id",
        "knowledge_type",
        "claim_ids",
        "fusion_status",
        "patch",
        "champion",
        "augment",
        "items",
        "summary",
        "confidence",
        "conflict_group",
    }
    _require_keys(record, required=required, path="knowledge_record")
    knowledge_type = _require_claim_type(
        record["knowledge_type"], "knowledge_record.knowledge_type"
    )
    claim_ids = _require_string_list(
        record["claim_ids"], "knowledge_record.claim_ids", minimum=1, maximum=100
    )
    claim_ids = tuple(
        _require_id(item, f"knowledge_record.claim_ids[{index}]")
        for index, item in enumerate(claim_ids)
    )
    fusion_status = _require_fusion_status(
        record["fusion_status"], "knowledge_record.fusion_status"
    )
    conflict_group = _optional_conflict_group(
        record["conflict_group"], "knowledge_record.conflict_group"
    )
    if fusion_status is FusionStatus.SINGLE_SOURCE and len(claim_ids) != 1:
        _fail(
            "knowledge_record.claim_ids",
            "SINGLE_SOURCE requires exactly one claim",
        )
    if fusion_status in {FusionStatus.FUSED_CONSENSUS, FusionStatus.CONFLICTED} and len(
        claim_ids
    ) < 2:
        _fail(
            "knowledge_record.claim_ids",
            f"{fusion_status.value} requires at least two claims",
        )
    if fusion_status is FusionStatus.CONFLICTED and conflict_group is None:
        _fail(
            "knowledge_record.conflict_group",
            "CONFLICTED requires a conflict group",
        )
    if fusion_status is FusionStatus.FUSED_CONSENSUS and conflict_group is not None:
        _fail(
            "knowledge_record.conflict_group",
            "FUSED_CONSENSUS cannot be labeled as a conflict group",
        )

    items = _require_string_list(
        record["items"], "knowledge_record.items", minimum=0, maximum=20
    )
    if knowledge_type is ClaimType.ITEM_IMPACT and not items:
        _fail("knowledge_record.items", "ITEM_IMPACT requires at least one item")
    return KnowledgeRecord(
        schema_version=_require_schema_version(
            record["schema_version"], "knowledge_record.schema_version"
        ),
        knowledge_id=_require_id(
            record["knowledge_id"], "knowledge_record.knowledge_id"
        ),
        knowledge_type=knowledge_type,
        claim_ids=claim_ids,
        fusion_status=fusion_status,
        patch=_require_patch(record["patch"], "knowledge_record.patch"),
        champion=_require_text(
            record["champion"], "knowledge_record.champion", maximum=128
        ),
        augment=_require_text(
            record["augment"], "knowledge_record.augment", maximum=128
        ),
        items=items,
        summary=_require_text(
            record["summary"], "knowledge_record.summary", maximum=2000
        ),
        confidence=_require_confidence(
            record["confidence"], "knowledge_record.confidence"
        ),
        conflict_group=conflict_group,
    )


def validate_query_result(value: Mapping[str, Any] | QueryResult) -> QueryResult:
    """Validate a player-safe query result without resolving its foreign keys."""

    if isinstance(value, QueryResult):
        value = value.to_dict()
    _player_safe_scan(value)
    record = _require_object(value, "query_result")
    required = {
        "schema_version",
        "query_id",
        "knowledge_ids",
        "source_ids",
        "patch",
        "champion",
        "augment",
        "items",
        "summary",
        "confidence",
    }
    _require_keys(record, required=required, path="query_result")
    knowledge_ids = _require_string_list(
        record["knowledge_ids"], "query_result.knowledge_ids", minimum=1, maximum=100
    )
    source_ids = _require_string_list(
        record["source_ids"], "query_result.source_ids", minimum=1, maximum=100
    )
    result = QueryResult(
        schema_version=_require_schema_version(
            record["schema_version"], "query_result.schema_version"
        ),
        query_id=_require_id(record["query_id"], "query_result.query_id"),
        knowledge_ids=tuple(
            _require_id(item, f"query_result.knowledge_ids[{index}]")
            for index, item in enumerate(knowledge_ids)
        ),
        source_ids=tuple(
            _require_id(item, f"query_result.source_ids[{index}]")
            for index, item in enumerate(source_ids)
        ),
        patch=_require_patch(record["patch"], "query_result.patch"),
        champion=_require_text(
            record["champion"], "query_result.champion", maximum=128
        ),
        augment=_require_text(
            record["augment"], "query_result.augment", maximum=128
        ),
        items=_require_string_list(
            record["items"], "query_result.items", minimum=0, maximum=20
        ),
        summary=_require_text(
            record["summary"], "query_result.summary", maximum=2000
        ),
        confidence=_require_confidence(
            record["confidence"], "query_result.confidence"
        ),
    )
    _player_safe_scan(result.to_dict())
    return result


def _index_unique(records: Iterable[Any], attribute: str, path: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, record in enumerate(records):
        identifier = getattr(record, attribute)
        if identifier in result:
            _fail(f"{path}[{index}].{attribute}", f"duplicate identifier: {identifier}")
        result[identifier] = record
    return result


def _expected_patch(patches: Iterable[str], path: str) -> str:
    values = set(patches)
    explicit = values - {UNKNOWN_PATCH}
    if len(explicit) > 1:
        _fail(path, f"cannot combine different explicit patches: {sorted(explicit)}")
    if UNKNOWN_PATCH in values:
        return UNKNOWN_PATCH
    if not explicit:
        _fail(path, "no patch evidence")
    return next(iter(explicit))


def _require_equal(value: Any, expected: Any, path: str, basis: str) -> None:
    if value != expected:
        _fail(path, f"must equal {basis}: expected {expected!r}, got {value!r}")


def validate_contract_bundle(
    sources: Iterable[Mapping[str, Any] | SourceRecord],
    claims: Iterable[Mapping[str, Any] | Claim],
    knowledge_records: Iterable[Mapping[str, Any] | KnowledgeRecord] = (),
    query_results: Iterable[Mapping[str, Any] | QueryResult] = (),
) -> ContractBundle:
    """Cross-validate provenance, foreign keys, fusion, patch, and query safety.

    A concrete patch can never be derived from ``UNKNOWN`` evidence. A fused
    consensus requires at least two distinct external sources; merely duplicating
    claims from one source is rejected. Query ``source_ids`` must be the exact
    source lineage reachable through its knowledge and claim references.
    """

    source_records = tuple(validate_source_record(value) for value in sources)
    claim_records = tuple(validate_claim(value) for value in claims)
    knowledge_values = tuple(
        validate_knowledge_record(value) for value in knowledge_records
    )
    query_values = tuple(validate_query_result(value) for value in query_results)

    source_by_id: dict[str, SourceRecord] = _index_unique(
        source_records, "source_id", "sources"
    )
    claim_by_id: dict[str, Claim] = _index_unique(claim_records, "claim_id", "claims")
    knowledge_by_id: dict[str, KnowledgeRecord] = _index_unique(
        knowledge_values, "knowledge_id", "knowledge_records"
    )
    _index_unique(query_values, "query_id", "query_results")

    for index, claim in enumerate(claim_records):
        source = source_by_id.get(claim.source_id)
        if source is None:
            _fail(
                f"claims[{index}].source_id",
                f"missing source foreign key: {claim.source_id}",
            )
        if source.patch == UNKNOWN_PATCH and claim.patch != UNKNOWN_PATCH:
            _fail(
                f"claims[{index}].patch",
                "cannot upgrade an UNKNOWN source patch to an explicit patch",
            )
        if (
            source.patch != UNKNOWN_PATCH
            and claim.patch != UNKNOWN_PATCH
            and source.patch != claim.patch
        ):
            _fail(
                f"claims[{index}].patch",
                f"does not match source patch {source.patch!r}",
            )

    for index, knowledge in enumerate(knowledge_values):
        selected: list[Claim] = []
        for claim_id in knowledge.claim_ids:
            claim = claim_by_id.get(claim_id)
            if claim is None:
                _fail(
                    f"knowledge_records[{index}].claim_ids",
                    f"missing claim foreign key: {claim_id}",
                )
            selected.append(claim)

        for field, expected in (
            ("knowledge_type", knowledge.knowledge_type),
            ("champion", knowledge.champion),
            ("augment", knowledge.augment),
            ("items", knowledge.items),
        ):
            claim_attribute = "claim_type" if field == "knowledge_type" else field
            if any(getattr(claim, claim_attribute) != expected for claim in selected):
                _fail(
                    f"knowledge_records[{index}].{field}",
                    f"must match every supporting claim's {claim_attribute}",
                )

        expected_patch = _expected_patch(
            (claim.patch for claim in selected),
            f"knowledge_records[{index}].patch",
        )
        _require_equal(
            knowledge.patch,
            expected_patch,
            f"knowledge_records[{index}].patch",
            "the conservative supporting-claim patch",
        )

        selected_sources = [source_by_id[claim.source_id] for claim in selected]
        if knowledge.fusion_status is FusionStatus.FUSED_CONSENSUS:
            external_ids = {
                source.source_id
                for source in selected_sources
                if source.source_type is not SourceType.INFERENCE
            }
            if len(external_ids) < 2 or len(external_ids) != len(
                {source.source_id for source in selected_sources}
            ):
                _fail(
                    f"knowledge_records[{index}].fusion_status",
                    "FUSED_CONSENSUS requires at least two distinct external sources and no INFERENCE source",
                )
            if any(claim.conflict_group is not None for claim in selected):
                _fail(
                    f"knowledge_records[{index}].conflict_group",
                    "conflict-group claims cannot be labeled FUSED_CONSENSUS",
                )
        elif knowledge.fusion_status is FusionStatus.CONFLICTED:
            if any(
                claim.conflict_group != knowledge.conflict_group for claim in selected
            ):
                _fail(
                    f"knowledge_records[{index}].conflict_group",
                    "must match every supporting claim conflict_group",
                )

    for index, query in enumerate(query_values):
        selected_knowledge: list[KnowledgeRecord] = []
        for knowledge_id in query.knowledge_ids:
            knowledge = knowledge_by_id.get(knowledge_id)
            if knowledge is None:
                _fail(
                    f"query_results[{index}].knowledge_ids",
                    f"missing knowledge foreign key: {knowledge_id}",
                )
            selected_knowledge.append(knowledge)

        for field in ("champion", "augment", "items"):
            if any(getattr(record, field) != getattr(query, field) for record in selected_knowledge):
                _fail(
                    f"query_results[{index}].{field}",
                    f"must match every referenced knowledge record's {field}",
                )
        expected_patch = _expected_patch(
            (record.patch for record in selected_knowledge),
            f"query_results[{index}].patch",
        )
        _require_equal(
            query.patch,
            expected_patch,
            f"query_results[{index}].patch",
            "the conservative knowledge patch",
        )
        if query.confidence > max(record.confidence for record in selected_knowledge):
            _fail(
                f"query_results[{index}].confidence",
                "must not exceed the strongest referenced knowledge confidence",
            )

        expected_source_ids = {
            claim_by_id[claim_id].source_id
            for knowledge in selected_knowledge
            for claim_id in knowledge.claim_ids
        }
        if set(query.source_ids) != expected_source_ids:
            _fail(
                f"query_results[{index}].source_ids",
                "must exactly preserve source provenance reachable from knowledge claims",
            )

    return ContractBundle(
        sources=source_records,
        claims=claim_records,
        knowledge_records=knowledge_values,
        query_results=query_values,
    )


def validate_bundle(
    sources: Iterable[Mapping[str, Any] | SourceRecord],
    claims: Iterable[Mapping[str, Any] | Claim],
    knowledge_records: Iterable[Mapping[str, Any] | KnowledgeRecord] = (),
    query_results: Iterable[Mapping[str, Any] | QueryResult] = (),
) -> ContractBundle:
    """Compatibility alias for :func:`validate_contract_bundle`."""

    return validate_contract_bundle(sources, claims, knowledge_records, query_results)


__all__ = [
    "SCHEMA_VERSION",
    "UNKNOWN_PATCH",
    "INTERNAL_INFERENCE_SOURCE_NAME",
    "ContractError",
    "SourceType",
    "ClaimType",
    "FusionStatus",
    "SourceRecord",
    "Claim",
    "KnowledgeRecord",
    "QueryResult",
    "ContractBundle",
    "strict_json_loads",
    "strict_json_load",
    "validate_source_record",
    "validate_claim",
    "validate_knowledge_record",
    "validate_query_result",
    "validate_contract_bundle",
    "validate_bundle",
]
