#!/usr/bin/env python3
"""Executable contract tests for Phase3 web knowledge records."""

from __future__ import annotations

import copy
import inspect
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.phase3 import contracts  # noqa: E402


def source_record(source_id: str, source_type: str = "OFFICIAL") -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_id": source_id,
        "source_type": source_type,
        "source_url": f"https://example.test/{source_id}",
        "source_name": f"Source {source_id}",
        "publish_date": "2024-12-01",
        "crawl_date": "2024-12-02",
        "patch": "14.24",
    }


def claim_record(claim_id: str, source_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "claim_id": claim_id,
        "source_id": source_id,
        "claim_type": "AUGMENT_SYNERGY",
        "patch": "14.24",
        "champion": "Veigar",
        "augment": "Archmage",
        "items": ["Rabadon's Deathcap"],
        "summary": "Archmage complements Veigar's ability-power scaling.",
        "confidence": 0.8,
        "conflict_group": None,
    }


def knowledge_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "knowledge_id": "knowledge.veigar.archmage",
        "knowledge_type": "AUGMENT_SYNERGY",
        "claim_ids": ["claim.official", "claim.guide"],
        "fusion_status": "FUSED_CONSENSUS",
        "patch": "14.24",
        "champion": "Veigar",
        "augment": "Archmage",
        "items": ["Rabadon's Deathcap"],
        "summary": "Archmage is a supported synergy for this Veigar setup.",
        "confidence": 0.85,
        "conflict_group": None,
    }


def query_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "query_id": "query.veigar.archmage",
        "knowledge_ids": ["knowledge.veigar.archmage"],
        "source_ids": ["source.official", "source.guide"],
        "patch": "14.24",
        "champion": "Veigar",
        "augment": "Archmage",
        "items": ["Rabadon's Deathcap"],
        "summary": "Archmage has documented synergy with this Veigar setup.",
        "confidence": 0.85,
    }


def valid_bundle() -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    sources = [
        source_record("source.official", "OFFICIAL"),
        source_record("source.guide", "GUIDE"),
    ]
    claims = [
        claim_record("claim.official", "source.official"),
        claim_record("claim.guide", "source.guide"),
    ]
    return sources, claims, [knowledge_record()], [query_result()]


class Phase3ContractTests(unittest.TestCase):
    def test_valid_veigar_archmage_fused_bundle(self) -> None:
        bundle = contracts.validate_contract_bundle(*valid_bundle())
        self.assertEqual(bundle.query_results[0].champion, "Veigar")
        self.assertEqual(bundle.query_results[0].augment, "Archmage")
        self.assertEqual(bundle.knowledge_records[0].fusion_status.value, "FUSED_CONSENSUS")

    def test_all_external_source_enums_are_accepted(self) -> None:
        for source_type in ("OFFICIAL", "STATISTICAL", "GUIDE", "COMMUNITY"):
            with self.subTest(source_type=source_type):
                parsed = contracts.validate_source_record(source_record("source.x", source_type))
                self.assertEqual(parsed.source_type.value, source_type)

    def test_strict_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(contracts.ContractError, "duplicate"):
            contracts.strict_json_loads('{"patch":"14.24","patch":"UNKNOWN"}')

    def test_strict_json_rejects_named_nonfinite_numbers(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaises(contracts.ContractError):
                contracts.strict_json_loads(f'{{"confidence":{token}}}')

    def test_strict_json_rejects_float_overflow(self) -> None:
        with self.assertRaisesRegex(contracts.ContractError, "non-finite"):
            contracts.strict_json_loads('{"confidence":1e999}')

    def test_unknown_source_enum_is_rejected(self) -> None:
        value = source_record("source.bad", "BLOG")
        with self.assertRaisesRegex(contracts.ContractError, "unknown source type"):
            contracts.validate_source_record(value)

    def test_bad_dates_and_publish_after_crawl_are_rejected(self) -> None:
        invalid_calendar = source_record("source.bad-date")
        invalid_calendar["publish_date"] = "2024-02-30"
        with self.assertRaisesRegex(contracts.ContractError, "calendar date"):
            contracts.validate_source_record(invalid_calendar)

        reversed_dates = source_record("source.reversed")
        reversed_dates["publish_date"] = "2024-12-03"
        with self.assertRaisesRegex(contracts.ContractError, "later than crawl_date"):
            contracts.validate_source_record(reversed_dates)

    def test_confidence_bounds_and_boolean_are_rejected(self) -> None:
        for confidence in (-0.01, 1.01, True):
            value = claim_record("claim.bad-confidence", "source.official")
            value["confidence"] = confidence
            with self.subTest(confidence=confidence), self.assertRaises(contracts.ContractError):
                contracts.validate_claim(value)

    def test_inference_cannot_masquerade_as_external_source(self) -> None:
        inference = source_record("source.inference", "INFERENCE")
        with self.assertRaisesRegex(contracts.ContractError, "masquerade"):
            contracts.validate_source_record(inference)

    def test_valid_internal_inference_is_explicit(self) -> None:
        inference = source_record("source.inference", "INFERENCE")
        inference.update(
            source_url=None,
            source_name="INTERNAL_INFERENCE",
            publish_date=None,
            patch="UNKNOWN",
        )
        parsed = contracts.validate_source_record(inference)
        self.assertIsNone(parsed.source_url)

    def test_missing_source_foreign_key_is_rejected(self) -> None:
        sources, claims, _, _ = valid_bundle()
        claims[0]["source_id"] = "source.missing"
        with self.assertRaisesRegex(contracts.ContractError, "missing source foreign key"):
            contracts.validate_contract_bundle(sources, claims)

    def test_unknown_patch_cannot_be_upgraded(self) -> None:
        source = source_record("source.unknown")
        source["patch"] = "UNKNOWN"
        claim = claim_record("claim.explicit", "source.unknown")
        with self.assertRaisesRegex(contracts.ContractError, "cannot upgrade"):
            contracts.validate_contract_bundle([source], [claim])

    def test_current_patch_alias_is_rejected(self) -> None:
        value = claim_record("claim.current", "source.official")
        value["patch"] = "CURRENT"
        with self.assertRaisesRegex(contracts.ContractError, "explicit patch"):
            contracts.validate_claim(value)

    def test_one_source_cannot_be_fused_consensus(self) -> None:
        source = source_record("source.only")
        claims = [
            claim_record("claim.one", "source.only"),
            claim_record("claim.two", "source.only"),
        ]
        knowledge = knowledge_record()
        knowledge["claim_ids"] = ["claim.one", "claim.two"]
        with self.assertRaisesRegex(contracts.ContractError, "distinct external sources"):
            contracts.validate_contract_bundle([source], claims, [knowledge])

    def test_conflict_group_is_cross_validated(self) -> None:
        sources, claims, _, _ = valid_bundle()
        for claim in claims:
            claim["conflict_group"] = "conflict.veigar.archmage"
        knowledge = knowledge_record()
        knowledge.update(
            fusion_status="CONFLICTED",
            conflict_group="conflict.veigar.archmage",
        )
        bundle = contracts.validate_contract_bundle(sources, claims, [knowledge])
        self.assertEqual(bundle.knowledge_records[0].conflict_group, "conflict.veigar.archmage")

        broken = copy.deepcopy(knowledge)
        broken["conflict_group"] = "conflict.other"
        with self.assertRaisesRegex(contracts.ContractError, "supporting claim"):
            contracts.validate_contract_bundle(sources, claims, [broken])

    def test_item_impact_requires_an_item(self) -> None:
        value = claim_record("claim.item", "source.official")
        value.update(claim_type="ITEM_IMPACT", items=[])
        with self.assertRaisesRegex(contracts.ContractError, "requires at least one item"):
            contracts.validate_claim(value)

    def test_query_rejects_rate_fields_recursively(self) -> None:
        for unsafe in (
            {**query_result(), "win_rate": 0.7},
            {**query_result(), "details": {"adjusted_pick_rate": 0.4}},
        ):
            with self.subTest(keys=list(unsafe)), self.assertRaisesRegex(
                contracts.ContractError, "player-unsafe"
            ):
                contracts.validate_query_result(unsafe)

    def test_query_rejects_mandatory_choice_wording(self) -> None:
        for wording in ("这个强化必须选。", "You must pick this augment."):
            value = query_result()
            value["summary"] = wording
            with self.subTest(wording=wording), self.assertRaisesRegex(
                contracts.ContractError, "mandatory"
            ):
                contracts.validate_query_result(value)

    def test_query_requires_exact_source_lineage(self) -> None:
        sources, claims, knowledge, queries = valid_bundle()
        queries[0]["source_ids"] = ["source.official"]
        with self.assertRaisesRegex(contracts.ContractError, "preserve source provenance"):
            contracts.validate_contract_bundle(sources, claims, knowledge, queries)

    def test_unknown_fields_and_duplicate_ids_are_rejected(self) -> None:
        value = claim_record("claim.extra", "source.official")
        value["win_rate"] = 0.9
        with self.assertRaisesRegex(contracts.ContractError, "unknown fields"):
            contracts.validate_claim(value)

        source = source_record("source.same")
        with self.assertRaisesRegex(contracts.ContractError, "duplicate identifier"):
            contracts.validate_contract_bundle([source, copy.deepcopy(source)], [])

    def test_schema_documents_are_strict_json_and_closed(self) -> None:
        schema_root = REPO_ROOT / "data" / "knowledge" / "web" / "schema"
        names = {
            "source_record.schema.json",
            "claim.schema.json",
            "knowledge_record.schema.json",
            "query_result.schema.json",
        }
        self.assertEqual({path.name for path in schema_root.glob("*.json")}, names)
        for name in sorted(names):
            with self.subTest(name=name):
                schema = contracts.strict_json_load(schema_root / name)
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(schema["type"], "object")

    def test_public_functions_have_docs_and_annotations(self) -> None:
        for name in (
            "strict_json_loads",
            "strict_json_load",
            "validate_source_record",
            "validate_claim",
            "validate_knowledge_record",
            "validate_query_result",
            "validate_contract_bundle",
        ):
            function = getattr(contracts, name)
            with self.subTest(name=name):
                self.assertTrue(function.__doc__)
                signature = inspect.signature(function)
                self.assertIsNot(signature.return_annotation, inspect.Signature.empty)
                self.assertTrue(
                    all(
                        parameter.annotation is not inspect.Signature.empty
                        for parameter in signature.parameters.values()
                    )
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
