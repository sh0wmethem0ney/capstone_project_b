from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from src.ocr.azure_read import analyze_form


PROJECT_ROOT = Path(__file__).resolve().parent
POSTPROCESS_SCRIPT = (
    PROJECT_ROOT / "src" / "postprocessing" / "anchor_based_simplified_json.py"
)
EVALUATION_SCRIPT = (
    PROJECT_ROOT / "src" / "evaluation" / "accuracy_form.py"
)


def _run_python(script: Path, *args: str) -> None:
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")

    command = [sys.executable, str(script), *args]
    print("\n[RUN]", " ".join(command))

    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
    )


def run_form(form_id: str, *, force_azure: bool, evaluate: bool) -> None:
    print("\n" + "=" * 70)
    print(f"Processing {form_id}")
    print("=" * 70)

    # 1) Replaces the manual Azure Studio upload/analyze/download step.
    analyze_form(form_id, force=force_azure)

    # 2) Existing anchor-based post-processing.
    _run_python(POSTPROCESS_SCRIPT, form_id)

    # 3) Optional evaluation against manual reference data.
    if evaluate:
        _run_python(EVALUATION_SCRIPT, form_id)

    print(f"\n[DONE] {form_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capstone OCR pipeline: cropped image -> Azure raw JSON "
            "-> existing simplified JSON -> optional evaluation."
        )
    )
    parser.add_argument(
        "form_ids",
        nargs="+",
        help="Example: bird_form_001 bird_form_002",
    )
    parser.add_argument(
        "--force-azure",
        action="store_true",
        help="Re-analyze forms even when Azure JSON already exists.",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Also run src/evaluation/accuracy_form.py.",
    )
    args = parser.parse_args()

    failed = []

    for form_id in args.form_ids:
        try:
            run_form(
                form_id,
                force_azure=args.force_azure,
                evaluate=args.evaluate,
            )
        except Exception as exc:
            failed.append((form_id, str(exc)))
            print(f"\n[FAILED] {form_id}: {exc}")

    if failed:
        print("\nFailed forms:")
        for form_id, message in failed:
            print(f"  - {form_id}: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
