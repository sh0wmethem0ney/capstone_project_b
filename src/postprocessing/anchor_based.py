from pathlib import Path
import json
import re
import sys
from statistics import mean

#base directory
#this finds the project root based on this script location
#parents[2] -> src/postprocessing/thisfile.py -> capstone project root
BASE_DIR = Path(__file__).resolve().parents[2]

#input directory for Azure raw JSON files
#expected file format = bird_form_001_upper_read.json
INPUT_DIR = BASE_DIR / "data" / "azure_results"

#output directory for simplified JSON
#this is where final processed outputs will be stored
OUTPUT_DIR = BASE_DIR / "data" / "simplified_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  #create output folder if there is nothing


def discover_form_ids() -> list[str]:

    #automatically find all available Azure JSON files
    #if no form_id is given in command, this function detects all
    #example file = bird_form_001_upper_read.json
    #output form_id = bird_form_001

    return [
        path.name.removesuffix("_upper_read.json")
        for path in sorted(INPUT_DIR.glob("bird_form_*_upper_read.json"))
    ]


#command line input
#if user runs: python anchor_based_simplified_json.py bird_form_001 bird_form_002
#then FORM_IDS = ["bird_form_001", "bird_form_002"]
#if user runs without form_id, it automatically discovers all JSON
FORM_IDS = sys.argv[1:] if len(sys.argv) > 1 else discover_form_ids()


def load_json(path: Path) -> dict:

    #load Azure raw OCR JSON file
    #returns Python dictionary

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def bbox_from_polygon(polygon: list[float]) -> tuple[float, float, float, float]:

    #Azure gives polygon points like [x1, y1, x2, y2, x3, y3, x4, y4]
    #this function converts polygon points to simple bounding box
    #bbox format = left, top, right, bottom

    xs = polygon[0::2]  #take x values only
    ys = polygon[1::2]  #take y values only

    return min(xs), min(ys), max(xs), max(ys)


def enrich_items(items: list[dict]) -> list[dict]:

    #Azure words/lines have polygon, but using polygon directly is annoying
    #this function adds:
    #bbox = simple rectangle
    #cx = center x
    #cy = center y
    #these values make region-based extraction easier

    enriched = []

    for item in items:
        polygon = item.get("polygon")

        #skip if there is no polygon
        #without polygon, we cannot use coordinate-based extraction
        if not polygon:
            continue

        x1, y1, x2, y2 = bbox_from_polygon(polygon)

        copied = dict(item)  #copy original word/line data
        copied["bbox"] = (x1, y1, x2, y2)
        copied["cx"] = (x1 + x2) / 2
        copied["cy"] = (y1 + y2) / 2

        enriched.append(copied)

    return enriched


def get_words(data: dict) -> list[dict]:

    #extract all word-level OCR results from Azure JSON
    #Azure Read model returns word content, confidence, and polygon
    #word-level output is used for actual field extraction

    words = []

    for page in data.get("analyzeResult", {}).get("pages", []):
        words.extend(page.get("words", []))

    return enrich_items(words)


def get_lines(data: dict) -> list[dict]:

    #extract all line-level OCR results from Azure JSON
    #line-level output is useful for finding printed labels such as MEMBER NUMBER or LOCATION
    #anchors are usually easier to find from lines than individual words

    lines = []

    for page in data.get("analyzeResult", {}).get("pages", []):
        lines.extend(page.get("lines", []))

    return enrich_items(lines)


def get_page_size(data: dict) -> tuple[float, float]:

    #get page width and height from Azure JSON
    #this is useful when defining search regions that go to the page edge

    pages = data.get("analyzeResult", {}).get("pages", [])

    if not pages:
        return 0, 0

    return pages[0].get("width", 0), pages[0].get("height", 0)


def norm(text: str) -> str:

    #normalise text for easier comparison
    #convert to upper case
    #remove symbols except numbers and slash
    #example: "NUMBER:" -> "NUMBER"

    return re.sub(r"[^A-Z0-9/]+", " ", str(text).upper()).strip()


def line_contains(line: dict, terms: list[str]) -> bool:

    #check whether a line contains all required terms
    #example terms = ["MEMBER", "NUMBER"]
    #line content should include both words

    line_text = norm(line.get("content", ""))

    return all(term.upper() in line_text for term in terms)


def find_line(lines: list[dict], terms: list[str]):

    #find line anchor from OCR lines
    #example:
    #terms = ["SPECIES", "RECORDED"]
    #then find line containing both SPECIES and RECORDED

    matches = [line for line in lines if line_contains(line, terms)]

    if not matches:
        return None

    #if multiple matches exist, choose the top-left one
    return sorted(matches, key=lambda line: (line["cy"], line["cx"]))[0]


def find_word(words: list[dict], text: str):

    #find exact word match from OCR words
    #example: find_word(words, "Latitude")
    #used for labels such as Latitude and Longitude

    target = norm(text)

    matches = [
        word for word in words
        if norm(word.get("content", "")) == target
    ]

    if not matches:
        return None

    #if multiple same words exist, choose the first one from top-left order
    return sorted(matches, key=lambda word: (word["cy"], word["cx"]))[0]


def find_word_startswith(words: list[dict], text: str):

    #find word that starts with given text
    #useful when OCR reads NAME: as NAME or NAME:
    #example: "NAME:" can still be matched with "NAME"

    target = norm(text)

    matches = [
        word for word in words
        if norm(word.get("content", "")).startswith(target)
    ]

    if not matches:
        return None

    return sorted(matches, key=lambda word: (word["cy"], word["cx"]))[0]


def combine_bbox(items: list[dict]) -> tuple[float, float, float, float]:

    #combine multiple word bounding boxes into one bigger box
    #used for anchors like First Date or Last Day
    #because they are two words, not one word

    x1 = min(item["bbox"][0] for item in items)
    y1 = min(item["bbox"][1] for item in items)
    x2 = max(item["bbox"][2] for item in items)
    y2 = max(item["bbox"][3] for item in items)

    return x1, y1, x2, y2


def find_sequence_anchor(words: list[dict], tokens: list[str]):

    #find multi-word anchor using nearby word positions
    #example:
    #tokens = ["First", "Date"]
    #find "First" first, then find "Date" near the right side
    #this is better than full text regex because OCR reading order can change

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

            #candidate should be to the right of current word
            #roughly on the same horizontal line
            candidates = [
                word for word in words
                if norm(word.get("content", "")) == target
                and word["cx"] > current["cx"]
                and abs(word["cy"] - current["cy"]) <= 80
            ]

            if not candidates:
                success = False
                break

            #choose the closest next word
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

    #extract words inside a specific coordinate box
    #box format = x1, y1, x2, y2
    #optional pattern can filter values like numbers or dates only
    #exclude_terms can remove unwanted label words such as ADDRESS or EMAIL

    x1, y1, x2, y2 = box
    matched = []

    for word in words:
        content = word.get("content", "")

        #check if word centre is inside the box
        if not (x1 <= word["cx"] <= x2 and y1 <= word["cy"] <= y2):
            continue

        #remove unwanted words
        #example: name field should not include ADDRESS or POSTCODE
        if exclude_terms:
            if any(term.upper() in norm(content) for term in exclude_terms):
                continue

        #if pattern is given, only accept matching words
        #example pattern = numbers only
        if pattern:
            if not re.fullmatch(pattern, content):
                continue

        matched.append(word)

    return sorted(matched, key=lambda word: (word["cy"], word["cx"]))


def sort_reading_order(words: list[dict], y_tolerance: float = 35) -> list[dict]:

    #sort words in more natural reading order
    #words with similar y-centres are treated as same line
    #then words in the same line are sorted left to right
    #this helps to avoid output like "Head Hat" instead of "Hat Head"

    if not words:
        return []

    words_sorted = sorted(words, key=lambda word: word["cy"])
    lines = []

    for word in words_sorted:
        placed = False

        for line in lines:
            avg_y = sum(w["cy"] for w in line) / len(line)

            #if y position is close enough, treat as same line
            if abs(word["cy"] - avg_y) <= y_tolerance:
                line.append(word)
                placed = True
                break

        #if word does not belong to existing line, make a new line
        if not placed:
            lines.append([word])

    #sort lines from top to bottom
    lines = sorted(lines, key=lambda line: sum(w["cy"] for w in line) / len(line))

    ordered_words = []

    for line in lines:
        #inside each line, sort left to right
        ordered_words.extend(sorted(line, key=lambda word: word["cx"]))

    return ordered_words


def make_value(words: list[dict]) -> tuple[str, float | None]:

    #combine selected words into one field value
    #also calculate field-level confidence by averaging Azure word confidence scores

    if not words:
        return "", None

    words = sort_reading_order(words)

    value = " ".join(word.get("content", "") for word in words).strip()

    confidences = [
        word.get("confidence")
        for word in words
        if word.get("confidence") is not None
    ]

    #field confidence = average of word confidences
    confidence = round(mean(confidences), 3) if confidences else None

    return value, confidence


def create_flag(value: str, confidence):

    #create initial uncertainty flag
    #confidence number is from Azure OCR
    #but flag name itself is assigned by this Python script

    if value is None or value == "":
        return "blank_or_not_detected"

    #threshold can be adjusted later
    #currently confidence lower than 0.7 = low confidence
    if confidence is not None and confidence < 0.7:
        return "low_confidence"

    #simple rule to catch likely OCR errors
    #example: 75 recognised as 7$
    #example: 14/5/25 recognised as 14/5/2)
    if "$" in value or "!" in value or ")" in value:
        return "possible_ocr_error"

    return None


def clean_date(value: str) -> str:

    #limited date cleanup for OCR confusion
    #only used for date fields
    #example: 14/5/2) -> 14/5/25

    value = value.strip()

    if value.endswith(")"):
        value = value[:-1] + "5"

    return value


def field_result(value: str, confidence):

    #standard field output structure
    #each extracted field has value, confidence, and flag
    #this makes output consistent for all fields

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

    #generic function to extract value located near the right side of a printed label line
    #example:
    #anchor = MEMBER NUMBER
    #value is expected to be on the right side of this anchor
    #x_extra and y_pad control the search region size

    if anchor_line is None:
        return "", None

    x1, y1, x2, y2 = anchor_line["bbox"]

    #create search box based on anchor position
    #start from right side of anchor
    box = (
        x2,
        y1 - y_pad_top,
        x2 + x_extra,
        y2 + y_pad_bottom,
    )

    matched_words = words_in_box(words, box, pattern=pattern)

    return make_value(matched_words)


def extract_species_count(words: list[dict], lines: list[dict], species_anchor: dict | None):

    #extract one species count next to the SPECIES RECORDED label
    #the old box also included latitude minutes on some forms
    #keep the search above GRID REFERENCE and before the breeding section

    if species_anchor is None:
        return "", None

    x1, y1, x2, y2 = species_anchor["bbox"]
    grid_anchor = find_line(lines, ["GRID", "REFERENCE"])
    breeding_anchor = find_line(lines, ["BREEDING", "SPECIES"])

    right = x2 + 330
    if breeding_anchor and breeding_anchor["bbox"][0] > x2:
        right = min(right, breeding_anchor["bbox"][0] - 20)

    bottom = y2 + 40
    if grid_anchor and grid_anchor["bbox"][1] > y1:
        bottom = min(bottom, grid_anchor["bbox"][1] - 12)

    box = (x2 + 5, y1 - 90, right, bottom)
    candidates = words_in_box(words, box, pattern=r"[0-9][0-9A-Za-z$!]{0,3}")

    #only return one word, e.g. 43 instead of 43 25
    #keep possible OCR errors such as 7$ for later evaluation
    if not candidates:
        return "", None

    candidates = sorted(
        candidates,
        key=lambda word: (word["cx"], abs(word["cy"] - species_anchor["cy"]))
    )
    return make_value([candidates[0]])


def extract_name(words: list[dict]):

    #extract name field using NAME anchor
    #name value is expected to be on the right side of NAME label
    #need to stop before GRID or LATITUDE labels
    #otherwise name may include unrelated labels such as Latitude

    name_anchor = find_word_startswith(words, "NAME")

    if name_anchor is None:
        return "", None

    x1, y1, x2, y2 = name_anchor["bbox"]

    #stop before nearby labels
    stop_candidates = []

    for stop_token in ["GRID", "LATITUDE"]:
        stop_word = find_word_startswith(words, stop_token)

        if stop_word:
            #only use stop labels that are to the right of NAME
            #and roughly in the same vertical area
            if stop_word["bbox"][0] > x2 and abs(stop_word["cy"] - name_anchor["cy"]) <= 180:
                stop_candidates.append(stop_word["bbox"][0])

    #if stop label found, stop before that label
    #if not, use default width
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

    #extra cleanup just in case unwanted labels are included in the value
    value = re.split(
        r"\b(?:ADDRESS|POSTCODE|GRID|REFERENCE|LATITUDE)\b",
        value,
        flags=re.IGNORECASE
    )[0].strip()

    return value, confidence


def extract_coordinate_pair(words: list[dict], label: str, next_label: str | None = None):

    #extract degrees and minutes next to Latitude or Longitude
    #allow trailing punctuation from OCR, e.g. 153, -> 153
    #do not guess missing digits if Azure reads a number incorrectly

    label_word = find_word(words, label)

    if label_word is None:
        return ("", None), ("", None)

    next_label_word = find_word(words, next_label) if next_label else None
    x1, y1, x2, y2 = label_word["bbox"]

    if label.lower() == "longitude":

        #limit longitude search to its own row
        y_top = y1 - 80
        y_bottom = y2 + 90

    else:

        #latitude numbers can appear above the printed label
        y_top = y1 - 100
        if next_label_word:
            y_bottom = (label_word["cy"] + next_label_word["cy"]) / 2
        else:
            y_bottom = y2 + 100

    box = (x2, y_top, x2 + 650, y_bottom)

    #accept up to three digits followed by optional punctuation
    numeric_words = words_in_box(words, box, pattern=r"[0-9]{1,3}[,.;:]?")
    numeric_words = sorted(numeric_words, key=lambda word: word["cx"])

    #keep the first two values as degree and minutes
    #clean a copy so the original Azure JSON is unchanged
    selected = []
    for word in numeric_words[:2]:
        cleaned = dict(word)
        cleaned["content"] = word["content"].rstrip(",.;:")
        selected.append(cleaned)

    degree_words = selected[:1]
    minute_words = selected[1:2]

    return make_value(degree_words), make_value(minute_words)


def extract_date_after_anchor(words: list[dict], tokens: list[str]):

    #extract a date to the right of First Date or Last Day
    #support dates split across two OCR words, e.g. 11/6/ + 25
    #keep the date inside its own label area

    anchor = find_sequence_anchor(words, tokens)
    if anchor is None:
        return "", None

    x1, y1, x2, y2 = anchor["bbox"]
    right = x2 + 450

    #do not include the Last Day value in the First Date search
    if tokens[0].lower() == "first":
        last_anchor = find_sequence_anchor(words, ["Last", "Day"])
        if last_anchor and last_anchor["bbox"][0] > x2:
            right = min(right, last_anchor["bbox"][0] - 5)

    box = (x2, y1 - 70, right, y2 + 80)
    candidates = sorted(words_in_box(words, box), key=lambda word: word["cx"])

    for index, word in enumerate(candidates):
        value = word.get("content", "").strip()

        #full date, including OCR text like 14/5/2)
        if re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}/[0-9A-Za-z()]{1,4}", value):
            return clean_date(value), word.get("confidence")

        #join a split year only if the next word is nearby
        if re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}/", value):
            for next_word in candidates[index + 1:]:
                next_value = next_word.get("content", "").strip()
                gap = next_word["bbox"][0] - word["bbox"][2]
                if gap > 100:
                    break
                if (0 <= gap <= 100
                        and abs(next_word["cy"] - word["cy"]) <= 50
                        and re.fullmatch(r"[0-9]{2,4}", next_value)):
                    return join_date_words([word, next_word])

    return "", None


def join_date_words(words: list[dict]) -> tuple[str, float | None]:

    #join split OCR date words without a space
    #average their Azure confidence scores as one field
    value = "".join(word["content"].strip() for word in words)
    confidences = [word.get("confidence") for word in words if word.get("confidence") is not None]
    confidence = round(mean(confidences), 3) if confidences else None
    return clean_date(value), confidence


def extract_location(words: list[dict], lines: list[dict], page_width: float):

    #extract location description
    #first find LOCATION - Describe location
    #then extract handwritten words below/near that anchor
    #search box goes to the right edge of the page

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

    #main extraction function
    #this function reads Azure JSON output and extracts all target fields
    #using anchor-based region extraction
    #not using full text order because Azure OCR reading order can change between forms

    words = get_words(data)
    lines = get_lines(data)
    page_width, page_height = get_page_size(data)

    #find main printed label anchors
    member_anchor = find_line(lines, ["MEMBER", "NUMBER"])
    species_anchor = find_line(lines, ["SPECIES", "RECORDED"])

    #extract member number from right side of MEMBER NUMBER anchor
    member_number, member_conf = extract_value_right_of_line(
        words,
        member_anchor,
        x_extra=650,
        y_pad_top=100,
        y_pad_bottom=90,
        pattern=r"[0-9]{4,8}"
    )

    #extract only one species count near its printed label
    species_recorded, species_conf = extract_species_count(words, lines, species_anchor)

    #extract name using NAME anchor and stop labels
    name, name_conf = extract_name(words)

    #extract latitude degree/minutes
    (lat_deg, lat_deg_conf), (lat_min, lat_min_conf) = extract_coordinate_pair(
        words,
        label="Latitude",
        next_label="Longitude"
    )

    #extract longitude degree/minutes
    (lon_deg, lon_deg_conf), (lon_min, lon_min_conf) = extract_coordinate_pair(
        words,
        label="Longitude",
        next_label=None
    )

    #extract dates
    first_date, first_date_conf = extract_date_after_anchor(words, ["First", "Date"])
    last_day, last_day_conf = extract_date_after_anchor(words, ["Last", "Day"])

    #extract location
    location, location_conf = extract_location(words, lines, page_width)

    #return all extracted fields in standard simplified JSON format
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

    #process one form
    #input = Azure OCR raw JSON
    #output = simplified JSON with extracted fields

    input_json = INPUT_DIR / f"{form_id}_upper_read.json"
    output_json = OUTPUT_DIR / f"{form_id}_simplified.json"

    if not input_json.exists():
        raise FileNotFoundError(f"Missing input JSON: {input_json}")

    data = load_json(input_json)

    #final simplified JSON structure
    #this is project-specific output, not raw Azure output
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

    #process all selected forms
    #FORM_IDS can come from command line input
    #or automatically discovered from available Azure raw JSON files

    for form_id in FORM_IDS:
        process_form(form_id)


if __name__ == "__main__":
    main()