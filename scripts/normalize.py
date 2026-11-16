"""
Normalize all harvested sources into data/normalized/questions.json

Unified schema per item:
  id          str   — unique across all sources
  type        "mcq" | "flashcard"
  question    str   — plain text
  choices     [str] — MCQ only: 4 answer options
  correct     int   — MCQ only: 0-based index of correct choice
  answer      str   — flashcard only
  explanation str | null
  difficulty  "easy" | "medium" | "hard" | null
  category    str | null
  tags        [str]
  source      str
  source_id   str | null

Loading order determines dedup priority (first = wins):
  1. LevlPrep   — 2 093 MCQ, richest metadata
  2. EMT116     —   690 MCQ, richest per-choice data
  3. Anki decks — ~4 433 flashcards / cloze

Usage:
    python scripts/normalize.py
    python scripts/normalize.py --out data/normalized/questions.json
"""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Cloze helpers
# ---------------------------------------------------------------------------

# Matches {{c1::answer}} or {{c1::answer::hint}}
_CLOZE_RE = re.compile(r"\{\{c\d+::(.+?)(?:::.*?)?\}\}", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def _cloze_to_qa(text: str):
    """Return (question_with_blanks, answer_string) or None if no cloze found."""
    answers = [m.group(1).strip() for m in _CLOZE_RE.finditer(text)]
    if not answers:
        return None
    question = _CLOZE_RE.sub("_____", text).strip()
    answer = "; ".join(answers)
    return question, answer


def _dedup_key(text: str) -> str:
    """Canonical dedup key: lowercase, no punctuation, collapsed whitespace."""
    t = _PUNCT_RE.sub("", text.lower())
    return _WS_RE.sub(" ", t).strip()


def _norm_diff(d) -> str | None:
    if not d:
        return None
    d = str(d).lower().strip()
    if d in ("easy", "low"):
        return "easy"
    if d in ("medium", "moderate", "mod"):
        return "medium"
    if d in ("hard", "difficult", "high", "expert"):
        return "hard"
    return None


# ---------------------------------------------------------------------------
# Source loaders  (each returns a list of normalized dicts)
# ---------------------------------------------------------------------------

def _load_levlprep(path: Path) -> list[dict]:
    items = []
    for q in json.loads(path.read_text()):
        correct = q["correct"]
        # ~8% of questions are multi-select (correct is a list of indices)
        if isinstance(correct, list):
            q_type = "multi"
            correct_val = [int(i) for i in correct]
        else:
            q_type = "mcq"
            correct_val = int(correct)
        items.append({
            "id": f"lvl-{int(q['id']):05d}",
            "type": q_type,
            "question": q["q"].strip(),
            "choices": [o.strip() for o in q["options"]],
            "correct": correct_val,
            "answer": None,
            "explanation": q.get("explain", "").strip() or None,
            "difficulty": _norm_diff(q.get("diff")),
            "category": q.get("system") or q.get("domain") or None,
            "tags": [t for t in [q.get("domain"), q.get("topic")] if t],
            "source": "levlprep",
            "source_id": str(q["id"]),
        })
    return items


def _load_emt116(paths) -> list[dict]:
    items = []
    for path in sorted(paths):
        data = json.loads(path.read_text())
        chapter_title = data.get("title", "")
        for q in data.get("questions", []):
            choices = [c["text"].strip() for c in q.get("choices", [])]
            correct_letter = (q.get("correctAnswer") or "A").upper()
            correct_idx = max(0, ord(correct_letter) - ord("A"))
            items.append({
                "id": f"emt116-{q['id']}",
                "type": "mcq",
                "question": q["question"].strip(),
                "choices": choices,
                "correct": correct_idx,
                "answer": None,
                "explanation": q.get("explanation", "").strip() or None,
                "difficulty": _norm_diff(q.get("difficulty")),
                "category": chapter_title or None,
                "tags": [t for t in [chapter_title, q.get("topic")] if t],
                "source": "emt116",
                "source_id": q["id"],
            })
    return items


def _load_anki_deck(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    source_name = Path(data["meta"]["source_file"]).stem
    items = []

    for note in data["notes"]:
        fields = note["fields_text"]
        fnames = note["field_names"] or list(fields.keys())
        if not fnames:
            continue

        front_key = fnames[0]
        back_key = fnames[1] if len(fnames) > 1 else None

        front = fields.get(front_key, "").strip()
        back = (fields.get(back_key, "") or "").strip() if back_key else ""

        if not front or len(front) < 6:
            continue

        cloze = _cloze_to_qa(front)
        if cloze:
            question, answer = cloze
            # back often has page refs; keep as explanation if non-trivial
            explanation = back if back and not back.startswith("pg.") else None
        else:
            question = front
            answer = back or None
            explanation = None

        items.append({
            "id": f"anki-{source_name}-{note['note_id']}",
            "type": "flashcard",
            "question": question,
            "choices": None,
            "correct": None,
            "answer": answer,
            "explanation": explanation,
            "difficulty": None,
            "category": None,
            "tags": [t for t in note.get("tags", []) if t],
            "source": source_name,
            "source_id": str(note["note_id"]),
        })
    return items


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate(items: list[dict]) -> tuple[list[dict], int]:
    """
    Keep the first item seen for each normalized question key.
    When a later item is MCQ and the existing one is a flashcard, upgrade.
    Returns (deduped_list, n_removed).
    """
    seen: dict[str, dict] = {}
    for item in items:
        key = _dedup_key(item["question"])
        if key not in seen:
            seen[key] = item
        elif item["type"] == "mcq" and seen[key]["type"] == "flashcard":
            seen[key] = item

    deduped = list(seen.values())
    return deduped, len(items) - len(deduped)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Normalize all sources → questions.json")
    parser.add_argument("--base", default="/home/kali/dev/make-me-emt")
    parser.add_argument("--out", default="data/normalized/questions.json")
    args = parser.parse_args()

    base = Path(args.base)
    raw_json = base / "data/raw/json"
    harvested = base / "data/harvested"
    out_path = base / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_items: list[dict] = []

    # --- MCQ sources (loaded first → win dedup) ---
    levl = _load_levlprep(raw_json / "levlprep_nremt_2093.json")
    all_items += levl
    print(f"LevlPrep NREMT      {len(levl):>5} MCQ       running total: {len(all_items)}")

    emt116_paths = sorted(raw_json.glob("emt116_chapter*.json"))
    emt116 = _load_emt116(emt116_paths)
    all_items += emt116
    print(f"EMT116 ch.1-14      {len(emt116):>5} MCQ       running total: {len(all_items)}")

    # --- Anki decks ---
    for deck_path in sorted(harvested.glob("*.json")):
        deck = _load_anki_deck(deck_path)
        all_items += deck
        print(f"{deck_path.stem[:30]:<30}  {len(deck):>5} flashcard  running total: {len(all_items)}")

    # --- Dedup ---
    before = len(all_items)
    all_items, n_removed = _deduplicate(all_items)
    print(f"\nDedup: removed {n_removed} ({before} → {len(all_items)})")

    mcq_count = sum(1 for i in all_items if i["type"] == "mcq")
    multi_count = sum(1 for i in all_items if i["type"] == "multi")
    fc_count = sum(1 for i in all_items if i["type"] == "flashcard")
    print(f"  MCQ: {mcq_count}   Multi-select: {multi_count}   Flashcard: {fc_count}   Total: {len(all_items)}")

    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(all_items),
            "mcq_count": mcq_count,
            "multi_count": multi_count,
            "flashcard_count": fc_count,
        },
        "questions": all_items,
    }

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nWrote → {out_path}  ({out_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
