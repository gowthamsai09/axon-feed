"""Offline validation of config/*.yaml. Exit 1 on errors. Run: python -m scripts.validate_config"""

from __future__ import annotations

import sys
from pathlib import Path

from models.loaders import ConfigError, load_authority, load_sources, load_taxonomy
from models.validation import Report, check_authority, check_sources, check_taxonomy

CONFIG = Path(__file__).resolve().parent.parent / "config"


def main(config_dir: Path = CONFIG, *, quiet_warnings: bool = False) -> int:
    try:
        tax = load_taxonomy(config_dir / "taxonomy.yaml")
        src = load_sources(config_dir / "sources.yaml")
        auth = load_authority(config_dir / "authority.yaml")
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    rep = Report()
    rep.extend(check_taxonomy(tax))
    rep.extend(check_authority(auth))
    rep.extend(check_sources(src, tax, auth))
    for e in rep.errors:
        print(f"ERROR   {e}")
    if not quiet_warnings:
        for w in rep.warnings:
            print(f"warning {w}")
    print(f"{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main(quiet_warnings="--quiet" in sys.argv))
