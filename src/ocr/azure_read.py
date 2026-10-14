import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from dotenv import load_dotenv


#project root directory
#finds the project root based on this script location
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#input directory
#cropped upper-section images are stored here
#expected input file name format = bird_form_001_upper_section.png
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "cropped_fields"

#output directory
#Azure raw OCR JSON results will be stored here
#expected output file name format = bird_form_001_upper_read.json
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "azure_results"


def _snake_to_camel(name: str) -> str:

    #convert snake_case text to camelCase text
    #Azure SDK returns some keys in snake_case
    #but Azure Studio style JSON usually uses camelCase
    #example: analyze_result -> analyzeResult

    if "_" not in name:
        return name

    first, *rest = name.split("_")

    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def _camelize_keys(value: Any) -> Any:

    #convert dictionary keys to camelCase
    #this means it also changes nested dictionaries and lists
    #needed bcs existing post-processing scripts expect **Azure Studio style JSON**

    if isinstance(value, dict):

        #if value is dictionary, convert all keys
        #also apply this function again to nested values
        return {
            _snake_to_camel(str(key)): _camelize_keys(item)
            for key, item in value.items()
        }

    if isinstance(value, list):

        #if value is list, apply the same conversion to each item
        return [_camelize_keys(item) for item in value]

    #if value is normal, return it
    return value


def _load_config() -> tuple[str, str, str]:

    #load Azure configuration from .env file
    #.env file must be placed in the project root
    #this keeps Azure endpoint and key outside of the Python code

    load_dotenv(PROJECT_ROOT / ".env")

    #endpoint
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").strip()

    #Azure API key
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "").strip()

    #Azure model ID
    #default is prebuilt-read if not specified in .env file
    model_id = os.getenv(
        "AZURE_DOCUMENT_INTELLIGENCE_MODEL",
        "prebuilt-read",
    ).strip()

    #check required values
    missing = []

    if not endpoint:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")

    if not key:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_KEY")

    #endpoint and key are required
    #model_id is optional because prebuilt-read is used as default
    if missing:
        raise RuntimeError(
            "Missing Azure configuration: "
            + ", ".join(missing)
            + "\nCreate a .env file in the project root using .env.example."
        )

    return endpoint, key, model_id


def analyze_form(
    form_id: str,
    *,
    force: bool = False,
    input_path: Path | None = None,
    output_path: Path | None = None,
) -> Path:

    #analyze one cropped form image with Azure Document Intelligence
    #input = cropped upper-section image
    #output = Azure raw OCR JSON
    #default input = png
    #default output = json

    endpoint, key, model_id = _load_config()

    #if custom input path is not provided, use default naming
    input_path = input_path or (
        DEFAULT_INPUT_DIR / f"{form_id}_upper_section.png"
    )

    #if custom output path is not provided, use default naming
    output_path = output_path or (
        DEFAULT_OUTPUT_DIR / f"{form_id}_upper_read.json"
    )

    #check whether input image exists
    #if the file does not exist, stop
    if not input_path.exists():
        raise FileNotFoundError(
            f"Input image not found:\n  {input_path}\n"
            f"Expected naming example: bird_form_001_upper_section.png"
        )

    #create output folder
    output_path.parent.mkdir(parents=True, exist_ok=True)

    #skip Azure request if output JSON already exists
    #this prevents unnecessary Azure usage
    #***use --force if we want to re-run Azure anyway***
    if output_path.exists() and not force:
        print(f"[SKIP] Azure JSON already exists: {output_path}")
        return output_path

    #create Azure Document Intelligence
    #endpoint and key is from .env
    client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )

    print(f"[AZURE] Form:  {form_id}")
    print(f"[AZURE] Input: {input_path}")
    print(f"[AZURE] Model: {model_id}")

    try:

        #open cropped image
        #send it to Azure model
        with input_path.open("rb") as document:
            poller = client.begin_analyze_document(
                model_id=model_id,
                body=document,
            )

            #Azure request is asynchronous
            #wait until Azure processing finished
            result = poller.result()

    except HttpResponseError as exc:

        #handle API error
        #wrong endpoint, wrong key, unsupported file, quota issue, etc.
        detail = exc.message if getattr(exc, "message", None) else str(exc)

        raise RuntimeError(
            f"Azure Document Intelligence request failed for {form_id}:\n{detail}"
        ) from exc

    finally:

        #close Azure client
        client.close()

    #SDK returns an AnalyzeResult model
    #convert it to dictionary first
    #then convert snake_case keys to camelCase keys
    #to keep compatibility with Azure Studio/REST JSON structure
    analyze_result = _camelize_keys(result.as_dict())

    #existing post-processing scripts expect this structure:
    #data["analyzeResult"]["pages"][...]["words"][...]
    #so this wrapper is to match Azure Studio style output
    studio_compatible_json = {
        "status": "succeeded",
        "analyzeResult": analyze_result,
    }

    #save Azure raw OCR result as JSON
    #ensure_ascii=False keeps text readable
    #indent=2 is for manual inspect
    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(
            studio_compatible_json,
            fp,
            ensure_ascii=False,
            indent=2,
        )

    #count number of pages
    page_count = len(analyze_result.get("pages", []))

    #count number of words
    #this is useful to quickly check if OCR actually detected text
    word_count = sum(
        len(page.get("words", []))
        for page in analyze_result.get("pages", [])
        if isinstance(page, dict)
    )

    print(f"[OK] Saved: {output_path}")
    print(f"[OK] Pages: {page_count}, words: {word_count}")

    return output_path


def main() -> None:

    #command line interface
    #when running the script from PowerShell with form IDs
    #example:
    #python azure_read.py bird_form_001 bird_form_002

    parser = argparse.ArgumentParser(
        description=(
            "Send cropped bird survey form images to Azure Document Intelligence "
            "and save Studio-compatible raw JSON."
        )
    )

    #form IDs are required
    #nargs="+" means one or more form IDs should be provided
    parser.add_argument(
        "form_ids",
        nargs="+",
        help="Example: bird_form_001 bird_form_002",
    )

    #force flag
    #if --force is used, existing Azure JSON will be overwritten by new Azure result
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run Azure even if the output JSON already exists.",
    )

    args = parser.parse_args()

    #store failed form IDs here
    #script continues even if a form fails
    failed = []

    for form_id in args.form_ids:

        try:

            #run Azure OCR for each ID
            analyze_form(form_id, force=args.force)

        except Exception as exc:

            #if one form fails, record it and continue
            failed.append(form_id)
            print(f"[ERROR] {form_id}: {exc}")

    #this makes failure visible in terminal
    if failed:
        raise SystemExit(
            "Failed form(s): " + ", ".join(failed)
        )


if __name__ == "__main__":

    #run main function only when this file is executed
    main()