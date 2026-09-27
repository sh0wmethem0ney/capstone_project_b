from __future__ import annotations

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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "cropped_fields"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "azure_results"


def _snake_to_camel(name: str) -> str:
    """Convert SDK-style snake_case keys to Azure REST/Studio-style camelCase."""
    if "_" not in name:
        return name
    first, *rest = name.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def _camelize_keys(value: Any) -> Any:
    """
    Recursively convert dictionary keys to camelCase.

    This keeps the saved JSON compatible with the Azure Studio/REST JSON
    shape already consumed by the existing post-processing scripts.
    """
    if isinstance(value, dict):
        return {
            _snake_to_camel(str(key)): _camelize_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_camelize_keys(item) for item in value]
    return value


def _load_config() -> tuple[str, str, str]:
    load_dotenv(PROJECT_ROOT / ".env")

    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").strip()
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "").strip()
    model_id = os.getenv(
        "AZURE_DOCUMENT_INTELLIGENCE_MODEL",
        "prebuilt-read",
    ).strip()

    missing = []
    if not endpoint:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    if not key:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_KEY")

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
    """
    Analyze one cropped form image with Azure Document Intelligence.

    Default input:
        data/cropped_fields/{FORM_ID}_upper_section.png

    Default output:
        data/azure_results/{FORM_ID}_upper_read.json
    """
    endpoint, key, model_id = _load_config()

    input_path = input_path or (
        DEFAULT_INPUT_DIR / f"{form_id}_upper_section.png"
    )
    output_path = output_path or (
        DEFAULT_OUTPUT_DIR / f"{form_id}_upper_read.json"
    )

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input image not found:\n  {input_path}\n"
            f"Expected naming example: bird_form_001_upper_section.png"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not force:
        print(f"[SKIP] Azure JSON already exists: {output_path}")
        return output_path

    client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )

    print(f"[AZURE] Form:  {form_id}")
    print(f"[AZURE] Input: {input_path}")
    print(f"[AZURE] Model: {model_id}")

    try:
        with input_path.open("rb") as document:
            poller = client.begin_analyze_document(
                model_id=model_id,
                body=document,
            )
            result = poller.result()
    except HttpResponseError as exc:
        detail = exc.message if getattr(exc, "message", None) else str(exc)
        raise RuntimeError(
            f"Azure Document Intelligence request failed for {form_id}:\n{detail}"
        ) from exc
    finally:
        client.close()

    # SDK returns an AnalyzeResult model. Convert it to a dictionary and then
    # normalize key names to the Azure Studio/REST JSON style.
    analyze_result = _camelize_keys(result.as_dict())

    # Existing project post-processing expects:
    # data["analyzeResult"]["pages"][...]["words"][...]
    studio_compatible_json = {
        "status": "succeeded",
        "analyzeResult": analyze_result,
    }

    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(
            studio_compatible_json,
            fp,
            ensure_ascii=False,
            indent=2,
        )

    page_count = len(analyze_result.get("pages", []))
    word_count = sum(
        len(page.get("words", []))
        for page in analyze_result.get("pages", [])
        if isinstance(page, dict)
    )

    print(f"[OK] Saved: {output_path}")
    print(f"[OK] Pages: {page_count}, words: {word_count}")

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Send cropped bird survey form images to Azure Document Intelligence "
            "and save Studio-compatible raw JSON."
        )
    )
    parser.add_argument(
        "form_ids",
        nargs="+",
        help="Example: bird_form_001 bird_form_002",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run Azure even if the output JSON already exists.",
    )
    args = parser.parse_args()

    failed = []

    for form_id in args.form_ids:
        try:
            analyze_form(form_id, force=args.force)
        except Exception as exc:
            failed.append(form_id)
            print(f"[ERROR] {form_id}: {exc}")

    if failed:
        raise SystemExit(
            "Failed form(s): " + ", ".join(failed)
        )


if __name__ == "__main__":
    main()
