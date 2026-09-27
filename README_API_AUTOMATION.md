# Azure API automation patch for Capstone Project B

This patch replaces the manual Azure Document Intelligence Studio step:

1. Open Azure Studio
2. Upload `bird_form_XXX_upper_section.png`
3. Run analysis
4. Download JSON
5. Rename it to `bird_form_XXX_upper_read.json`

with a Python API call.

## Existing project structure used by this patch

```text
C:\Users\lemon\capstone
├─ data
│  ├─ cropped_fields
│  │  └─ bird_form_001_upper_section.png
│  ├─ azure_results
│  │  └─ bird_form_001_upper_read.json
│  ├─ simplified_outputs
│  ├─ evaluation
│  ├─ manual_reference
│  └─ raw_forms
├─ src
│  ├─ ocr
│  │  └─ azure_read.py                  <- new
│  ├─ postprocessing
│  │  └─ anchor_based_simplified_json.py
│  ├─ preprocessing
│  └─ evaluation
│     └─ accuracy_form.py
├─ run_pipeline.py                       <- new
├─ .env                                  <- create locally
└─ requirements-azure.txt                <- new
```

## 1. Install packages

From PowerShell:

```powershell
cd C:\Users\lemon\capstone
python -m pip install -r requirements-azure.txt
```

If `python` is not the interpreter you use, run it with your existing Python 3.11 executable.

## 2. Create `.env`

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Then enter the Azure endpoint and API key.

Example shape:

```text
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://xxxx.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=xxxxxxxx
AZURE_DOCUMENT_INTELLIGENCE_MODEL=prebuilt-read
```

Do not commit `.env`.

Add this to `.gitignore` if it is not already there:

```text
.env
```

## 3. Test only the Azure replacement step

Assuming this exists:

```text
data\cropped_fields\bird_form_002_upper_section.png
```

run:

```powershell
python src\ocr\azure_read.py bird_form_002
```

The script automatically creates:

```text
data\azure_results\bird_form_002_upper_read.json
```

The output is wrapped in the same structure expected by the existing
postprocessor:

```json
{
  "status": "succeeded",
  "analyzeResult": {
    "modelId": "prebuilt-read",
    "content": "...",
    "pages": [
      {
        "words": [
          {
            "content": "...",
            "polygon": [],
            "confidence": 0.99
          }
        ]
      }
    ]
  }
}
```

## 4. Run Azure + existing post-processing

```powershell
python run_pipeline.py bird_form_002
```

Flow:

```text
data/cropped_fields/bird_form_002_upper_section.png
        ↓
Azure Document Intelligence API
        ↓
data/azure_results/bird_form_002_upper_read.json
        ↓
src/postprocessing/anchor_based_simplified_json.py
        ↓
data/simplified_outputs/bird_form_002_simplified.json
```

## 5. Process multiple forms

```powershell
python run_pipeline.py bird_form_001 bird_form_002
```

No source-code edit is required for each new form as long as the input follows:

```text
{FORM_ID}_upper_section.png
```

## 6. Re-run Azure for an existing form

By default, an existing Azure JSON file is skipped to avoid unnecessary API calls/cost.

To force a new request:

```powershell
python run_pipeline.py bird_form_002 --force-azure
```

## 7. Include the existing evaluation step

```powershell
python run_pipeline.py bird_form_002 --evaluate
```

This additionally invokes:

```text
src/evaluation/accuracy_form.py bird_form_002
```

## Important

This patch starts at the cropped image because that is the exact point where
the current manual Azure Studio workflow begins. It does not change the
existing preprocessing/cropping code.

A later refactor can connect:

```text
raw form
→ preprocessing
→ cropping
→ Azure API
→ simplified JSON
→ validation/evaluation
```

into one command.
