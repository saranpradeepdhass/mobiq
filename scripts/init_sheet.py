"""Create the Mobiq worksheets (headers only, or with clearly marked SAMPLE rows) or export CSV templates.

Usage (from the project root):
  python scripts/init_sheet.py --csv-dir sheets_template            # offline: write header+sample CSVs
  python scripts/init_sheet.py --init                               # create missing worksheets + headers
  python scripts/init_sheet.py --init --sample                      # ...and append SAMPLE data (empty sheets only)
Credentials are read from .streamlit/secrets.toml ([gsheets]); nothing is printed except sheet names.
"""
from __future__ import annotations

import argparse
import csv
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.models import TABLES  # noqa: E402
from sample_data import sample_rows  # noqa: E402


def write_csvs(out: Path, sample: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = sample_rows() if sample else {}
    for table, (_, headers) in TABLES.items():
        with open(out / f"{table}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            for r in rows.get(table, []):
                w.writerow([r.get(h, "") for h in headers])
        print(f"wrote {table}.csv")


def init_sheet(sample: bool) -> None:
    import gspread

    cfg = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))["gsheets"]
    ss = gspread.service_account_from_dict(cfg["service_account"]).open_by_key(cfg["spreadsheet_id"])
    existing = {ws.title: ws for ws in ss.worksheets()}
    rows = sample_rows() if sample else {}
    for table, (_, headers) in TABLES.items():
        ws = existing.get(table) or ss.add_worksheet(title=table, rows=200, cols=len(headers))
        if not ws.get_all_values():
            ws.update(range_name="A1", values=[headers], value_input_option="RAW")
            ws.freeze(rows=1)
            print(f"created headers: {table}")
        if sample and len(ws.get_all_values()) == 1 and rows.get(table):
            ws.append_rows([[r.get(h, "") for h in headers] for r in rows[table]], value_input_option="RAW")
            print(f"appended SAMPLE rows: {table}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", type=Path)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()
    if a.csv_dir:
        write_csvs(a.csv_dir, a.sample)
    if a.init:
        init_sheet(a.sample)
    if not (a.csv_dir or a.init):
        ap.print_help()
