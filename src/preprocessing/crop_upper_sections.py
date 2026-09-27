from pathlib import Path
import csv
import fitz
from PIL import Image


#directories
RAW_DIR = Path("data/raw_forms")
OUTPUT_DIR = Path("data/cropped_fields")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

#crop the upper field section - front page
# Adjust this value if the crop is wrong
UPPER_SECTION_BOTTOM_RATIO = 0.28

#higher DPI = better image quality
DPI = 300


def render_pdf_page_to_image(pdf_path: Path, page_index: int = 0, dpi: int = 300) -> Image.Image:
    """
    Convert a PDF file into a high-resolution image.
    page_index=0 means the first/front page.
    """
    doc = fitz.open(pdf_path)
    page = doc[page_index]

    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)

    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()

    return image


def crop_upper_section(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """
    Crop only the upper section of the form.
    This area contains member number, name, location, coordinates, date, and species count.
    The species list is excluded because it creates very large OCR JSON output.
    """
    width, height = image.size

    left = 0
    top = 0
    right = width
    bottom = int(height * UPPER_SECTION_BOTTOM_RATIO)

    cropped_image = image.crop((left, top, right, bottom))
    crop_box = (left, top, right, bottom)

    return cropped_image, crop_box


def main():
    pdf_files = sorted(RAW_DIR.glob("*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in {RAW_DIR}")
        return

    manifest_rows = []

    for pdf_path in pdf_files:
        form_id = pdf_path.stem

        print(f"Processing {pdf_path.name}...")

        #convert the front page of the PDF
        page_image = render_pdf_page_to_image(pdf_path, page_index=0, dpi=DPI)

        #crop upper fields
        cropped_image, crop_box = crop_upper_section(page_image)

        #save
        output_path = OUTPUT_DIR / f"{form_id}_upper_section.png"
        cropped_image.save(output_path)

        #crop info for documentation
        manifest_rows.append({
            "form_id": form_id,
            "source_file": pdf_path.name,
            "page": "front",
            "crop_region": "upper_section",
            "output_file": str(output_path),
            "dpi": DPI,
            "crop_left": crop_box[0],
            "crop_top": crop_box[1],
            "crop_right": crop_box[2],
            "crop_bottom": crop_box[3],
            "bottom_ratio": UPPER_SECTION_BOTTOM_RATIO
        })

    #create CSV
    manifest_path = OUTPUT_DIR / "crop.csv"

    with open(manifest_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=manifest_rows[0].keys())
        writer.writeheader()
        writer.writerows(manifest_rows)

    print("Cropping completed.")
    print(f"Cropped images saved to: {OUTPUT_DIR}")
    print(f"Manifest saved to: {manifest_path}")


if __name__ == "__main__":
    main()