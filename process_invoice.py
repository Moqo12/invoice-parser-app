# process_invoice.py
import os
from datetime import datetime
import re

PROJECT_ID   = os.environ.get("PROJECT_ID")
LOCATION     = os.environ.get("LOCATION", "eu")
PROCESSOR_ID = os.environ.get("PROCESSOR_ID")
MIME_TYPE    = os.environ.get("MIME_TYPE", "application/pdf")

def _clean_supplier(name: str) -> str:
    return (name or "").strip().rstrip(",;:·")

def _normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    s = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s

def _parse_amount(text: str):
    if not text:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(text))
        return float(cleaned) if cleaned else None
    except ValueError:
        return None

def process_the_invoice(filepath: str) -> dict:
    """
    Takes a path to a PDF file and returns extracted data as a dict.
    Mirrors your original function, with light cleanup.
    """
    try:
        from google.cloud import documentai

        opts = {"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
        client = documentai.DocumentProcessorServiceClient(client_options=opts)
        name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)

        with open(filepath, "rb") as image:
            image_content = image.read()

        raw_document = documentai.RawDocument(content=image_content, mime_type=MIME_TYPE)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        result = client.process_document(request=request)
        document = result.document

        results_dict = {}
        for entity in document.entities:
            etype = entity.type_ or ""
            clean_text = (entity.mention_text or "").replace("\n", " ").replace("\r", "").strip()

            if etype == "total_amount":
                results_dict[etype] = _parse_amount(clean_text)
            elif etype in {"supplier_name", "supplier", "vendor", "seller"}:
                results_dict["supplier_name"] = _clean_supplier(clean_text)
            elif etype in {"invoice_date", "date"}:
                results_dict["invoice_date"] = _normalize_date(clean_text)
            else:
                results_dict[etype] = clean_text

        return results_dict

    except Exception as e:
        print(f"Error during processing: {e}")
        return {"error": str(e)}
