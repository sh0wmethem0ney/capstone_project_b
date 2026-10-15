import argparse
import subprocess
import sys
from pathlib import Path

from src.ocr.azure_read import analyze_form
#reuse analyze_form function from azure_read.py
#avoids copying Azure OCR logic again


#project root directory
#this file is expected to be located in the project root
PROJECT_ROOT = Path(__file__).resolve().parent

#post-processing script path
#this script converts Azure raw JSON to simplified JSON
POSTPROCESS_SCRIPT = (
    PROJECT_ROOT / "src" / "postprocessing" / "anchor_based.py"
)

#evaluation script path
#this script compares simplified JSON with manual reference CSV
EVALUATION_SCRIPT = (
    PROJECT_ROOT / "src" / "evaluation" / "accuracy_form.py"
)


def _run_python(script: Path, *args: str) -> None:

    #run another Python script from this pipeline script

    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")

    #use the same Python interpreter currently running this file
    command = [sys.executable, str(script), *args]

    print("\n[RUN]", " ".join(command))

    #run target script from project root
    #check=True means stop with error if the script fails
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
    )


def run_form(form_id: str, *, force_azure: bool, evaluate: bool) -> None:

    #run the full pipeline for one form
    #input form_id example = bird_form_001
    #pipeline = Azure OCR -> simplified JSON -> optional evaluation

    print("\n" + "=" * 70)
    print(f"Processing {form_id}")
    print("=" * 70)

    #Azure OCR
    #this replaces manual Azure Studio upload/analyze/download process
    analyze_form(form_id, force=force_azure)

    #post-processing
    #this runs existing anchor-based extraction script
    _run_python(POSTPROCESS_SCRIPT, form_id)

    #evaluation
    #this is optional because manual reference may not exist for every form
    if evaluate:
        _run_python(EVALUATION_SCRIPT, form_id)

    print(f"\n[DONE] {form_id}")


def main() -> None:

    #CLI
    #running the full OCR pipeline from terminal
    #example:
    #python run_pipeline.py bird_form_001 bird_form_002 --evaluate

    parser = argparse.ArgumentParser(
        description=(
            "Capstone OCR pipeline: cropped image -> Azure raw JSON "
            "-> existing simplified JSON -> optional evaluation."
        )
    )

    #form IDs are required
    #nargs="+" means one or more form IDs should be provided
    parser.add_argument(
        "form_ids",
        nargs="+",
        help="Example: bird_form_001 bird_form_002",
    )

    #optional Azure force flag
    #if this is used, Azure OCR runs again even if JSON already exists
    parser.add_argument(
        "--force-azure",
        action="store_true",
        help="Re-analyze forms even when Azure JSON already exists.",
    )

    #optional evaluation flag
    #if this is used, evaluation script runs after simplified JSON is created
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Also run src/evaluation/accuracy_form.py.",
    )

    args = parser.parse_args()

    #store failed forms here
    failed = []

    for form_id in args.form_ids:

        try:

            #run full pipeline for each form ID
            run_form(
                form_id,
                force_azure=args.force_azure,
                evaluate=args.evaluate,
            )

        except Exception as exc:

            #record failed form and error message
            failed.append((form_id, str(exc)))
            print(f"\n[FAILED] {form_id}: {exc}")

    #print failed forms if any
    #exit with error code 1
    if failed:
        print("\nFailed forms:")

        for form_id, message in failed:
            print(f"  - {form_id}: {message}")

        raise SystemExit(1)


if __name__ == "__main__":

    #run main
    main()