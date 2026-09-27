from pathlib import Path
import csv
import json
import re
import sys
from difflib import SequenceMatcher
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parents[2]

FORM_ID = sys.argv[1] if len(sys.argv) > 1 else "bird_form_001"

SIMPLIFIED_JSON = BASE_DIR / "data" / "simplified_outputs" / f"{FORM_ID}_simplified.json"

MANUAL_CSV_CANDIDATES = [
    BASE_DIR / "data" / "manual_reference" / "manual_reference_fields.csv",
    BASE_DIR / "data" / "manual_reference" / "manual_reference_fields_template.csv",
    BASE_DIR / "bird_form_csv_templates" / "manual_reference_fields_template.csv",
    BASE_DIR / "data" / "bird_form_csv_templates" / "manual_reference_fields_template.csv",
]

OUTPUT_DIR = BASE_DIR / "data" / "evaluation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DETAIL_CSV = OUTPUT_DIR / f"{FORM_ID}_field_evaluation.csv"
OUTPUT_SUMMARY_CSV = OUTPUT_DIR / f"{FORM_ID}_accuracy_summary.csv"


def find_manual_csv() -> Path:
    for path in MANUAL_CSV_CANDIDATES:
        if path.exists():
            return path

    raise FileNotFoundError(
        "Manual reference CSV not found. Put manual_reference_fields.csv in data/manual_reference/"
    )


def normalise(value: str) -> str:
    if value is None:
        return ""

    value = str(value).strip()

    if value == "__BLANK__":
        return ""

    value = value.lower()
    value = re.sub(r"\s+", " ", value)

    return value.strip()

DATE_FIELDS = {"first_date", "last_day"}


def normalise_date(value: str) -> str:
    if value is None:
        return ""

    value = str(value).strip()

    if value == "" or value == "__BLANK__":
        return ""

    # Try common date formats used in the forms and manual reference CSV.
    formats = [
        "%d/%m/%Y",   # 11/02/2026
        "%d/%m/%y",   # 11/2/26
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d%m%Y",     # 11022026
        "%d%m%y",     # 110226
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # If parsing fails, fall back to basic text normalisation.
    return normalise(value)


def similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, normalise(a), normalise(b)).ratio(), 3)


def classify_match(field_name: str, manual_value: str, model_value: str) -> tuple[str, str]:
    manual_norm = normalise(manual_value)
    model_norm = normalise(model_value)

    # Blank field comparison
    if str(manual_value).strip() == "" and model_norm == "":
        return "correct", "blank_match"

    if str(manual_value).strip() == "" and model_norm != "":
        return "incorrect", "expected_blank_but_detected"

    if model_norm == "":
        return "not_detected", "model_value_empty"

    # Date comparison
    if field_name in DATE_FIELDS:
        manual_date = normalise_date(manual_value)
        model_date = normalise_date(model_value)

        if manual_date != "" and manual_date == model_date:
            return "correct", "date_match"

    # Exact comparison
    if manual_norm == model_norm:
        return "correct", "exact_match"

    # Text-heavy fields can be partially correct.
    text_fields = {"name", "location_description", "address", "email"}

    if field_name in text_fields:
        score = similarity(manual_value, model_value)
        if score >= 0.70:
            return "partial", "text_partial_match"

    return "incorrect", "value_mismatch"


def main():
    manual_csv = find_manual_csv()

    with open(SIMPLIFIED_JSON, "r", encoding="utf-8") as file:
        simplified = json.load(file)

    extracted_fields = simplified["extracted_fields"]

    with open(manual_csv, "r", encoding="utf-8-sig", newline="") as file:
        manual_rows = list(csv.DictReader(file))

    detail_rows = []

    for row in manual_rows:
        if row.get("form_id") != FORM_ID:
            continue

        field_name = row.get("field_name", "")

        # Only evaluate fields that exist in the simplified JSON output.
        if field_name not in extracted_fields:
            continue

        manual_value = row.get("manual_value", "")
        model_result = extracted_fields[field_name]
        model_value = str(model_result.get("value", ""))
        confidence = model_result.get("confidence")
        flag = model_result.get("flag")

        match_status, error_type = classify_match(field_name, manual_value, model_value)

        detail_rows.append({
            "form_id": FORM_ID,
            "field_name": field_name,
            "manual_value": manual_value,
            "model_value": model_value,
            "confidence": confidence,
            "flag": flag,
            "match_status": match_status,
            "similarity": similarity(manual_value, model_value),
            "error_type": error_type
        })

    if not detail_rows:
        raise ValueError(
            f"No evaluation rows found: {FORM_ID}. "
            " Check the manual reference"
        )
    
    evaluated_rows = [r for r in detail_rows if r["match_status"] != "not_evaluated"]

    total = len(evaluated_rows)
    correct = sum(1 for r in evaluated_rows if r["match_status"] == "correct")
    partial = sum(1 for r in evaluated_rows if r["match_status"] == "partial")
    incorrect = sum(1 for r in evaluated_rows if r["match_status"] == "incorrect")
    not_detected = sum(1 for r in evaluated_rows if r["match_status"] == "not_detected")

    exact_accuracy = round(correct / total, 3) if total else 0
    relaxed_accuracy = round((correct + partial) / total, 3) if total else 0

    summary_rows = [{
        "form_id": FORM_ID,
        "evaluated_fields": total,
        "correct": correct,
        "partial": partial,
        "incorrect": incorrect,
        "not_detected": not_detected,
        "exact_accuracy": exact_accuracy,
        "relaxed_accuracy_correct_or_partial": relaxed_accuracy
    }]

    with open(OUTPUT_DETAIL_CSV, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=detail_rows[0].keys())
        writer.writeheader()
        writer.writerows(detail_rows)

    with open(OUTPUT_SUMMARY_CSV, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=summary_rows[0].keys())
        writer.writeheader()
        writer.writerows(summary_rows)

    print("Evaluation completed.")
    print(f"Manual reference used: {manual_csv}")
    print(f"Detail CSV saved to: {OUTPUT_DETAIL_CSV}")
    print(f"Summary CSV saved to: {OUTPUT_SUMMARY_CSV}")
    print()
    print(summary_rows[0])


if __name__ == "__main__":
    main()