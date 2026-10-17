from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from models.summary import Summary, validate_for_depth, verify_against_source
from tests.factories import summary

EMPTY = {
    "title": "",
    "one_line": "",
    "key_points": [],
    "why_it_matters": "",
    "tags": [],
    "entities": [],
    "notable_figures": [],
}


def test_valid() -> None:
    Summary.model_validate(summary())


@pytest.mark.parametrize(
    "over",
    [
        {"one_line": "x" * 201},
        {"why_it_matters": "x" * 301},
        {"key_points": ["x"] * 7},
        {"key_points": ["x" * 241]},
        {"tags": ["rag", "datasets", "robotics", "multimodal", "agents-tooling"]},
        {"tags": ["not-in-taxonomy"]},
        {"tags": ["rag", "rag"]},
        {"entities": ["e"] * 9},
        {"status": "weird"},
        {"extra": 1},
        {"one_line": "see https://evil.example/x"},
        {"one_line": "go to www.evil.example"},
        {"key_points": ["```code```"]},
        {"key_points": ["<b>bold</b>"]},
        {"one_line": "[click](x)"},
        {"title": "", "one_line": ""},
        {"status": "insufficient_content"},  # content must be empty
        {"status": "suspected_injection", **EMPTY, "injection_flag": False},
    ],
)
def test_invalid(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Summary.model_validate(summary(**over))


def test_non_ok_states_valid_when_empty() -> None:
    Summary.model_validate(summary(status="insufficient_content", **EMPTY))
    Summary.model_validate(summary(status="suspected_injection", injection_flag=True, **EMPTY))


def test_depth_rules() -> None:
    s = Summary.model_validate(summary(key_points=["a", "b", "c", "d"]))
    assert validate_for_depth(s, "brief")
    assert not validate_for_depth(s, "detailed")
    assert validate_for_depth(s, "headlines")
    long = Summary.model_validate(summary(key_points=["x" * 200]))
    assert validate_for_depth(long, "brief") and not validate_for_depth(long, "detailed")


def test_verify_against_source() -> None:
    s = Summary.model_validate(summary())
    url = "https://example.com/post"
    assert verify_against_source(s, source_url=url, source_text="a 7B model") == []
    assert verify_against_source(s, source_url=url, source_text="no figures here")
    assert verify_against_source(s, source_url="https://other.example/", source_text="7B")
    assert verify_against_source(s, source_url=url, source_text="7B", allowed_tags=["datasets"])
