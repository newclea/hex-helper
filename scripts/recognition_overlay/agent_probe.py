"""Run an explicit, speech-free Agent connectivity probe."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from typing import Sequence

from agent_text import AgentResult, AgentTextProvider, build_agent_provider
from overlay_config import load_agent_settings
from paths import legacy_overlay_config_path, overlay_config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test the configured Agent text provider")
    parser.add_argument("--prompt", required=True, help="text sent to the configured Agent")
    return parser


def create_provider() -> AgentTextProvider:
    settings = load_agent_settings(overlay_config_path(), legacy_overlay_config_path())
    return build_agent_provider(settings)


def run_probe(prompt: str, provider: AgentTextProvider) -> AgentResult:
    return provider.generate(prompt)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_probe(args.prompt, create_provider())
    except Exception:
        result = AgentResult(False, "", "", "internal_error")
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
