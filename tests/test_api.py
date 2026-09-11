"""API and pipeline tests. All run offline against tests/fixtures/sample_raw.csv.

The fixture has 7 unique stations, 1 duplicate row (differing only by
whitespace), 2 blank rows and 1 station (Altnabreac) whose measurements are
all [z].
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import DATA_PATH_ENV, app
from pipeline.cleaner import (
    SchemaValidationError,
    clean,
    load_raw,
    validate_schema,
)
from tests.conftest import FIXTURE_CSV, FIXTURE_MIN_ROWS

# --- API: health ----------------------------------------------------------


def test_health_ok_reports_loaded_rows(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["rows_loaded"] == 7
    assert body["data_last_modified"] is not None


def test_health_degraded_when_data_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_PATH_ENV, str(tmp_path / "does_not_exist.csv"))

    with TestClient(app) as degraded_client:
        health = degraded_client.get("/health")
        stations = degraded_client.get("/stations")

    assert health.status_code == 503
    assert health.json()["status"] == "degraded"
    assert health.json()["rows_loaded"] == 0
    assert stations.status_code == 503


# --- API: station lookups -------------------------------------------------


def test_lookup_by_three_letter_code_is_case_insensitive(client):
    response = client.get("/stations/lst")

    assert response.status_code == 200
    body = response.json()
    assert body["station_name"] == "London Liverpool Street"
    assert body["three_letter_code_tlc"] == "LST"
    assert body["entries_and_exits"]["all_tickets"] == 98_015_658


def test_lookup_by_national_location_code(client):
    response = client.get("/stations/5131")

    assert response.status_code == 200
    body = response.json()
    assert body["station_name"] == "Abbey Wood"
    assert body["national_location_code_nlc"] == "5131"


def test_lookup_unknown_code_returns_404(client):
    response = client.get("/stations/ZZZ")

    assert response.status_code == 404
    assert "ZZZ" in response.json()["detail"]


# --- API: listings --------------------------------------------------------


def test_list_stations_default_pagination(client):
    response = client.get("/stations", params={"limit": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 7
    assert body["limit"] == 3
    assert body["offset"] == 0
    assert [s["station_name"] for s in body["items"]] == [
        "Abbey Wood",
        "Aberdeen",
        "Altnabreac",
    ]


def test_list_stations_filters_by_region(client):
    response = client.get("/stations", params={"region": "scotland"})

    body = response.json()
    assert body["total"] == 3
    assert {s["region"] for s in body["items"]} == {"Scotland"}


def test_list_stations_filters_by_name(client):
    response = client.get("/stations", params={"name": "london"})

    names = {s["station_name"] for s in response.json()["items"]}
    assert names == {"London Liverpool Street", "London Waterloo"}


def test_list_stations_rejects_out_of_range_limit(client):
    response = client.get("/stations", params={"limit": 501})

    assert response.status_code == 422
    assert "detail" in response.json()


# --- API: analytics -------------------------------------------------------


def test_top_stations_ordered_descending_and_limited(client):
    response = client.get("/analytics/top-stations", params={"n": 3})

    assert response.status_code == 200
    items = response.json()["items"]
    assert [s["three_letter_code_tlc"] for s in items] == ["LST", "WAT", "EDB"]
    values = [s["entries_and_exits"] for s in items]
    assert values == sorted(values, reverse=True)
    assert [s["position"] for s in items] == [1, 2, 3]


def test_top_stations_by_ticket_type_and_region(client):
    response = client.get(
        "/analytics/top-stations",
        params={"n": 5, "region": "Scotland", "ticket_type": "season"},
    )

    body = response.json()
    assert body["ticket_type"] == "season"
    assert [
        (s["three_letter_code_tlc"], s["entries_and_exits"]) for s in body["items"]
    ] == [
        ("EDB", 1_500_000),
        ("ABD", 105_864),
    ]


def test_top_stations_excludes_unreported_stations(client):
    response = client.get("/analytics/top-stations", params={"n": 100})

    body = response.json()
    codes = [s["three_letter_code_tlc"] for s in body["items"]]
    assert body["stations_considered"] == 6
    assert len(codes) == 6
    assert "ABC" not in codes


# --- Pipeline -------------------------------------------------------------


def test_pipeline_dedupes_drops_blank_rows_and_keeps_z_as_missing(pipeline_result):
    stations = pipeline_result.stations

    assert pipeline_result.station_rows == 7
    assert pipeline_result.duplicates_removed == 1
    assert pipeline_result.ticket_rows == 21
    assert pipeline_result.unreported_stations == 1

    altnabreac = stations.loc[stations["three_letter_code_tlc"] == "ABC"].iloc[0]
    assert altnabreac["station_name"] == "Altnabreac"
    assert altnabreac["station_note"] == "[note 1]"
    assert not altnabreac["usage_reported"]
    assert pd.isna(altnabreac["entries_and_exits_all_tickets"])
    assert stations["entries_and_exits_all_tickets"].dtype == "Int64"


def test_validation_rejects_negative_measurement():
    stations, _ = clean(load_raw(FIXTURE_CSV))
    stations.loc[0, "interchanges"] = -1

    with pytest.raises(
        SchemaValidationError, match="Negative values found in interchanges"
    ):
        validate_schema(stations, min_rows=FIXTURE_MIN_ROWS)
