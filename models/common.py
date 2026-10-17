"""Shared types and helpers (spec 5.2, 13.1)."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# The 16 slugs are referenced verbatim by the prompts in spec section 13. Keep in sync with
# axon-template (digest/profile.py) and axon-gateway (src/schemas.ts); hub CI checks parity.
TopicSlug = Literal[
    "llm-releases",
    "open-source-llms",
    "small-local-models",
    "multimodal",
    "agents-tooling",
    "rag",
    "fine-tuning-training",
    "research-papers",
    "mlops-infra",
    "ai-hardware",
    "evals-benchmarks",
    "safety-alignment",
    "ai-policy-regulation",
    "ai-products-apps",
    "datasets",
    "robotics",
]
TOPIC_SLUGS: tuple[str, ...] = get_args(TopicSlug)

SourceType = Literal["rss", "api", "github_releases"]
Depth = Literal["headlines", "brief", "detailed"]

TRACKING_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src"})


def is_tracking_param(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in TRACKING_KEYS


SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
ITEM_ID_PATTERN = r"^[a-f0-9]{16}$"


class StrictModel(BaseModel):
    """Unknown keys are errors (spec 13.1: 'do not add extra keys')."""

    model_config = ConfigDict(extra="forbid")


def _check_url(value: str, *, https_only: bool) -> str:
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError("URL must not contain whitespace or control characters")
    parts = urlsplit(value)
    allowed = {"https"} if https_only else {"http", "https"}
    if parts.scheme not in allowed:
        raise ValueError(f"URL scheme must be one of {sorted(allowed)}")
    if not parts.hostname:
        raise ValueError("URL must have a host")
    if parts.username or parts.password:
        raise ValueError("URL must not contain credentials")
    return value


def _http(value: str) -> str:
    return _check_url(value, https_only=False)


def _https(value: str) -> str:
    return _check_url(value, https_only=True)


HttpUrlStr = Annotated[str, Field(min_length=8, max_length=2048), AfterValidator(_http)]
HttpsUrlStr = Annotated[str, Field(min_length=9, max_length=2048), AfterValidator(_https)]
SourceId = Annotated[str, Field(pattern=SLUG_PATTERN, max_length=64)]


def compute_item_id(canonical_url: str) -> str:
    """id = sha256(canonical_url)[:16] (spec 5.2). Enforced by the canonicalize stage (Phase 1)."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
