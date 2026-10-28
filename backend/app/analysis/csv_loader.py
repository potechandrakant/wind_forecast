"""Load the committed wind snapshot into analysis-friendly dictionaries."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def load_snapshot(data_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with (data_dir / "actuals_wind.csv").open(newline="", encoding="utf-8") as handle:
        actuals = list(csv.DictReader(handle))
    with (data_dir / "forecasts_wind.csv").open(newline="", encoding="utf-8") as handle:
        forecasts = list(csv.DictReader(handle))
    return actuals, forecasts
