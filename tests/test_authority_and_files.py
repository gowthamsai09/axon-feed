from __future__ import annotations

from pathlib import Path

from models.common import TOPIC_SLUGS
from models.loaders import load_authority, load_sources, load_taxonomy
from models.validation import check_authority, check_sources, check_taxonomy
from scripts.validate_config import main as validate_main

CONFIG = Path(__file__).resolve().parent.parent / "config"


def test_shipped_config_has_no_errors() -> None:
    tax = load_taxonomy(CONFIG / "taxonomy.yaml")
    src = load_sources(CONFIG / "sources.yaml")
    auth = load_authority(CONFIG / "authority.yaml")
    assert list(tax.topics) == list(TOPIC_SLUGS) or set(tax.topics) == set(TOPIC_SLUGS)
    for rep in (check_taxonomy(tax), check_authority(auth), check_sources(src, tax, auth)):
        assert rep.errors == []
    assert {s.id for s in src.sources} >= {"arxiv-cs-ai", "github-releases"}
    assert {n.id for n in src.needs_research} >= {"anthropic-news", "deepseek-news"}


def test_validate_config_cli_ok() -> None:
    assert validate_main(CONFIG, quiet_warnings=True) == 0


def test_authority_gap_detected() -> None:
    auth = load_authority(CONFIG / "authority.yaml")
    auth.tiers[0].min_score = 0.90
    assert not check_authority(auth).ok


def test_every_source_authority_has_a_tier() -> None:
    auth = load_authority(CONFIG / "authority.yaml")
    for s in load_sources(CONFIG / "sources.yaml").sources:
        assert auth.tier_for(s.authority) is not None
