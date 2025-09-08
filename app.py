import os
import re
import json
import csv
import io
from datetime import datetime

from flask import Flask, request, render_template, redirect, url_for, Response
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from google.cloud import documentai

load_dotenv()

# --- FLASK & DB CONFIG ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invoices.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- GOOGLE DOCUMENT AI CONFIG ---
PROJECT_ID = "invoice-processor-mvp"
LOCATION = "eu"
PROCESSOR_ID = "76b22179c654ba14"
MIME_TYPE = "application/pdf"

# --- DATABASE MODEL ---
class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(100), nullable=False)
    supplier_name = db.Column(db.String(100))
    invoice_id = db.Column(db.String(50))
    total_amount = db.Column(db.Float)
    invoice_date = db.Column(db.String(50))
    status = db.Column(db.String(20), default='Pending Review')
    # keep the existing column name to avoid migrations
    xero_json = db.Column(db.Text)  # stores normalized parsed payload
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- HELPERS ---
def _parse_line_item(line_item_text: str):
    """
    Parses a line item in the rough format:
    '2 Widget A $10.00 $20.00'
    Returns None if it doesn't match.
    """
    match = re.match(r'(\d+)\s+(.*?)\s+\$?([\d.]+)\s+\$?([\d.]+)', line_item_text)
    if match:
        return {
            "Description": match.group(2).strip(),
            "Quantity": float(match.group(1)),
            "UnitAmount": float(match.group(3)),
            "AccountCode": "400"
        }
    return None

def _safe_float_conversion(text_value: str) -> float:
    if not text_value:
        return 0.0
    cleaned_text = re.sub(r'[^\d.]', '', text_value)
    if not cleaned_text:
        return 0.0
    try:
        return float(cleaned_text)
    except ValueError:
        return 0.0

def process_and_transform(filepath: str) -> dict:
    """
    Uses Google Document AI to parse an invoice PDF and returns a normalized payload.
    """
    try:
        opts = {"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
        client = documentai.DocumentProcessorServiceClient(client_options=opts)
        name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)

        with open(filepath, "rb") as f:
            image_content = f.read()

        raw_document = documentai.RawDocument(content=image_content, mime_type=MIME_TYPE)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        result = client.process_document(request=request)
        document = result.document

        raw_data = {"line_items": []}
        for entity in document.entities:
            field = entity.type_
            value = (entity.mention_text or "").replace("\n", " ").replace("\r", "")
            if field == "line_item":
                raw_data["line_items"].append(value)
            else:
                raw_data[field] = value

    except Exception as e:
        return {"error": str(e)}

    total_amount_float = _safe_float_conversion(raw_data.get("total_amount"))
    structured_line_items = [
        li for li in (_parse_line_item(i) for i in raw_data.get("line_items", [])) if li
    ]

    # Neutral, normalized structure (no Xero posting)
    normalized_invoice = {
        "Type": "PURCHASE",
        "Contact": {"Name": raw_data.get("supplier_name", "Unknown Supplier")},
        "Date": raw_data.get("invoice_date", "YYYY-MM-DD"),
        "DueDate": raw_data.get("due_date", "YYYY-MM-DD"),
        "LineItems": structured_line_items,
        "InvoiceNumber": raw_data.get("invoice_id"),
        "CurrencyCode": raw_data.get("currency", "GBP"),
        "Total": total_amount_float
    }
    return {"Invoices": [normalized_invoice]}

# --- ROUTES ---
@app.route('/')
def dashboard():
    invoices = Invoice.query.order_by(Invoice.uploaded_at.desc()).all()
    return render_template('dashboard.html', invoices=invoices)

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'invoice' not in request.files:
            return redirect(request.url)

        file = request.files['invoice']
        if file.filename == '':
            return redirect(request.url)

        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        data = process_and_transform(filepath)
        if "error" in data:
            print(f"Error processing {filename}: {data['error']}")
            return redirect(url_for('dashboard'))

        inv = data['Invoices'][0]
        new_invoice = Invoice(
            original_filename=filename,
            supplier_name=inv['Contact'].get('Name'),
            invoice_id=inv.get('InvoiceNumber'),
            total_amount=inv.get('Total'),
            invoice_date=inv.get('Date'),
            xero_json=json.dumps(data)  # store normalized payload
        )
        db.session.add(new_invoice)
        db.session.commit()
        return redirect(url_for('dashboard'))

    return render_template('upload.html')

@app.route('/invoice/<int:invoice_id>', methods=['GET', 'POST'])
def invoice_detail(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    if request.method == 'POST':
        invoice.supplier_name = request.form['supplier_name']
        invoice.invoice_id = request.form['invoice_id']
        invoice.invoice_date = request.form['invoice_date']
        invoice.total_amount = float(request.form['total_amount'])
        invoice.status = request.form['status']
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('details.html', invoice=invoice)

# --- SIMPLE EXPORTS ---
@app.route('/invoice/<int:invoice_id>/download/json')
def download_invoice_json(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    return Response(
        invoice.xero_json,
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=invoice_{invoice_id}.json'}
    )

@app.route('/invoice/<int:invoice_id>/download/csv')
def download_invoice_csv(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Supplier", "InvoiceNumber", "InvoiceDate", "Total", "Status", "UploadedAt", "OriginalFile"])
    writer.writerow([
        invoice.supplier_name or "",
        invoice.invoice_id or "",
        invoice.invoice_date or "",
        invoice.total_amount or 0.0,
        invoice.status or "",
        invoice.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if invoice.uploaded_at else "",
        invoice.original_filename or ""
    ])
    output.seek(0)
    return Response(
        output.read(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=invoice_{invoice_id}.csv'}
    )

# --- APP INIT ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
