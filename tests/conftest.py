from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import DATA_PATH_ENV, app
from pipeline.cleaner import PipelineResult, run_pipeline

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_raw.csv"

# The fixture holds 7 unique stations, far below the real-data sanity range.
FIXTURE_MIN_ROWS = 5


@pytest.fixture(scope="session")
def pipeline_result(tmp_path_factory) -> PipelineResult:
    out_dir = tmp_path_factory.mktemp("processed")
    return run_pipeline(FIXTURE_CSV, out_dir=out_dir, min_rows=FIXTURE_MIN_ROWS)


@pytest.fixture
def client(pipeline_result, monkeypatch):
    monkeypatch.setenv(DATA_PATH_ENV, str(pipeline_result.stations_path))
    with TestClient(app) as test_client:
        yield test_client
