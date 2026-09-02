"""FastAPI service over the cleaned ORR station-usage table.

Run locally from the repository root:

    uvicorn api.app:app --reload

The data file defaults to data/processed/station_usage.csv and can be
overridden with the RAIL_DATA_PATH environment variable.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api import __version__
from pipeline.cleaner import SCHEMA, STATIONS_FILENAME

logger = logging.getLogger(__name__)

DATA_PATH_ENV = "RAIL_DATA_PATH"
DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "processed" / STATIONS_FILENAME
)

TICKET_TYPE_COLUMNS = {
    "all": "entries_and_exits_all_tickets",
    "full_price": "entries_and_exits_full_price_tickets",
    "reduced_price": "entries_and_exits_reduced_price_tickets",
    "season": "entries_and_exits_season_tickets",
}
TicketType = Literal["all", "full_price", "reduced_price", "season"]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    rows_loaded: int
    data_path: str
    data_last_modified: datetime | None = None
    detail: str | None = None


class EntriesAndExits(BaseModel):
    full_price: int | None
    reduced_price: int | None
    season: int | None
    all_tickets: int | None


class Station(BaseModel):
    station_name: str
    station_note: str | None
    national_location_code_nlc: str
    three_letter_code_tlc: str
    region: str
    station_facility_owner: str | None
    station_group: str | None
    usage_reported: bool
    entries_and_exits: EntriesAndExits
    entries_and_exits_rank: int | None
    interchanges: int | None
    main_origin_or_destination_station: str | None
    journeys_to_or_from_main_origin_or_destination: int | None
    data_source_or_adjustments: str | None
    quality_limitations: str | None
    additional_information: str | None


class StationPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Station]


class RegionSummary(BaseModel):
    region: str
    station_count: int
    total_entries_and_exits: int


class TopStation(BaseModel):
    position: int
    station_name: str
    three_letter_code_tlc: str
    national_location_code_nlc: str
    region: str
    entries_and_exits: int
    share_of_total_percent: float = Field(
        description="Share of the filtered total for the chosen ticket type"
    )


class TopStationsResponse(BaseModel):
    ticket_type: TicketType
    region: str | None
    n: int
    stations_considered: int
    items: list[TopStation]


# ---------------------------------------------------------------------------
# Data store
# ---------------------------------------------------------------------------


def _value(value: Any) -> Any:
    """Convert pandas missing values and numpy scalars to plain Python."""
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _read_dtypes() -> dict[str, str]:
    return {column: spec.kind for column, spec in SCHEMA.items()}


class StationStore:
    """Holds the processed station table and lookup indexes in memory."""

    def __init__(self, path: Path):
        self.path = path
        self.df: pd.DataFrame | None = None
        self.last_modified: datetime | None = None
        self.error: str | None = None
        self._by_tlc: dict[str, int] = {}
        self._by_nlc: dict[str, int] = {}

    @property
    def loaded(self) -> bool:
        return self.df is not None

    def load(self) -> None:
        if not self.path.is_file():
            self.error = f"Data file not found: {self.path}"
            logger.warning(self.error)
            return
        try:
            df = pd.read_csv(self.path, dtype=_read_dtypes())
            missing = [c for c in SCHEMA if c not in df.columns]
            if missing:
                raise ValueError(f"Missing columns: {missing}")
        except (ValueError, OSError) as exc:
            self.error = f"Could not load {self.path}: {exc}"
            logger.error(self.error)
            return

        self.df = df.reset_index(drop=True)
        self._by_tlc = {
            code.upper(): i for i, code in enumerate(df["three_letter_code_tlc"])
        }
        self._by_nlc = {
            code: i for i, code in enumerate(df["national_location_code_nlc"])
        }
        self.last_modified = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
        self.error = None
        logger.info("Loaded %d stations from %s", len(df), self.path)

    def require(self) -> pd.DataFrame:
        if self.df is None:
            raise HTTPException(
                status_code=503, detail=self.error or "Station data not loaded"
            )
        return self.df

    def find(self, code: str) -> pd.Series | None:
        df = self.require()
        code = code.strip()
        if code.isdigit():
            index = self._by_nlc.get(code)
        else:
            index = self._by_tlc.get(code.upper())
        return None if index is None else df.iloc[index]


def to_station(row: pd.Series) -> Station:
    r = {key: _value(value) for key, value in row.items()}
    return Station(
        station_name=r["station_name"],
        station_note=r["station_note"],
        national_location_code_nlc=r["national_location_code_nlc"],
        three_letter_code_tlc=r["three_letter_code_tlc"],
        region=r["region"],
        station_facility_owner=r["station_facility_owner"],
        station_group=r["station_group"],
        usage_reported=bool(r["usage_reported"]),
        entries_and_exits=EntriesAndExits(
            full_price=r["entries_and_exits_full_price_tickets"],
            reduced_price=r["entries_and_exits_reduced_price_tickets"],
            season=r["entries_and_exits_season_tickets"],
            all_tickets=r["entries_and_exits_all_tickets"],
        ),
        entries_and_exits_rank=r["entries_and_exits_rank"],
        interchanges=r["interchanges"],
        main_origin_or_destination_station=r["main_origin_or_destination_station"],
        journeys_to_or_from_main_origin_or_destination=r[
            "number_of_journeys_to_or_from_main_origin_or_destination_station"
        ],
        data_source_or_adjustments=r["data_source_or_adjustments"],
        quality_limitations=r["quality_limitations"],
        additional_information=r["additional_information"],
    )


def _filter_region(df: pd.DataFrame, region: str | None) -> pd.DataFrame:
    if region is None:
        return df
    return df.loc[df["region"].str.casefold() == region.strip().casefold()]


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(os.environ.get(DATA_PATH_ENV, DEFAULT_DATA_PATH))
    store = StationStore(path)
    store.load()
    app.state.store = store
    yield


app = FastAPI(
    title="Rail Data Pipeline and API",
    description="Station usage from ORR Table 1410 (April 2024 to March 2025).",
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def get_store(request: Request) -> StationStore:
    return request.app.state.store


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> JSONResponse:
    store = get_store(request)
    body = HealthResponse(
        status="ok" if store.loaded else "degraded",
        version=__version__,
        rows_loaded=0 if store.df is None else len(store.df),
        data_path=str(store.path),
        data_last_modified=store.last_modified,
        detail=store.error,
    )
    return JSONResponse(
        status_code=200 if store.loaded else 503, content=body.model_dump(mode="json")
    )


@app.get("/stations", response_model=StationPage, tags=["stations"])
def list_stations(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    region: str | None = Query(None, description="Exact region, case-insensitive"),
    name: str | None = Query(None, min_length=1, description="Name contains"),
    sort: Literal["name", "usage"] = Query("name"),
) -> StationPage:
    df = _filter_region(get_store(request).require(), region)
    if name is not None:
        df = df.loc[
            df["station_name"].str.contains(name.strip(), case=False, regex=False)
        ]

    if sort == "usage":
        df = df.sort_values(
            "entries_and_exits_all_tickets", ascending=False, na_position="last"
        )
    else:
        df = df.sort_values("station_name", key=lambda s: s.str.casefold())

    page = df.iloc[offset : offset + limit]
    return StationPage(
        total=len(df),
        limit=limit,
        offset=offset,
        items=[to_station(row) for _, row in page.iterrows()],
    )


@app.get("/stations/{code}", response_model=Station, tags=["stations"])
def get_station(request: Request, code: str) -> Station:
    """Look up a station by three-letter code (any case) or numeric NLC."""
    row = get_store(request).find(code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Station not found: {code}")
    return to_station(row)


@app.get("/regions", response_model=list[RegionSummary], tags=["stations"])
def list_regions(request: Request) -> list[RegionSummary]:
    df = get_store(request).require()
    grouped = df.groupby("region").agg(
        station_count=("station_name", "size"),
        total_entries_and_exits=("entries_and_exits_all_tickets", "sum"),
    )
    return [
        RegionSummary(
            region=str(region),
            station_count=int(row.station_count),
            total_entries_and_exits=int(row.total_entries_and_exits),
        )
        for region, row in grouped.sort_index().iterrows()
    ]


@app.get(
    "/analytics/top-stations", response_model=TopStationsResponse, tags=["analytics"]
)
def top_stations(
    request: Request,
    n: int = Query(10, ge=1, le=100),
    region: str | None = Query(None, description="Exact region, case-insensitive"),
    ticket_type: TicketType = Query("all"),
) -> TopStationsResponse:
    """Busiest stations by entries and exits.

    Stations without reported usage ([z] in the source) are excluded rather
    than treated as zero.
    """
    column = TICKET_TYPE_COLUMNS[ticket_type]
    df = _filter_region(get_store(request).require(), region)
    df = df.loc[df["usage_reported"] & df[column].notna()]

    total = int(df[column].sum()) if not df.empty else 0
    top = df.sort_values(column, ascending=False, kind="stable").head(n)

    items = [
        TopStation(
            position=position,
            station_name=row["station_name"],
            three_letter_code_tlc=row["three_letter_code_tlc"],
            national_location_code_nlc=row["national_location_code_nlc"],
            region=row["region"],
            entries_and_exits=int(row[column]),
            share_of_total_percent=round(100 * int(row[column]) / total, 2)
            if total
            else 0.0,
        )
        for position, (_, row) in enumerate(top.iterrows(), start=1)
    ]
    return TopStationsResponse(
        ticket_type=ticket_type,
        region=region,
        n=n,
        stations_considered=len(df),
        items=items,
    )
