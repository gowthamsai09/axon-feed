from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from models.common import TOPIC_SLUGS
from models.taxonomy import Taxonomy
from models.validation import check_taxonomy


def base() -> dict[str, Any]:
    return {
        "topics": {
            s: {"description": s, "keywords": [f"kw-{i}-unique"]} for i, s in enumerate(TOPIC_SLUGS)
        },
        "entities": {
            "qwen": {"name": "Qwen", "aliases": ["Alibaba Qwen"]},
            "ollama": {"name": "Ollama", "aliases": ["ollama/ollama"]},
        },
    }


def test_valid_has_no_errors() -> None:
    assert check_taxonomy(Taxonomy.model_validate(base())).ok


def test_missing_slug_rejected() -> None:
    d = base()
    del d["topics"]["robotics"]
    with pytest.raises(ValidationError):
        Taxonomy.model_validate(d)


def test_unknown_slug_rejected() -> None:
    d = base()
    d["topics"]["made-up"] = {"description": "x", "keywords": ["k"]}
    with pytest.raises(ValidationError):
        Taxonomy.model_validate(d)


def test_entity_needs_name_and_aliases() -> None:
    for bad in ({"aliases": ["a"]}, {"name": "X"}, {"name": "X", "aliases": []}):
        d = base()
        d["entities"]["x"] = bad
        with pytest.raises(ValidationError):
            Taxonomy.model_validate(d)


def test_duplicate_keyword_across_topics_reported() -> None:
    d = base()
    d["topics"]["rag"]["keywords"].append("Shared Word")
    d["topics"]["datasets"]["keywords"].append("shared  word")
    rep = check_taxonomy(Taxonomy.model_validate(d))
    assert any("shared word" in e for e in rep.errors)


def test_shared_keywords_allowed_when_marked_everywhere() -> None:
    d = base()
    for t in ("rag", "datasets"):
        d["topics"][t]["shared_keywords"] = ["shared word"]
    assert check_taxonomy(Taxonomy.model_validate(d)).ok


def test_shared_in_one_plain_in_other_is_error() -> None:
    d = base()
    d["topics"]["rag"]["shared_keywords"] = ["x word"]
    d["topics"]["datasets"]["keywords"].append("x word")
    assert not check_taxonomy(Taxonomy.model_validate(d)).ok


def test_duplicate_within_topic_reported() -> None:
    d = base()
    d["topics"]["rag"]["keywords"] = ["dup", "DUP"]
    assert not check_taxonomy(Taxonomy.model_validate(d)).ok


def test_entity_alias_collision_reported() -> None:
    d = copy.deepcopy(base())
    d["entities"]["ollama"]["aliases"].append("alibaba qwen")
    assert not check_taxonomy(Taxonomy.model_validate(d)).ok
    d = base()
    d["entities"]["qwen"]["ambiguous_aliases"] = ["Alibaba Qwen"]
    assert not check_taxonomy(Taxonomy.model_validate(d)).ok


def test_warnings_for_short_and_overlapping_keywords() -> None:
    d = base()
    d["topics"]["rag"]["keywords"] = ["rag", "benchmark"]
    d["topics"]["datasets"]["keywords"] = ["benchmark results"]
    rep = check_taxonomy(Taxonomy.model_validate(d))
    assert rep.ok
    assert any("short keyword 'rag'" in w for w in rep.warnings)
    assert any("contained in 'benchmark results'" in w for w in rep.warnings)
