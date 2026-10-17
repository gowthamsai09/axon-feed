from __future__ import annotations

from pathlib import Path

from scripts.export_schemas import OWNED, check, export, main


def test_export_is_deterministic_and_checkable(tmp_path: Path) -> None:
    export(tmp_path)
    first = {p.name: p.read_text() for p in tmp_path.iterdir()}
    assert set(first) == {f"{n}.schema.json" for n in OWNED}
    export(tmp_path)
    assert first == {p.name: p.read_text() for p in tmp_path.iterdir()}
    assert check(tmp_path) == []
    assert main(["--check", str(tmp_path)]) == 0


def test_drift_detected(tmp_path: Path) -> None:
    export(tmp_path)
    (tmp_path / "summary.schema.json").write_text("{}\n")
    assert check(tmp_path)
    assert main(["--check", str(tmp_path)]) == 1


def test_missing_detected(tmp_path: Path) -> None:
    assert len(check(tmp_path)) == len(OWNED)
