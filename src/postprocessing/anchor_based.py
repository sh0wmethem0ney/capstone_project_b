from pathlib import Path
import json
import re
import sys
from statistics import mean


BASE_DIR = Path(__file__).resolve().parents[2]
INPUT_DIR = BASE_DIR / "data" / "azure_results"
OUTPUT_DIR = BASE_DIR / "data" / "simplified_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


FORM_IDS = sys.argv[1:] if len(sys.argv) > 1 else ["bird_form_001", "bird_form_002"]


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def bbox_from_polygon(polygon: list[float]) -> tuple[float, float, float, float]:
    xs = polygon[0::2]
    ys = polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def enrich_items(items: list[dict]) -> list[dict]:
    enriched = []

    for item in items:
        polygon = item.get("polygon")
        if not polygon:
            continue

        x1, y1, x2, y2 = bbox_from_polygon(polygon)

        copied = dict(item)
        copied["bbox"] = (x1, y1, x2, y2)
        copied["cx"] = (x1 + x2) / 2
        copied["cy"] = (y1 + y2) / 2

        enriched.append(copied)

    return enriched


def get_words(data: dict) -> list[dict]:
    words = []

    for page in data.get("analyzeResult", {}).get("pages", []):
        words.extend(page.get("words", []))

    return enrich_items(words)


def get_lines(data: dict) -> list[dict]:
    lines = []

    for page in data.get("analyzeResult", {}).get("pages", []):
        lines.extend(page.get("lines", []))

    return enrich_items(lines)


def get_page_size(data: dict) -> tuple[float, float]:
    pages = data.get("analyzeResult", {}).get("pages", [])
    if not pages:
        return 0, 0

    return pages[0].get("width", 0), pages[0].get("height", 0)


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9/]+", " ", str(text).upper()).strip()


def line_contains(line: dict, terms: list[str]) -> bool:
    line_text = norm(line.get("content", ""))
    return all(term.upper() in line_text for term in terms)


def find_line(lines: list[dict], terms: list[str]):
    matches = [line for line in lines if line_contains(line, terms)]

    if not matches:
        return None

    return sorted(matches, key=lambda line: (line["cy"], line["cx"]))[0]


def find_word(words: list[dict], text: str):
    target = norm(text)

    matches = [
        word for word in words
        if norm(word.get("content", "")) == target
    ]

    if not matches:
        return None

    return sorted(matches, key=lambda word: (word["cy"], word["cx"]))[0]


def find_word_startswith(words: list[dict], text: str):
    target = norm(text)

    matches = [
        word for word in words
        if norm(word.get("content", "")).startswith(target)
    ]

    if not matches:
        return None

    return sorted(matches, key=lambda word: (word["cy"], word["cx"]))[0]


def combine_bbox(items: list[dict]) -> tuple[float, float, float, float]:
    x1 = min(item["bbox"][0] for item in items)
    y1 = min(item["bbox"][1] for item in items)
    x2 = max(item["bbox"][2] for item in items)
    y2 = max(item["bbox"][3] for item in items)

    return x1, y1, x2, y2


def find_sequence_anchor(words: list[dict], tokens: list[str]):
    """
    Find anchors such as First Date or Last Day using nearby word positions.
    """

    first_token = norm(tokens[0])

    first_candidates = [
        word for word in words
        if norm(word.get("content", "")) == first_token
    ]

    for first in first_candidates:
        selected = [first]
        current = first

        success = True

        for token in tokens[1:]:
            target = norm(token)

            candidates = [
                word for word in words
                if norm(word.get("content", "")) == target
                and word["cx"] > current["cx"]
                and abs(word["cy"] - current["cy"]) <= 80
            ]

            if not candidates:
                success = False
                break

            next_word = sorted(
                candidates,
                key=lambda word: abs(word["cx"] - current["cx"]) + abs(word["cy"] - current["cy"])
            )[0]

            selected.append(next_word)
            current = next_word

        if success:
            x1, y1, x2, y2 = combine_bbox(selected)
            return {
                "content": " ".join(w["content"] for w in selected),
                "bbox": (x1, y1, x2, y2),
                "cx": (x1 + x2) / 2,
                "cy": (y1 + y2) / 2,
            }

    return None


def words_in_box(
    words: list[dict],
    box: tuple[float, float, float, float],
    pattern: str | None = None,
    exclude_terms: list[str] | None = None
) -> list[dict]:
    x1, y1, x2, y2 = box
    matched = []

    for word in words:
        content = word.get("content", "")

        if not (x1 <= word["cx"] <= x2 and y1 <= word["cy"] <= y2):
            continue

        if exclude_terms:
            if any(term.upper() in norm(content) for term in exclude_terms):
                continue

        if pattern:
            if not re.fullmatch(pattern, content):
                continue

        matched.append(word)

    return sorted(matched, key=lambda word: (word["cy"], word["cx"]))

def sort_reading_order(words: list[dict], y_tolerance: float = 35) -> list[dict]:
    """
    Sort words in a more natural reading order.
    Words with similar y-centres are treated as being on the same line,
    then sorted left to right.
    """

    if not words:
        return []

    words_sorted = sorted(words, key=lambda word: word["cy"])
    lines = []

    for word in words_sorted:
        placed = False

        for line in lines:
            avg_y = sum(w["cy"] for w in line) / len(line)

            if abs(word["cy"] - avg_y) <= y_tolerance:
                line.append(word)
                placed = True
                break

        if not placed:
            lines.append([word])

    lines = sorted(lines, key=lambda line: sum(w["cy"] for w in line) / len(line))

    ordered_words = []

    for line in lines:
        ordered_words.extend(sorted(line, key=lambda word: word["cx"]))

    return ordered_words

def make_value(words: list[dict]) -> tuple[str, float | None]:
    if not words:
        return "", None

    words = sort_reading_order(words)

    value = " ".join(word.get("content", "") for word in words).strip()

    confidences = [
        word.get("confidence")
        for word in words
        if word.get("confidence") is not None
    ]

    confidence = round(mean(confidences), 3) if confidences else None

    return value, confidence


def create_flag(value: str, confidence):
    if value is None or value == "":
        return "blank_or_not_detected"

    if confidence is not None and confidence < 0.7:
        return "low_confidence"

    if "$" in value or "!" in value or ")" in value:
        return "possible_ocr_error"

    return None


def clean_date(value: str) -> str:
    """
    date cleanup for OCR confusion in date fields only.
    Example: 14/5/2) -> 14/5/25
    """

    value = value.strip()

    if value.endswith(")"):
        value = value[:-1] + "5"

    return value


def field_result(value: str, confidence):
    return {
        "value": value,
        "confidence": confidence,
        "flag": create_flag(value, confidence)
    }


def extract_value_right_of_line(
    words: list[dict],
    anchor_line: dict | None,
    x_extra: float,
    y_pad_top: float,
    y_pad_bottom: float,
    pattern: str
):
    if anchor_line is None:
        return "", None

    x1, y1, x2, y2 = anchor_line["bbox"]

    box = (
        x2,
        y1 - y_pad_top,
        x2 + x_extra,
        y2 + y_pad_bottom,
    )

    matched_words = words_in_box(words, box, pattern=pattern)

    return make_value(matched_words)


def extract_name(words: list[dict]):
    name_anchor = find_word_startswith(words, "NAME")

    if name_anchor is None:
        return "", None

    x1, y1, x2, y2 = name_anchor["bbox"]

    # stop before nearby labels
    stop_candidates = []

    for stop_token in ["GRID", "LATITUDE"]:
        stop_word = find_word_startswith(words, stop_token)

        if stop_word:
            # only use stop labels that are to the right of NAME
            # and roughly in the same vertical area.
            if stop_word["bbox"][0] > x2 and abs(stop_word["cy"] - name_anchor["cy"]) <= 180:
                stop_candidates.append(stop_word["bbox"][0])

    max_x = min(stop_candidates) - 20 if stop_candidates else x2 + 700

    box = (
        x2,
        y1 - 50,
        max_x,
        y2 + 90,
    )

    matched_words = words_in_box(
        words,
        box,
        exclude_terms=["ADDRESS", "POSTCODE", "GRID", "REFERENCE", "LATITUDE"]
    )

    value, confidence = make_value(matched_words)

    value = re.split(
        r"\b(?:ADDRESS|POSTCODE|GRID|REFERENCE|LATITUDE)\b",
        value,
        flags=re.IGNORECASE
    )[0].strip()

    return value, confidence


def extract_coordinate_pair(words: list[dict], label: str, next_label: str | None = None):
    label_word = find_word(words, label)

    if label_word is None:
        return ("", None), ("", None)

    next_label_word = find_word(words, next_label) if next_label else None

    x1, y1, x2, y2 = label_word["bbox"]

    if label.lower() == "longitude":
        # lon values are close to the Longitude label.
        # do not search too far
        y_top = y1 - 80
        y_bottom = y2 + 90
    else:
        # values may appear slightly above the Latitude label
        y_top = y1 - 100

        if next_label_word:
            y_bottom = (label_word["cy"] + next_label_word["cy"]) / 2
        else:
            y_bottom = y2 + 100

    box = (
        x2,
        y_top,
        x2 + 650,
        y_bottom,
    )

    numeric_words = words_in_box(
        words,
        box,
        pattern=r"[0-9]{1,3}"
    )

    numeric_words = sorted(numeric_words, key=lambda word: word["cx"])

    if len(numeric_words) >= 2:
        degree_words = [numeric_words[0]]
        minute_words = [numeric_words[1]]
    elif len(numeric_words) == 1:
        degree_words = [numeric_words[0]]
        minute_words = []
    else:
        degree_words = []
        minute_words = []

    return make_value(degree_words), make_value(minute_words)


def extract_date_after_anchor(words: list[dict], tokens: list[str]):
    anchor = find_sequence_anchor(words, tokens)

    if anchor is None:
        return "", None

    x1, y1, x2, y2 = anchor["bbox"]

    box = (
        x2,
        y1 - 70,
        x2 + 450,
        y2 + 80,
    )

    matched_words = words_in_box(
        words,
        box,
        pattern=r"[0-9]{1,2}/[0-9]{1,2}/[0-9A-Za-z\)\(]{1,4}"
    )

    value, confidence = make_value(matched_words)

    return clean_date(value), confidence


def extract_location(words: list[dict], lines: list[dict], page_width: float):
    location_anchor = find_line(lines, ["LOCATION", "DESCRIBE", "LOCATION", "PRECISELY"])

    if location_anchor is None:
        return "", None

    x1, y1, x2, y2 = location_anchor["bbox"]

    box = (
        x1,
        y2 - 5,
        page_width,
        y2 + 140,
    )

    matched_words = words_in_box(
        words,
        box,
        exclude_terms=["EMAIL", "PERIOD", "OBSERVATION"]
    )

    return make_value(matched_words)


def extract_fields_anchor_based(data: dict) -> dict:
    words = get_words(data)
    lines = get_lines(data)
    page_width, page_height = get_page_size(data)

    member_anchor = find_line(lines, ["MEMBER", "NUMBER"])
    species_anchor = find_line(lines, ["SPECIES", "RECORDED"])

    member_number, member_conf = extract_value_right_of_line(
        words,
        member_anchor,
        x_extra=650,
        y_pad_top=100,
        y_pad_bottom=90,
        pattern=r"[0-9]{4,8}"
    )

    species_recorded, species_conf = extract_value_right_of_line(
        words,
        species_anchor,
        x_extra=300,
        y_pad_top=100,
        y_pad_bottom=90,
        pattern=r"[0-9A-Za-z$]+"
    )

    name, name_conf = extract_name(words)

    (lat_deg, lat_deg_conf), (lat_min, lat_min_conf) = extract_coordinate_pair(
        words,
        label="Latitude",
        next_label="Longitude"
    )

    (lon_deg, lon_deg_conf), (lon_min, lon_min_conf) = extract_coordinate_pair(
        words,
        label="Longitude",
        next_label=None
    )

    first_date, first_date_conf = extract_date_after_anchor(words, ["First", "Date"])
    last_day, last_day_conf = extract_date_after_anchor(words, ["Last", "Day"])

    location, location_conf = extract_location(words, lines, page_width)

    return {
        "member_number": field_result(member_number, member_conf),
        "number_of_species_recorded": field_result(species_recorded, species_conf),
        "name": field_result(name, name_conf),
        "latitude_degrees": field_result(lat_deg, lat_deg_conf),
        "latitude_minutes": field_result(lat_min, lat_min_conf),
        "longitude_degrees": field_result(lon_deg, lon_deg_conf),
        "longitude_minutes": field_result(lon_min, lon_min_conf),
        "first_date": field_result(first_date, first_date_conf),
        "last_day": field_result(last_day, last_day_conf),
        "location_description": field_result(location, location_conf),
    }


def process_form(form_id: str):
    input_json = INPUT_DIR / f"{form_id}_upper_read.json"
    output_json = OUTPUT_DIR / f"{form_id}_simplified.json"

    if not input_json.exists():
        raise FileNotFoundError(f"Missing input JSON: {input_json}")

    data = load_json(input_json)

    simplified_output = {
        "form_id": form_id,
        "source_file": f"{form_id}_upper_section.pdf",
        "processed_region": "upper_section",
        "extraction_method": "anchor_based_region",
        "preprocessing": {
            "crop_method": "automatic upper-section crop",
            "crop_bottom_ratio": 0.28,
            "dpi": 300
        },
        "model": {
            "provider": "Azure Document Intelligence",
            "model_id": data.get("analyzeResult", {}).get("modelId", "prebuilt-read")
        },
        "extracted_fields": extract_fields_anchor_based(data)
    }

    with open(output_json, "w", encoding="utf-8") as file:
        json.dump(simplified_output, file, indent=2, ensure_ascii=False)

    print(f"Created: {output_json}")


def main():
    for form_id in FORM_IDS:
        process_form(form_id)


if __name__ == "__main__":
    main()