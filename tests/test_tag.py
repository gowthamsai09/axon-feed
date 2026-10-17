from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from models.common import TopicSlug
from models.loaders import load_taxonomy
from pipeline.tag import MAX_TAGS, Tagger

TAX = load_taxonomy(Path(__file__).resolve().parent.parent / "config" / "taxonomy.yaml")
TAG = Tagger(TAX)


def topics(title: str, text: str = "", default: list[TopicSlug] | None = None) -> Sequence[str]:
    return TAG.tag_topics(title, text, default)


def ents(title: str, text: str = "") -> list[str]:
    return TAG.tag_entities(title, text)


def test_whole_token_matching_not_substring() -> None:
    assert "small-local-models" not in topics("We expand our knowledge of rare cases")
    assert "rag" not in topics("Better storage and average results")
    assert "ai-products-apps" not in topics("Apple announces things", "provider inside")
    assert "rag" in topics("A new RAG pipeline")
    assert "small-local-models" in topics("Running models at the edge")


def test_hyphen_and_space_interchangeable_and_case_insensitive() -> None:
    assert "open-source-llms" in topics("New OPEN-WEIGHTS model")
    assert "open-source-llms" in topics("open weights release")
    assert "small-local-models" in topics("Run llama.cpp locally")
    assert "evals-benchmarks" in topics("SWE-bench Verified results")


def test_title_hit_or_two_body_hits() -> None:
    assert topics("Unrelated heading", "mentions a dataset once") == []
    assert "datasets" in topics("Unrelated heading", "a dataset and a corpus")
    assert "datasets" in topics("Release of a new dataset")


def test_default_tags_appended_and_capped() -> None:
    assert topics("Nothing here", "", ["research-papers"]) == ["research-papers"]
    assert topics("A new RAG pipeline", "", ["rag"]).count("rag") == 1
    many = topics(
        "rag lora gpu dataset robot benchmark safety policy agent quantization", "", ["multimodal"]
    )
    assert len(many) <= MAX_TAGS


def test_entities_basic_and_order() -> None:
    assert ents("Qwen3 beats DeepSeek-R1 on a benchmark") == ["Qwen", "DeepSeek"]
    assert ents("vLLM and Ollama both ship updates") == ["vLLM", "Ollama"]


def test_ambiguous_aliases_do_not_qualify_alone() -> None:
    assert ents("Gemini is a crypto exchange") == []
    assert ents("Meta shipped something") == []
    assert ents("Meta AI released Llama 4") == ["Meta AI"]
    assert "Google DeepMind" in ents("Gemini from DeepMind")


def test_short_names_are_case_sensitive() -> None:
    assert ents("xAI raises money") == ["xAI"]
    assert ents("XAI methods for explaining models") == []


def test_longest_match_wins() -> None:
    assert ents("Hugging Face Transformers v5 is out") == ["Hugging Face Transformers"]
    assert ents("Hugging Face launches a thing") == ["Hugging Face"]


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_text(text: str) -> None:
    assert TAG.tag("", text).tags == [] and TAG.tag("", text).entities == []
