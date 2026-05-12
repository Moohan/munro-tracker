from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.seed_munros import (
    has_expected_seeded_munros,
    main,
    parse_rows,
    split_name_and_alternatives,
)


def test_split_name_and_alternatives_extracts_bracketed_names() -> None:
    name, alternatives = split_name_and_alternatives("Beinn Example [Alt One] [Alt Two]")

    assert name == "Beinn Example"
    assert alternatives == ["Alt One", "Alt Two"]


def test_parse_rows_filters_out_munro_tops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.seed_munros.EXPECTED_MUNRO_COUNT", 1)
    csv_path = tmp_path / "munros.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Number,Name,Metres,Drop,Latitude,Longitude,Area,M,MT",
                "1,Ben Alpha [A],1000,250,56.1,-4.1,Area 1,1,0",
                "2,Ben Beta,990,200,56.2,-4.2,Area 2,1,1",
            ]
        ),
        encoding="utf-8",
    )

    rows = parse_rows(csv_path)

    assert len(rows) == 1
    assert rows[0].dobih_id == 1
    assert rows[0].alternative_names == ["A"]


def test_has_expected_seeded_munros_returns_true_for_282_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.seed_munros.EXPECTED_MUNRO_COUNT", 282)
    mock_engine = MagicMock()
    mock_connection = MagicMock()
    mock_connection.execute.return_value.scalar_one.return_value = 282
    mock_begin = mock_engine.begin.return_value.__enter__.return_value
    mock_begin.execute = mock_connection.execute

    with patch("scripts.seed_munros.create_engine", return_value=mock_engine), patch(
        "scripts.seed_munros.inspect",
        return_value=MagicMock(has_table=MagicMock(return_value=True)),
    ):
        assert has_expected_seeded_munros("postgresql://example") is True


def test_main_skips_download_when_seed_already_present(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "scripts.seed_munros.parse_args",
        lambda: MagicMock(
            database_url=None,
            source_url="https://example.invalid/dobih.zip",
            verify_only=False,
            skip_if_seeded=True,
        ),
    )
    monkeypatch.setattr("scripts.seed_munros.load_project_env", lambda: None)
    monkeypatch.setattr(
        "scripts.seed_munros.get_settings",
        lambda: MagicMock(database_url="postgresql://example"),
    )
    monkeypatch.setattr("scripts.seed_munros.has_expected_seeded_munros", lambda database_url: True)

    with patch("scripts.seed_munros.download_archive") as mock_download_archive:
        main()

    output = capsys.readouterr().out
    assert "DoBIH download was skipped" in output
    mock_download_archive.assert_not_called()
