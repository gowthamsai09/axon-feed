"""YAML loaders that turn pydantic errors into readable messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from .authority import AuthorityConfig
from .sources import SourcesFile
from .taxonomy import Taxonomy

MAX_BYTES = 1_000_000


class ConfigError(Exception):
    """Raised with a human-readable message for any config problem."""


def load_yaml(path: Path) -> Any:
    if path.stat().st_size > MAX_BYTES:
        raise ConfigError(f"{path}: file too large")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc


def format_errors(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: {exc.error_count()} validation error(s)"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_model[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        return model.model_validate(load_yaml(path))
    except ValidationError as exc:
        raise ConfigError(format_errors(path, exc)) from exc


def load_taxonomy(path: Path) -> Taxonomy:
    return load_model(path, Taxonomy)


def load_sources(path: Path) -> SourcesFile:
    return load_model(path, SourcesFile)


def load_authority(path: Path) -> AuthorityConfig:
    return load_model(path, AuthorityConfig)
