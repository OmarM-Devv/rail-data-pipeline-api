# ADR 0004: Validate everything before writing, and keep the last good data

| | |
|---|---|
| Date recorded | 28/09/2026 |
| Status | Accepted |
| Stakeholders consulted | None (personal project) |

## Context

The API reads `station_usage.csv` once, when it starts, and serves it until
the next restart. Every deploy downloads a fresh copy of ORR Table 1410 and
rebuilds that file first. The upstream file can change without notice: new
columns, a reformatted header, or rows that no longer add up. The ORR site can
also be unreachable during a deploy.

## Options considered

1. **Write as you go, and let the API fail on bad data.** Simplest, but a bad
   download could take the service down.
2. **Clean what can be cleaned and drop rows that fail checks.** Keeps the
   service up, but silently serves an incomplete dataset.
3. **Validate the whole dataset before writing anything; if the refresh fails,
   keep serving the previous file.**

## Decision

Option 3.

- [`pipeline/cleaner.py`](../../pipeline/cleaner.py) validates the station
  table and the reshaped ticket table, including the station-count range,
  code formats, uniqueness and ticket-type totals, before either file is
  written. Any failure exits with code 1. Each file is written to a temporary
  file and renamed into place.
- The deploy script in
  [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) runs
  the pipeline in a one-off container. If the pipeline fails and a previous
  data file exists, it logs a warning and carries on with the previous file.
  If there is no previous file, the deploy fails.
- The API returns 503 from `/health` if the file is missing or unreadable, and
  the deploy rolls back to the previous image if the new container never
  becomes healthy.

## Consequences

- Data that fails validation never replaces the file the API serves.
- An ORR outage or format change degrades to stale data, not downtime.
- Stale data is visible but not alerted on: `/health` reports
  `data_last_modified`, but nothing checks it automatically.
- The first ever deploy has no previous file to fall back on, so it fails if
  the pipeline fails.
- Each output file is written to a temporary `.part` file and then renamed
  over the old one. The rename is atomic on the same filesystem, so a write
  interrupted part-way, for example by a full disk, leaves the previous file
  untouched. The first version wrote files in place, where an interrupted
  write could leave a truncated file that the API would still load; this was
  fixed after that gap was recorded here.
- The two files are replaced one after the other, so a failure between them
  could leave a new station file next to the previous ticket file. The API
  reads only the station file, so it is not affected.

## Supporting links

- `test_validation_rejects_negative_measurement` and
  `test_interrupted_write_keeps_the_previous_file` in
  [`tests/test_api.py`](../../tests/test_api.py)
- `save_csv` in [`pipeline/cleaner.py`](../../pipeline/cleaner.py)
- [Figure 3](../../README.md#figure-3--validation-stops-bad-data) and
  [Figure 5](../../README.md#figure-5--deploy-output-from-the-instance) in the README
