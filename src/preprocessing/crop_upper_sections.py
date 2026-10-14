from pathlib import Path
import csv
import fitz
from PIL import Image


#directories
RAW_DIR = Path("data/raw_forms")
OUTPUT_DIR = Path("data/cropped_fields")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True) #create if there is nothing
#must run from the project directory -> this could be improved for the future projects

#crop the upper field section - front page
#******************adjust this value if the crop goes wrong*******************
UPPER_SECTION_BOTTOM_RATIO = 0.28

#output image resolution don't go too high. Size will also go up. 300 provides good balances between clarity and size so far
DPI = 300


def render_pdf_page_to_image(pdf_path: Path, page_index: int = 0, dpi: int = 300) -> Image.Image:

    #render PDF with higher resolution image
 
    doc = fitz.open(pdf_path) #open up the file
    page = doc[page_index] #page index 0 = use the front page -> lower & back page fields are out of scope

    zoom = dpi / 72 #base PDF resolution = 72 DPI
    matrix = fitz.Matrix(zoom, zoom) #calculate ratio to convert 72DPI to 300DPI output
    pix = page.get_pixmap(matrix=matrix, alpha=False)#pixel level image rendering with no transparent backgroud

    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    #convert pymuPDF pixmap data to PIL image
    #pymuPDF allows direct rendering of PDF pages -> high resolution images
    
    doc.close()
    return image
    #this return is not stored yet. Still an object level in memory


def crop_upper_section(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:

    #crop  the upper section of the form
    #Target fields= member number, name, location, coordinates, date, and species count.
    #To exclude species list -> this creates very large OCR JSON output from Azure OCR.

    width, height = image.size

    left = 0
    top = 0
    right = width
    bottom = int(height * UPPER_SECTION_BOTTOM_RATIO)

    cropped_image = image.crop((left, top, right, bottom))
    crop_box = (left, top, right, bottom)

    return cropped_image, crop_box
    #it returns two ouputs. cropped image and crop box coordinates


def main():
    pdf_files = sorted(RAW_DIR.glob("*.pdf")) #search PDF from RAW_DIR. sort by name

    if not pdf_files: #no PDF then print a mesg and exit
        print(f"No PDF files found in {RAW_DIR}")
        return

    manifest_rows = [] #this is for PDF output results. stored as crop.csv

    #loop to start from  # 001 and store as 001
    for pdf_path in pdf_files:
        form_id = pdf_path.stem

        print(f"Processing {pdf_path.name}...")

        #convert the front page of the PDF -> only the first page of PDF by default. Change page_index in case needed
        page_image = render_pdf_page_to_image(pdf_path, page_index=0, dpi=DPI)

        #crop upper fields
        cropped_image, crop_box = crop_upper_section(page_image)

        #save as PNG -> This is input for Azure AI OCR model
        output_path = OUTPUT_DIR / f"{form_id}_upper_section.png"
        cropped_image.save(output_path)

        #crop info for documentation -> this is to record the crop information for each input. Redunduncy purpose
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

    #create manifest CSV
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