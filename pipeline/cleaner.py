"""Clean, validate and reshape the ORR Table 1410 station-usage CSV.

Run from the repository root:

    python -m pipeline.cleaner                      # download from ORR
    python -m pipeline.cleaner --source data/raw/station_usage.csv

Stages: fetch -> load -> normalise columns -> handle missing values ->
deduplicate -> validate schema -> reshape by ticket type -> validate
reshape -> save both CSVs -> check saved files.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

ORR_URL = (
    "https://dataportal.orr.gov.uk/media/1909/"
    "table-1410-passenger-entries-and-exits-and-interchanges-by-station.csv"
)
DEFAULT_RAW_PATH = Path("data/raw/station_usage.csv")
DEFAULT_OUT_DIR = Path("data/processed")
STATIONS_FILENAME = "station_usage.csv"
TICKETS_FILENAME = "station_usage_by_ticket_type.csv"

# Sanity range for the real ORR file (2,589 stations in 2024-25).
# It is a project check, not an ORR guarantee.
MIN_ROWS = 2000
MAX_ROWS = 3500

MEASUREMENT_COLUMNS = [
    "entries_and_exits_full_price_tickets",
    "entries_and_exits_reduced_price_tickets",
    "entries_and_exits_season_tickets",
    "entries_and_exits_all_tickets",
    "entries_and_exits_rank",
    "interchanges",
    "number_of_journeys_to_or_from_main_origin_or_destination_station",
]

TICKET_COMPONENT_COLUMNS = {
    "entries_and_exits_full_price_tickets": "full_price",
    "entries_and_exits_reduced_price_tickets": "reduced_price",
    "entries_and_exits_season_tickets": "season",
}
TOTAL_COLUMN = "entries_and_exits_all_tickets"

TICKET_OUTPUT_COLUMNS = [
    "station_name",
    "national_location_code_nlc",
    "region",
    "ticket_type",
    "entries_and_exits",
]

NOTE_PATTERN = r"\s*\[note\s*\d+\]"
TLC_PATTERN = r"[A-Z]{3}"
NLC_PATTERN = r"\d+"


@dataclass(frozen=True)
class ColumnSpec:
    kind: str  # "string", "Int64" or "boolean"
    nullable: bool = True


# The 18 source columns plus two derived columns, in output order.
SCHEMA: dict[str, ColumnSpec] = {
    "station_name": ColumnSpec("string", nullable=False),
    "station_note": ColumnSpec("string"),
    "entries_and_exits_full_price_tickets": ColumnSpec("Int64"),
    "entries_and_exits_reduced_price_tickets": ColumnSpec("Int64"),
    "entries_and_exits_season_tickets": ColumnSpec("Int64"),
    "entries_and_exits_all_tickets": ColumnSpec("Int64"),
    "entries_and_exits_rank": ColumnSpec("Int64"),
    "interchanges": ColumnSpec("Int64"),
    "main_origin_or_destination_station": ColumnSpec("string"),
    "number_of_journeys_to_or_from_main_origin_or_destination_station": ColumnSpec(
        "Int64"
    ),
    "data_source_or_adjustments": ColumnSpec("string"),
    "estimates_supplemented_by_local_ticketing_data_or_by_retailing_organisation": (
        ColumnSpec("string")
    ),
    "quality_limitations": ColumnSpec("string"),
    "additional_information": ColumnSpec("string"),
    "national_location_code_nlc": ColumnSpec("string", nullable=False),
    "three_letter_code_tlc": ColumnSpec("string", nullable=False),
    "region": ColumnSpec("string", nullable=False),
    "station_facility_owner": ColumnSpec("string"),
    "station_group": ColumnSpec("string"),
    "usage_reported": ColumnSpec("boolean", nullable=False),
}

SOURCE_COLUMNS = [c for c in SCHEMA if c not in ("station_note", "usage_reported")]
TEXT_COLUMNS = [c for c in SOURCE_COLUMNS if SCHEMA[c].kind == "string"]


class SchemaValidationError(ValueError):
    """Raised when data does not match the expected schema or rules."""


@dataclass
class PipelineResult:
    stations: pd.DataFrame
    tickets: pd.DataFrame
    stations_path: Path
    tickets_path: Path
    duplicates_removed: int

    @property
    def station_rows(self) -> int:
        return len(self.stations)

    @property
    def ticket_rows(self) -> int:
        return len(self.tickets)

    @property
    def unreported_stations(self) -> int:
        return int((~self.stations["usage_reported"]).sum())


def fetch_raw_csv(url: str, raw_path: Path) -> Path:
    """Download the CSV and save the response bytes unchanged."""
    logger.info("Downloading %s", url)
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(response.content)
    logger.info("Saved raw CSV (%d bytes) to %s", len(response.content), raw_path)
    return raw_path


def load_raw(raw_path: Path) -> pd.DataFrame:
    """Read the raw ORR CSV, skipping the three preamble rows.

    [z] means "not applicable" and is read as missing, never as zero.
    The NLC is an identifier, so it is kept as text.
    """
    df = pd.read_csv(
        raw_path,
        skiprows=3,
        thousands=",",
        na_values="[z]",
        dtype={"National Location Code (NLC)": "string"},
    )
    logger.info("Loaded %d raw rows from %s", len(df), raw_path)
    return df


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert headers such as 'Entries and exits:\\nFull price tickets'
    into snake_case such as 'entries_and_exits_full_price_tickets'."""
    df = df.copy()
    df.columns = (
        df.columns.str.strip()
        .str.replace("\n", " ", regex=False)
        .str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
        .str.strip("_")
    )
    return df


def handle_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the missing-value policy.

    - Drop rows only when every field is empty.
    - Strip text and treat blank strings as missing.
    - Keep missing measurements missing (no imputation) and record
      whether a station reported usage in the usage_reported flag.
    - Move "[note N]" markers out of station names into station_note.
    """
    missing = [c for c in SOURCE_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaValidationError(f"Missing required columns: {missing}")

    before = len(df)
    df = df.dropna(how="all").copy()
    logger.info("Removed %d fully empty rows", before - len(df))

    for column in TEXT_COLUMNS:
        text = df[column].astype("string").str.strip()
        df[column] = text.mask(text == "", pd.NA)

    df["three_letter_code_tlc"] = df["three_letter_code_tlc"].str.upper()

    names = df["station_name"]
    df["station_note"] = names.str.extract(r"(\[note\s*\d+\])", expand=False)
    df["station_name"] = names.str.replace(NOTE_PATTERN, "", regex=True).str.strip()

    for column in MEASUREMENT_COLUMNS:
        try:
            df[column] = pd.to_numeric(df[column], errors="raise").astype("Int64")
        except (ValueError, TypeError) as exc:
            raise SchemaValidationError(
                f"Non-numeric data found in {column}: {exc}"
            ) from exc

    df["usage_reported"] = df[TOTAL_COLUMN].notna().astype("boolean")
    unreported = int((~df["usage_reported"]).sum())
    logger.info("%d stations have no reported usage (kept as missing)", unreported)

    return df[list(SCHEMA)].reset_index(drop=True)


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove exact duplicate rows, then repeated NLCs (keeping the first)."""
    before = len(df)
    df = df.drop_duplicates()
    df = df.drop_duplicates(subset=["national_location_code_nlc"], keep="first")
    removed = before - len(df)
    logger.info("Removed %d duplicate station rows", removed)
    return df.reset_index(drop=True), removed


def _dtype_matches(series: pd.Series, kind: str) -> bool:
    if kind == "string":
        return pd.api.types.is_string_dtype(series)
    if kind == "Int64":
        return pd.api.types.is_integer_dtype(series)
    if kind == "boolean":
        return pd.api.types.is_bool_dtype(series)
    raise ValueError(f"Unknown column kind: {kind}")


def validate_schema(
    df: pd.DataFrame, min_rows: int = MIN_ROWS, max_rows: int = MAX_ROWS
) -> None:
    """Check columns, types, nulls, uniqueness, ranges and totals."""
    missing = [c for c in SCHEMA if c not in df.columns]
    if missing:
        raise SchemaValidationError(f"Missing required columns: {missing}")

    if not min_rows <= len(df) <= max_rows:
        raise SchemaValidationError(
            f"Expected between {min_rows} and {max_rows} station rows, found {len(df)}"
        )

    for column, spec in SCHEMA.items():
        series = df[column]
        if not _dtype_matches(series, spec.kind):
            raise SchemaValidationError(
                f"Column {column} should be {spec.kind}, found {series.dtype}"
            )
        if not spec.nullable:
            if series.isna().any():
                raise SchemaValidationError(f"Missing values found in {column}")
            if spec.kind == "string" and series.str.strip().eq("").any():
                raise SchemaValidationError(f"Blank values found in {column}")

    for column in ("national_location_code_nlc", "three_letter_code_tlc"):
        duplicated = df.loc[df[column].duplicated(), column]
        if not duplicated.empty:
            raise SchemaValidationError(
                f"Duplicate values in {column}: {sorted(duplicated.unique())[:5]}"
            )

    bad_tlc = df.loc[~df["three_letter_code_tlc"].str.fullmatch(TLC_PATTERN)]
    if not bad_tlc.empty:
        raise SchemaValidationError(
            f"Invalid three-letter codes: {bad_tlc['three_letter_code_tlc'].tolist()[:5]}"
        )

    bad_nlc = df.loc[~df["national_location_code_nlc"].str.fullmatch(NLC_PATTERN)]
    if not bad_nlc.empty:
        raise SchemaValidationError(
            "Invalid National Location Codes: "
            f"{bad_nlc['national_location_code_nlc'].tolist()[:5]}"
        )

    for column in MEASUREMENT_COLUMNS:
        if (df[column] < 0).any():
            raise SchemaValidationError(f"Negative values found in {column}")

    complete = df[[*TICKET_COMPONENT_COLUMNS, TOTAL_COLUMN]].notna().all(axis=1)
    checked = df.loc[complete]
    component_sum = checked[list(TICKET_COMPONENT_COLUMNS)].sum(axis=1)
    mismatched = checked.loc[component_sum != checked[TOTAL_COLUMN]]
    if not mismatched.empty:
        raise SchemaValidationError(
            "All-ticket totals do not equal the sum of ticket types for: "
            f"{mismatched['station_name'].tolist()[:5]}"
        )

    reported_mismatch = df["usage_reported"] != df[TOTAL_COLUMN].notna()
    if reported_mismatch.any():
        raise SchemaValidationError("usage_reported does not match reported totals")

    logger.info("Station schema validation passed (%d rows)", len(df))


def reshape_ticket_data(df: pd.DataFrame) -> pd.DataFrame:
    """Turn the three ticket-type columns into rows (one per station and type).

    The all-ticket total is excluded because it combines the three types.
    """
    ticket_df = df.melt(
        id_vars=["station_name", "national_location_code_nlc", "region"],
        value_vars=list(TICKET_COMPONENT_COLUMNS),
        var_name="ticket_type",
        value_name="entries_and_exits",
    )
    ticket_df["ticket_type"] = (
        ticket_df["ticket_type"].map(TICKET_COMPONENT_COLUMNS).astype("string")
    )
    ticket_df["entries_and_exits"] = ticket_df["entries_and_exits"].astype("Int64")
    logger.info("Reshaped into %d ticket rows", len(ticket_df))
    return ticket_df


def validate_ticket_data(ticket_df: pd.DataFrame, station_count: int) -> None:
    if list(ticket_df.columns) != TICKET_OUTPUT_COLUMNS:
        raise SchemaValidationError("Unexpected columns in reshaped ticket data")

    expected_rows = station_count * len(TICKET_COMPONENT_COLUMNS)
    if ticket_df.empty or len(ticket_df) != expected_rows:
        raise SchemaValidationError(
            f"Expected {expected_rows} ticket rows, found {len(ticket_df)}"
        )

    for column in ("station_name", "national_location_code_nlc", "ticket_type"):
        if ticket_df[column].isna().any():
            raise SchemaValidationError(f"Missing values found in {column}")
        if ticket_df[column].str.strip().eq("").any():
            raise SchemaValidationError(f"Blank values found in {column}")

    if set(ticket_df["ticket_type"]) != set(TICKET_COMPONENT_COLUMNS.values()):
        raise SchemaValidationError("Unexpected ticket types in reshaped data")

    if ticket_df.duplicated(subset=["national_location_code_nlc", "ticket_type"]).any():
        raise SchemaValidationError("Duplicate station and ticket-type combinations")

    if not pd.api.types.is_integer_dtype(ticket_df["entries_and_exits"]):
        raise SchemaValidationError("Reshaped entries and exits must be integers")

    if (ticket_df["entries_and_exits"] < 0).any():
        raise SchemaValidationError("Negative entries and exits in reshaped data")

    logger.info("Ticket data validation passed (%d rows)", len(ticket_df))


def save_csv(df: pd.DataFrame, path: Path) -> Path:
    """Write to a temporary file, then rename it over the target.

    The rename is atomic on the same filesystem, so the API only ever reads
    the previous file or the complete new one, never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        df.to_csv(tmp, index=False)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    logger.info("Saved %d rows to %s", len(df), path)
    return path


def validate_processed_file(path: Path) -> None:
    if not path.is_file():
        raise SchemaValidationError(f"Processed file not found: {path}")
    if path.stat().st_size == 0:
        raise SchemaValidationError(f"Processed file is empty: {path}")
    logger.info("Processed file check passed: %s", path)


def clean(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Run every cleaning stage on a raw DataFrame."""
    df = normalise_columns(raw_df)
    df = handle_missing(df)
    return deduplicate(df)


def run_pipeline(
    source: str | Path = ORR_URL,
    out_dir: Path = DEFAULT_OUT_DIR,
    raw_path: Path = DEFAULT_RAW_PATH,
    min_rows: int = MIN_ROWS,
    max_rows: int = MAX_ROWS,
) -> PipelineResult:
    """Run the full pipeline. `source` is an http(s) URL or a local CSV path."""
    source_text = str(source)
    if re.match(r"https?://", source_text):
        source_path = fetch_raw_csv(source_text, Path(raw_path))
    else:
        source_path = Path(source_text)
        if not source_path.is_file():
            raise FileNotFoundError(f"Source CSV not found: {source_path}")

    stations, duplicates_removed = clean(load_raw(source_path))
    validate_schema(stations, min_rows=min_rows, max_rows=max_rows)

    tickets = reshape_ticket_data(stations)
    validate_ticket_data(tickets, len(stations))

    # Both tables are validated before either file is written.
    out_dir = Path(out_dir)
    stations_path = save_csv(stations, out_dir / STATIONS_FILENAME)
    validate_processed_file(stations_path)
    tickets_path = save_csv(tickets, out_dir / TICKETS_FILENAME)
    validate_processed_file(tickets_path)

    return PipelineResult(
        stations=stations,
        tickets=tickets,
        stations_path=stations_path,
        tickets_path=tickets_path,
        duplicates_removed=duplicates_removed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default=ORR_URL,
        help="ORR CSV URL or local path (default: the ORR download URL)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Folder for processed CSVs (default: data/processed)",
    )
    parser.add_argument(
        "--raw-path",
        type=Path,
        default=DEFAULT_RAW_PATH,
        help="Where to save a downloaded raw CSV (default: data/raw/station_usage.csv)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        result = run_pipeline(args.source, args.out_dir, args.raw_path)
    except (SchemaValidationError, FileNotFoundError, requests.RequestException):
        logger.exception("Pipeline failed")
        return 1

    logger.info(
        "Pipeline complete: %d stations, %d ticket rows, %d duplicates removed, "
        "%d stations without reported usage",
        result.station_rows,
        result.ticket_rows,
        result.duplicates_removed,
        result.unreported_stations,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
