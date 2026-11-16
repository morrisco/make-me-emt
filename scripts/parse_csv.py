"""
Parse CSV/TSV files into raw JSON.

Each file → data/harvested/<stem>.json

Delimiter is auto-detected (tab vs comma). All column values are
preserved exactly as-is. The source file is never modified.

Handles:
  - .csv  comma-separated
  - .tsv  tab-separated
  - .txt  auto-detected (common Quizlet / StudyStack export format)

Usage:
    python scripts/parse_csv.py                    # processes data/raw/csv/
    python scripts/parse_csv.py path/to/file.csv
    python scripts/parse_csv.py --out /other/dir
"""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

_CSV_EXTENSIONS = {".csv", ".tsv", ".txt"}


def _detect_delimiter(sample: str) -> str:
    """Prefer tab if it appears at least as often as comma in the first 4 KB."""
    tab_count = sample.count("\t")
    comma_count = sample.count(",")
    return "\t" if tab_count >= comma_count and tab_count > 0 else ","


def parse_csv_file(file_path: Path, out_dir: Path) -> None:
    out_path = out_dir / f"{file_path.stem}.json"

    # utf-8-sig handles files exported with a BOM (common from Excel / Quizlet)
    text = file_path.read_text(encoding="utf-8-sig", errors="replace")
    delimiter = _detect_delimiter(text[:4096])

    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    rows = [dict(row) for row in reader]

    # reader.fieldnames is populated after iterating
    columns = list(reader.fieldnames) if reader.fieldnames else []

    output = {
        "meta": {
            "source_file": file_path.name,
            "source_type": "csv",
            "delimiter": "tab" if delimiter == "\t" else "comma",
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "columns": columns,
            "row_count": len(rows),
        },
        "rows": rows,
    }

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  {file_path.name} → {out_path.name}  ({len(rows)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse CSV/TSV files to raw JSON")
    parser.add_argument(
        "input",
        nargs="?",
        default="data/raw/csv",
        help="Single file or directory of CSV/TSV files (default: data/raw/csv/)",
    )
    parser.add_argument(
        "--out",
        default="data/harvested",
        help="Output directory (default: data/harvested/)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(
            f for f in input_path.iterdir() if f.suffix.lower() in _CSV_EXTENSIONS
        )
    else:
        print(f"Error: {input_path} does not exist")
        return

    if not files:
        print(f"No CSV/TSV files found in {input_path}")
        return

    print(f"Parsing {len(files)} file(s)...")
    for f in files:
        try:
            parse_csv_file(f, out_dir)
        except Exception as exc:
            print(f"  ERROR {f.name}: {exc}")


if __name__ == "__main__":
    main()
