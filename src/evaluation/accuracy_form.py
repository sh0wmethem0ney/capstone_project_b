from pathlib import Path
import csv
import json
import re
import sys
from difflib import SequenceMatcher
from datetime import datetime


#base project directory
BASE_DIR = Path(__file__).resolve().parents[2]

#simplified JSON directory
#input file format = bird_form_001_simplified.json
SIMPLIFIED_DIR = BASE_DIR / "data" / "simplified_outputs"

#manual reference CSV candidates
#can be added later
MANUAL_CSV_CANDIDATES = [
    BASE_DIR / "data" / "manual_reference" / "manual_reference_fields.csv",
]

#output directory for evaluation results
#field-level result and accuracy summary will be stored here
OUTPUT_DIR = BASE_DIR / "data" / "evaluation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  #create if there is nothing


def discover_form_ids() -> list[str]:

    #automatically find all simplified JSON
    #example file = bird_form_001_simplified.json
    #output form_id = bird_form_001

    form_ids = [
        path.name.removesuffix("_simplified.json")
        for path in sorted(SIMPLIFIED_DIR.glob("bird_form_*_simplified.json"))
    ]

    #if there is no simplified JSON, stop the script
    if not form_ids:
        raise FileNotFoundError(
            f"No simplified JSON files found in: {SIMPLIFIED_DIR}"
        )

    return form_ids


def find_manual_csv() -> Path:

    #find manual reference CSV file
    #script checks multiple possible locations
    #first existing file will be used

    for path in MANUAL_CSV_CANDIDATES:
        if path.exists():
            return path

    #if no manual reference CSV exists, stop the script
    raise FileNotFoundError(
        "Manual reference CSV not found. Put manual_reference_fields.csv in data/manual_reference/"
    )


def normalise(value: str) -> str:

    #basic text normalisation
    #used before comparing manual value and model value
    #lowercase, trim spaces, and remove duplicated spaces

    if value is None:
        return ""

    value = str(value).strip()

    #old blank marker support
    #currently blank cells can be used instead
    if value == "__BLANK__":
        return ""

    value = value.lower()
    value = re.sub(r"\s+", " ", value)

    return value.strip()


#date fields need special comparison
#because 11/02/2026 and 11/2/26 should be treated as the same date
DATE_FIELDS = {"first_date", "last_day"}

#numeric values can have leading zeros, but member number is an ID
NUMERIC_FIELDS = {
    "number_of_species_recorded",
    "latitude_degrees",
    "latitude_minutes",
    "longitude_degrees",
    "longitude_minutes",
}


def normalise_date(value: str) -> str:

    #normalise date value into yyyy-mm-dd format
    #this allows different date formats to be compared fairly

    if value is None:
        return ""

    value = str(value).strip()

    if value == "" or value == "__BLANK__":
        return ""

    #common date formats used in OCR output and manual reference CSV -> can be added later
    formats = [
        "%d/%m/%Y",   #example: 11/02/2026
        "%d/%m/%y",   #example: 11/2/26
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d%m%Y",     #example: 11022026
        "%d%m%y",     #example: 110226
    ]

    #try each date format
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    #if date parsing fails, use basic text normalisation
    return normalise(value)


def similarity(a: str, b: str) -> float:

    #calculate text similarity score
    #used for text-heavy fields such as name and location
    #score range = 0 to 1

    return round(SequenceMatcher(None, normalise(a), normalise(b)).ratio(), 3)


def classify_match(field_name: str, manual_value: str, model_value: str) -> tuple[str, str]:

    #compare one field between manual reference and model output
    #returns match_status and error_type

    manual_norm = normalise(manual_value)
    model_norm = normalise(model_value)

    #blank field comparison
    #if manual is blank and model is also blank, this is correct
    if str(manual_value).strip() == "" and model_norm == "":
        return "correct", "blank_match"

    #if manual should be blank but model detected something, this is incorrect
    if str(manual_value).strip() == "" and model_norm != "":
        return "incorrect", "expected_blank_but_detected"

    #if manual has value but model output is empty, this is not detected
    if model_norm == "":
        return "not_detected", "model_value_empty"

    #date comparison
    #date fields are compared after date normalisation
    if field_name in DATE_FIELDS:
        manual_date = normalise_date(manual_value)
        model_date = normalise_date(model_value)

        if manual_date != "" and manual_date == model_date:
            return "correct", "date_match"

    #exact comparison
    #after normalisation, both values are exactly same
    if manual_norm == model_norm:
        return "correct", "exact_match"

    #compare numeric values without leading zeros
    #example: 5 and 05 are the same number
    #do not use this for member IDs or text fields
    if field_name in NUMERIC_FIELDS:
        if re.fullmatch(r"[0-9]+", manual_norm) and re.fullmatch(r"[0-9]+", model_norm):
            if int(manual_norm) == int(model_norm):
                return "correct", "numeric_match"

    #text-heavy fields can be partially correct
    #may not be perfect but still useful
    text_fields = {"name", "location_description", "address", "email"}

    if field_name in text_fields:
        score = similarity(manual_value, model_value)

        #threshold can be adjusted later
        #currently ***70% or higher*** = partial match
        if score >= 0.70:
            return "partial", "text_partial_match"

    #otherwise it is value mismatch
    return "incorrect", "value_mismatch"


def evaluate_form(form_id: str, manual_rows: list[dict]):

    #evaluate one form
    #input = simplified JSON + manual reference CSV rows
    #output = field-level evaluation CSV + accuracy summary CSV

    simplified_json = (
        SIMPLIFIED_DIR / f"{form_id}_simplified.json"
    )

    output_detail_csv = (
        OUTPUT_DIR / f"{form_id}_field_evaluation.csv"
    )

    output_summary_csv = (
        OUTPUT_DIR / f"{form_id}_accuracy_summary.csv"
    )

    #load simplified JSON output
    with open(simplified_json, "r", encoding="utf-8") as file:
        simplified = json.load(file)

    extracted_fields = simplified["extracted_fields"]

    #store field-level evaluation rows
    detail_rows = []

    for row in manual_rows:

        #only evaluate rows for this form_id
        if row.get("form_id") != form_id:
            continue

        field_name = row.get("field_name", "")

        #DES Ref is outside the current extraction scope
        #warn only if the manual CSV still contains this field
        if field_name in {"des_ref", "disk_entry_sheet_no"}:
            print(f"[WARNING] {form_id}: {field_name} is excluded from evaluation.")
            continue

        #only evaluate fields that exist in simplified JSON output
        #if manual CSV has extra fields, ignore them
        if field_name not in extracted_fields:
            continue

        manual_value = row.get("manual_value", "")

        #model result has value, confidence, and flag
        model_result = extracted_fields[field_name]
        model_value = str(model_result.get("value", ""))
        confidence = model_result.get("confidence")
        flag = model_result.get("flag")

        #classify result for this field
        match_status, error_type = classify_match(field_name, manual_value, model_value)

        #append one field-level evaluation row
        detail_rows.append({
            "form_id": form_id,
            "field_name": field_name,
            "manual_value": manual_value,
            "model_value": model_value,
            "confidence": confidence,
            "flag": flag,
            "match_status": match_status,
            "similarity": similarity(manual_value, model_value),
            "error_type": error_type
        })

    #if no rows are evaluated, usually form_id mismatch or manual CSV issue
    if not detail_rows:
        raise ValueError(
            f"No evaluation rows found: {form_id}. "
            " Check the manual reference"
        )

    #currently all detail rows are evaluated
    #not_evaluated is kept for future extension
    evaluated_rows = [r for r in detail_rows if r["match_status"] != "not_evaluated"]

    total = len(evaluated_rows)

    #count each result category
    correct = sum(1 for r in evaluated_rows if r["match_status"] == "correct")
    partial = sum(1 for r in evaluated_rows if r["match_status"] == "partial")
    incorrect = sum(1 for r in evaluated_rows if r["match_status"] == "incorrect")
    not_detected = sum(1 for r in evaluated_rows if r["match_status"] == "not_detected")

    #exact accuracy = correct only
    exact_accuracy = round(correct / total, 3) if total else 0

    #relaxed accuracy = correct + partial
    #useful for handwriting fields where output can be partially correct
    relaxed_accuracy = round((correct + partial) / total, 3) if total else 0

    #summary row for this form
    summary_rows = [{
        "form_id": form_id,
        "evaluated_fields": total,
        "correct": correct,
        "partial": partial,
        "incorrect": incorrect,
        "not_detected": not_detected,
        "exact_accuracy": exact_accuracy,
        "relaxed_accuracy_correct_or_partial": relaxed_accuracy
    }]

    #save field-level evaluation CSV
    with open(output_detail_csv, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=detail_rows[0].keys())
        writer.writeheader()
        writer.writerows(detail_rows)

    #save accuracy summary CSV
    with open(output_summary_csv, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=summary_rows[0].keys())
        writer.writeheader()
        writer.writerows(summary_rows)

    print("Evaluation completed.")
    print(f"Detail CSV saved to: {output_detail_csv}")
    print(f"Summary CSV saved to: {output_summary_csv}")
    print()
    print(summary_rows[0])


def main():

    #find and load manual reference CSV
    manual_csv = find_manual_csv()

    with open(manual_csv, "r", encoding="utf-8-sig", newline="") as file:
        manual_rows = list(csv.DictReader(file))

    #command line input
    #if user gives form_id, evaluate selected forms only
    #automatically find all simplified JSON
    form_ids = (
        sys.argv[1:]
        if len(sys.argv) > 1
        else discover_form_ids()
    )

    #evaluate forms
    for form_id in form_ids:
        evaluate_form(form_id, manual_rows)


if __name__ == "__main__":
    main()