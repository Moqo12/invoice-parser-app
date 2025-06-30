import os
import re
import json
from flask import Flask, request, render_template, redirect, url_for
from werkzeug.utils import secure_filename
from google.cloud import documentai
from dotenv import load_dotenv # <-- Make sure this line is here

load_dotenv() # <-- This line loads your .env file automatically

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

# --- GOOGLE AI DETAILS ---
PROJECT_ID = "invoice-processor-mvp"
LOCATION = "eu"
PROCESSOR_ID = "76b22179c654ba14"
MIME_TYPE = "application/pdf"

# --- HELPER FUNCTION TO PARSE A SINGLE LINE ITEM ---
def _parse_line_item(line_item_text):
    match = re.match(r'(\d+)\s+(.*?)\s+\$?([\d.]+)\s+\$?([\d.]+)', line_item_text)
    if match:
        return {
            "Description": match.group(2).strip(),
            "Quantity": float(match.group(1)),
            "UnitAmount": float(match.group(3)),
            "AccountCode": "400" 
        }
    return None

# --- THE MAIN PROCESSING AND TRANSFORMATION FUNCTION ---
def process_and_transform_for_xero(filepath):
    try:
        # The Google client will automatically find the credentials from the loaded .env file
        opts = {"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
        client = documentai.DocumentProcessorServiceClient(client_options=opts)
        
        name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)
        with open(filepath, "rb") as image:
            image_content = image.read()
        raw_document = documentai.RawDocument(content=image_content, mime_type=MIME_TYPE)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        result = client.process_document(request=request)
        document = result.document

        raw_data = {"line_items": []}
        for entity in document.entities:
            field = entity.type_
            value = entity.mention_text.replace('\n', ' ').replace('\r', '')
            if field == 'line_item':
                raw_data["line_items"].append(value)
            else:
                raw_data[field] = value
    except Exception as e:
        return {"error": str(e)}

    structured_line_items = []
    for item_text in raw_data.get("line_items", []):
        parsed_item = _parse_line_item(item_text)
        if parsed_item:
            structured_line_items.append(parsed_item)

    xero_invoice = {
        "Type": "ACCPAY",
        "Contact": {"Name": raw_data.get("supplier_name", "Unknown Supplier")},
        "Date": "2025-06-16",
        "DueDate": "2025-07-16",
        "LineAmountTypes": "Exclusive",
        "LineItems": structured_line_items,
        "Status": "DRAFT",
        "InvoiceNumber": raw_data.get("invoice_id"),
        "CurrencyCode": raw_data.get("currency", "GBP")
    }
    
    return {"Invoices": [xero_invoice]}

# --- WEB ROUTES ---
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'invoice' not in request.files: return redirect(request.url)
        file = request.files['invoice']
        if file.filename == '': return redirect(request.url)
        
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            xero_ready_data = process_and_transform_for_xero(filepath)
            
            return f"<pre>{json.dumps(xero_ready_data, indent=2)}</pre>"

    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)