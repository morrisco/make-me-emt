"""
Parse Anki .apkg files into raw JSON.

Each .apkg → data/harvested/<stem>.json

fields_html  — original content, HTML tags intact
fields_text  — HTML stripped (requires beautifulsoup4; falls back to regex)

The source .apkg is never modified.

Usage:
    python scripts/parse_apkg.py                   # processes data/raw/anki/
    python scripts/parse_apkg.py path/to/deck.apkg
    python scripts/parse_apkg.py --out /other/dir
"""
import argparse
import json
import re
import sqlite3
import tempfile
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
    warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

FIELD_SEP = "\x1f"


def _strip_html(html: str) -> str:
    if _HAS_BS4:
        return BeautifulSoup(html, "html.parser").get_text(separator=" ").strip()
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _field_names_for_model(conn: sqlite3.Connection, model_id: int) -> list[str]:
    """Read field names from the col.models JSON blob (all Anki versions)."""
    try:
        row = conn.execute("SELECT models FROM col").fetchone()
        if row:
            models = json.loads(row[0])
            model = models.get(str(model_id)) or models.get(model_id)
            if model:
                return [f["name"] for f in model.get("flds", [])]
    except Exception:
        pass
    return []


def parse_apkg(apkg_path: Path, out_dir: Path) -> None:
    out_path = out_dir / f"{apkg_path.stem}.json"

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(apkg_path) as zf:
            zf.extractall(tmp)

        db_path = None
        db_version = None
        for candidate_name in ("collection.anki21", "collection.anki2"):
            p = Path(tmp) / candidate_name
            if p.exists():
                db_path, db_version = p, candidate_name
                break

        if db_path is None:
            raise FileNotFoundError(
                f"No collection.anki2/21 found inside {apkg_path.name}"
            )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Cache field-name lookups per model id
        _model_fields: dict[int, list[str]] = {}

        notes = []
        for row in conn.execute(
            "SELECT id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data"
            " FROM notes"
        ):
            mid = row["mid"]
            if mid not in _model_fields:
                _model_fields[mid] = _field_names_for_model(conn, mid)
            field_names = _model_fields[mid]

            raw_parts = row["flds"].split(FIELD_SEP)

            if field_names and len(field_names) == len(raw_parts):
                fields_html = dict(zip(field_names, raw_parts))
            else:
                # Positional fallback: "0", "1", ...
                fields_html = {str(i): v for i, v in enumerate(raw_parts)}

            fields_text = {k: _strip_html(v) for k, v in fields_html.items()}

            notes.append(
                {
                    "note_id": row["id"],
                    "guid": row["guid"],
                    "model_id": mid,
                    "field_names": field_names,
                    "fields_html": fields_html,
                    "fields_text": fields_text,
                    "tags": row["tags"].strip().split() if row["tags"].strip() else [],
                    "mod": row["mod"],
                    # Preserve rarely-used columns so nothing is lost
                    "usn": row["usn"],
                    "sfld": row["sfld"],
                    "csum": row["csum"],
                    "flags": row["flags"],
                    "data": row["data"],
                }
            )

        conn.close()

    output = {
        "meta": {
            "source_file": apkg_path.name,
            "source_type": "anki",
            "db_version": db_version,
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "note_count": len(notes),
        },
        "notes": notes,
    }

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  {apkg_path.name} → {out_path.name}  ({len(notes)} notes)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse .apkg files to raw JSON")
    parser.add_argument(
        "input",
        nargs="?",
        default="data/raw/anki",
        help="Single .apkg file or directory of .apkg files (default: data/raw/anki/)",
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
        apkg_files = [input_path]
    elif input_path.is_dir():
        apkg_files = sorted(input_path.glob("*.apkg"))
    else:
        print(f"Error: {input_path} does not exist")
        return

    if not apkg_files:
        print(f"No .apkg files found in {input_path}")
        return

    print(f"Parsing {len(apkg_files)} .apkg file(s)...")
    for apkg in apkg_files:
        try:
            parse_apkg(apkg, out_dir)
        except Exception as exc:
            print(f"  ERROR {apkg.name}: {exc}")


if __name__ == "__main__":
    main()
