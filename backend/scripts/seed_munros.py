from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy import Text as SqlText
from sqlalchemy.dialects.postgresql import ARRAY

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = SCRIPT_ROOT if (SCRIPT_ROOT / "app").exists() else Path(__file__).resolve().parents[2] / "backend"
PROJECT_ROOT = BACKEND_ROOT.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings

DOBIH_CSV_URL = "https://www.hills-database.co.uk/hillcsv.zip"
EXPECTED_MUNRO_COUNT = 282
REQUIRED_COLUMNS = {
    "Area",
    "Drop",
    "Latitude",
    "Longitude",
    "M",
    "MT",
    "Metres",
    "Name",
    "Number",
}
ALTERNATIVE_NAME_PATTERN = re.compile(r"\[([^\]]+)\]")
INSERT_MUNRO_SQL = text(
    """
    INSERT INTO munros (
        dobih_id,
        name,
        alternative_names,
        hill_category,
        is_munro_top,
        height_metres,
        prominence_metres,
        area,
        geom
    )
    VALUES (
        :dobih_id,
        :name,
        :alternative_names,
        'MUN',
        FALSE,
        :height_metres,
        :prominence_metres,
        :area,
        ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
    )
    ON CONFLICT (dobih_id) DO UPDATE
    SET
        name = EXCLUDED.name,
        alternative_names = EXCLUDED.alternative_names,
        hill_category = EXCLUDED.hill_category,
        is_munro_top = EXCLUDED.is_munro_top,
        height_metres = EXCLUDED.height_metres,
        prominence_metres = EXCLUDED.prominence_metres,
        area = EXCLUDED.area,
        geom = EXCLUDED.geom,
        updated_at = NOW()
    """
).bindparams(bindparam("alternative_names", type_=ARRAY(SqlText())))


@dataclass(frozen=True)
class MunroSeedRow:
    dobih_id: int
    name: str
    alternative_names: list[str]
    height_metres: Decimal
    prominence_metres: Decimal | None
    area: str | None
    latitude: Decimal
    longitude: Decimal


def load_project_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the DoBIH CSV, filter the 282 Munros, and seed PostGIS.",
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL from the project environment.",
    )
    parser.add_argument(
        "--source-url",
        default=DOBIH_CSV_URL,
        help=f"DoBIH CSV zip URL to download. Defaults to {DOBIH_CSV_URL}.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Download and validate the source count without writing to PostGIS.",
    )
    parser.add_argument(
        "--skip-if-seeded",
        action="store_true",
        help=(
            "Exit successfully without downloading DoBIH when the database already "
            f"contains exactly {EXPECTED_MUNRO_COUNT} Munros."
        ),
    )
    return parser.parse_args()


def download_archive(source_url: str, destination: Path) -> Path:
    request = Request(
        source_url,
        headers={"User-Agent": "MunroStream seed script"},
    )
    with urlopen(request, timeout=60) as response, destination.open("wb") as archive_file:
        shutil.copyfileobj(response, archive_file)
    return destination


def extract_csv(archive_path: Path, destination_dir: Path) -> Path:
    with ZipFile(archive_path) as archive:
        csv_members = sorted(
            member for member in archive.namelist() if member.lower().endswith(".csv")
        )
        if not csv_members:
            raise RuntimeError(f"No CSV file was found in {archive_path.name}.")

        archive.extract(csv_members[0], path=destination_dir)
        return destination_dir / csv_members[0]


def split_name_and_alternatives(raw_name: str) -> tuple[str, list[str]]:
    alternative_names = [
        alternative.strip()
        for alternative in ALTERNATIVE_NAME_PATTERN.findall(raw_name)
        if alternative.strip()
    ]
    name = ALTERNATIVE_NAME_PATTERN.sub("", raw_name).strip()
    return name, alternative_names


def parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    return Decimal(cleaned)


def parse_rows(csv_path: Path) -> list[MunroSeedRow]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise RuntimeError(f"{csv_path.name} does not contain a header row.")

        missing_columns = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise RuntimeError(f"{csv_path.name} is missing required DoBIH columns: {missing}.")

        munros: list[MunroSeedRow] = []
        for row in reader:
            if row["M"].strip() != "1" or row["MT"].strip() != "0":
                continue

            name, alternative_names = split_name_and_alternatives(row["Name"])
            height_metres = parse_decimal(row["Metres"])
            latitude = parse_decimal(row["Latitude"])
            longitude = parse_decimal(row["Longitude"])
            if height_metres is None or latitude is None or longitude is None:
                raise RuntimeError(
                    f"Munro row {row['Number']} is missing height or coordinate data."
                )

            munros.append(
                MunroSeedRow(
                    dobih_id=int(row["Number"]),
                    name=name,
                    alternative_names=alternative_names,
                    height_metres=height_metres,
                    prominence_metres=parse_decimal(row["Drop"]),
                    area=row["Area"].strip() or None,
                    latitude=latitude,
                    longitude=longitude,
                )
            )

    if len(munros) != EXPECTED_MUNRO_COUNT:
        raise RuntimeError(
            "Expected exactly "
            f"{EXPECTED_MUNRO_COUNT} Munros from DoBIH after excluding Munro Tops, "
            f"but found {len(munros)}."
        )

    return munros


def seed_database(database_url: str, munros: list[MunroSeedRow]) -> int:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            if not inspect(connection).has_table("munros"):
                raise RuntimeError(
                    "The munros table does not exist. Initialise the database schema first."
                )

            connection.execute(
                text(
                    "ALTER TABLE munros "
                    "ALTER COLUMN height_metres TYPE NUMERIC(6, 1) "
                    "USING height_metres::NUMERIC(6, 1)"
                )
            )
            connection.execute(INSERT_MUNRO_SQL, [asdict(munro) for munro in munros])
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_munros_geom "
                    "ON munros USING GIST (geom)"
                )
            )

            db_count = connection.execute(text("SELECT COUNT(*) FROM munros")).scalar_one()
            if db_count != EXPECTED_MUNRO_COUNT:
                raise RuntimeError(
                    "Expected exactly "
                    f"{EXPECTED_MUNRO_COUNT} rows in munros after seeding, "
                    f"but found {db_count}."
                )

            return int(db_count)
    finally:
        engine.dispose()


def has_expected_seeded_munros(database_url: str) -> bool:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            if not inspect(connection).has_table("munros"):
                raise RuntimeError(
                    "The munros table does not exist. Initialise the database schema first."
                )

            munro_count = connection.execute(
                text("SELECT COUNT(*) FROM munros WHERE is_munro_top = FALSE")
            ).scalar_one()
            return int(munro_count) == EXPECTED_MUNRO_COUNT
    finally:
        engine.dispose()


def main() -> None:
    load_project_env()
    args = parse_args()
    settings = get_settings()
    database_url = args.database_url or settings.database_url

    if args.skip_if_seeded and has_expected_seeded_munros(database_url):
        print(
            "Munro seed already loaded. "
            f"Found exactly {EXPECTED_MUNRO_COUNT} Munros, so the DoBIH download was skipped."
        )
        return

    with tempfile.TemporaryDirectory(prefix="dobih_") as tmp_dir:
        temp_dir = Path(tmp_dir)
        archive_path = download_archive(args.source_url, temp_dir / "hillcsv.zip")
        csv_path = extract_csv(archive_path, temp_dir)
        munros = parse_rows(csv_path)

    print(
        f"Validated {len(munros)} Munros from {args.source_url} "
        "after excluding Munro Tops."
    )

    if args.verify_only:
        return

    db_count = seed_database(database_url, munros)
    print(f"Seeded PostGIS and verified munros count is exactly {db_count}.")


if __name__ == "__main__":
    main()
