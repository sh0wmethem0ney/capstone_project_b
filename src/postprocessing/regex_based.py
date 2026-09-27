from pathlib import Path
import json
import re
import sys
from statistics import mean


BASE_DIR = Path(__file__).resolve().parents[2]

FORM_ID = sys.argv[1] if len(sys.argv) > 1 else "bird_form_001"

INPUT_JSON = BASE_DIR / "data" / "azure_results" / f"{FORM_ID}_upper_read.json"

OUTPUT_DIR = BASE_DIR / "data" / "simplified_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_JSON = OUTPUT_DIR / f"{FORM_ID}_simplified.json"


def load_azure_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def get_content(data: dict) -> str:
    return data.get("analyzeResult", {}).get("content", "")


def get_words(data: dict) -> list[dict]:
    words = []

    pages = data.get("analyzeResult", {}).get("pages", [])
    for page in pages:
        for word in page.get("words", []):
            words.append(word)

    return words


def avg_confidence_for_exact_words(words: list[dict], target_text: str):
    
    """
    Estimate confidence by matching OCR words against the target value
    """

    if not target_text:
        return None

    target_parts = target_text.lower().split()
    matched_confidences = []

    for word in words:
        content = word.get("content", "").lower()
        if content in target_parts:
            confidence = word.get("confidence")
            if confidence is not None:
                matched_confidences.append(confidence)

    if not matched_confidences:
        return None

    return round(mean(matched_confidences), 3)


def create_flag(value, confidence):

    """
    initial uncertainty flag
    (extraction-side flagging)
    """

    if value is None or value == "":
        return "blank_or_not_detected"

    if confidence is not None and confidence < 0.7:
        return "low_accuracy"

    if "$" in value or "!" in value:
        return "possible_error"

    return None


def regex_search(pattern: str, text: str):
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def extract_fields(content: str, words: list[dict]) -> dict:
    """
    Extract selected upper-section fields from OCR text.
    This version is rule-based and designed for the upper field section crop.
    """

    member_number = regex_search(r"MEMBER\s+NUMBER:?\s*\n?([0-9]{4,8})", content)

    number_of_species_recorded = regex_search(
        r"SPECIES\s+RECORDED\s*\n?([0-9A-Za-z$]+)",
        content
    )

    name = regex_search(
        r"NAME:?\s*\n?(.+?)(?:\s+ADDRESS\.?|\s+GRID\s+REFERENCE)",
        content
    )

    name = re.sub(r"\s+", " ", name).strip()

    coordinate_block = re.search(
        r"Latitude\s+([0-9]{1,3})\s+([0-9]{1,2})\s+Degrees\s+\.?Mins\s+Longitude\s+([0-9]{1,3})\s+([0-9]{1,2})\s+Degrees\s+\.?Mins",
        content,
        re.IGNORECASE
    )

    if coordinate_block:
        latitude_degrees = coordinate_block.group(1)
        latitude_minutes = coordinate_block.group(2)
        longitude_degrees = coordinate_block.group(3)
        longitude_minutes = coordinate_block.group(4)
    else:
        latitude_degrees = ""
        latitude_minutes = ""
        longitude_degrees = ""
        longitude_minutes = ""

    first_date = regex_search(
        r"First\s+Date\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})",
        content
    )

    last_day = regex_search(
        r"Last\s+Day\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|[0-9]+)",
        content
    )

    location_description = regex_search(
        r"Describe\s+location\s+precisely\.?\s*(.+?)(?:EMAIL|PERIOD|ADDRESS|Postcode)",
        content
    )

    location_description = re.sub(r"\s+", " ", location_description).strip()

    raw_fields = {
        "member_number": member_number,
        "number_of_species_recorded": number_of_species_recorded,
        "name": name,
        "latitude_degrees": latitude_degrees,
        "latitude_minutes": latitude_minutes,
        "longitude_degrees": longitude_degrees,
        "longitude_minutes": longitude_minutes,
        "first_date": first_date,
        "last_day": last_day,
        "location_description": location_description,
    }

    extracted_fields = {}

    for field_name, value in raw_fields.items():
        confidence = avg_confidence_for_exact_words(words, value)
        flag = create_flag(value, confidence)

        extracted_fields[field_name] = {
            "value": value,
            "confidence": confidence,
            "flag": flag
        }

    return extracted_fields


def main():
    data = load_azure_json(INPUT_JSON)
    content = get_content(data)
    words = get_words(data)

    simplified_output = {
        "form_id": FORM_ID,
        "source_file": f"{FORM_ID}.pdf",
        "processed_region": "upper_section",
        "preprocessing": {
            "crop_method": "automatic upper-section crop",
            "crop_bottom_ratio": 0.26,
            "dpi": 300
        },
        "model": {
            "provider": "Azure Document Intelligence",
            "model_id": data.get("analyzeResult", {}).get("modelId", "prebuilt-read")
        },
        "extracted_fields": extract_fields(content, words)
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as file:
        json.dump(simplified_output, file, indent=2, ensure_ascii=False)

    print("Simplified JSON created.")
    print(f"Output saved to: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()