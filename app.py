# app.py
import os
import json
import re
import csv
from datetime import datetime
from io import StringIO

# --- load .env BEFORE importing anything that reads env vars ---
from dotenv import load_dotenv
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from flask import (
    Flask, render_template, request, redirect,
    url_for, send_file, flash
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

# This reads PROJECT_ID / LOCATION / PROCESSOR_ID / MIME_TYPE from env
from process_invoice import process_the_invoice

# ---------------- Flask & DB setup ----------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "invoices.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# ---------------- Model ----------------
class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier_name = db.Column(db.String(255), nullable=True)
    invoice_id = db.Column(db.String(255), nullable=True)
    invoice_date = db.Column(db.String(32), nullable=True)   # store ISO string
    total_amount = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(64), default="Pending Review")
    xero_json = db.Column(db.Text, nullable=True)            # JSON (string) for raw extracted data
    original_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ---------------- Helpers ----------------
def _parse_amount(text: str):
    """Turn '£2,604.00' into 2604.0; return None if not parseable."""
    if not text:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(text))
        return float(cleaned) if cleaned else None
    except ValueError:
        return None

# ---------------- Routes ----------------
@app.route("/")
def dashboard():
    invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
    return render_template("dashboard.html", invoices=invoices)

# POST-only upload — make sure your form uses method="post" + enctype="multipart/form-data"
@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("No file selected.")
        return redirect(url_for("dashboard"))

    filename = secure_filename(file.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    # Extract using Document AI via process_invoice.py (reads env)
    results = process_the_invoice(save_path)
    if isinstance(results, dict) and "error" in results:
        flash(f"Extraction error: {results['error']}")

    # Persist raw payload for the "Raw Extracted Data" section
    xero_payload = results if isinstance(results, dict) else {"raw": str(results)}

    supplier_name = (results.get("supplier_name") or "").strip()
    invoice_id    = (results.get("invoice_id") or "").strip()
    invoice_date  = (results.get("invoice_date") or "").strip()

    total_amount  = results.get("total_amount")
    if total_amount is None:
        total_amount = _parse_amount(results.get("amount") or results.get("total") or "")

    inv = Invoice(
        supplier_name=supplier_name,
        invoice_id=invoice_id,
        invoice_date=invoice_date,
        total_amount=total_amount,
        status="Pending Review",
        xero_json=json.dumps(xero_payload, ensure_ascii=False),
        original_filename=filename,
    )
    db.session.add(inv)
    db.session.commit()

    return redirect(url_for("invoice_detail", invoice_id=inv.id))

@app.route("/invoice/<int:invoice_id>", methods=["GET", "POST"])
def invoice_detail(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)

    if request.method == "POST":
        # Light sanitation for user edits
        invoice.supplier_name = (request.form.get("supplier_name", "") or "").strip().rstrip(",;:·")
        invoice.invoice_id    = (request.form.get("invoice_id", "") or "").strip()
        invoice.invoice_date  = (request.form.get("invoice_date", "") or "").strip()

        parsed = _parse_amount(request.form.get("total_amount", ""))
        if parsed is not None:
            invoice.total_amount = parsed

        status = request.form.get("status")
        if status:
            invoice.status = status

        db.session.commit()
        return redirect(url_for("invoice_detail", invoice_id=invoice.id))

    # Parse JSON string for template usage with |tojson
    try:
        xero_json = json.loads(invoice.xero_json) if invoice.xero_json else {}
    except Exception:
        xero_json = invoice.xero_json or {}

    view_model = {
        "id": invoice.id,
        "supplier_name": invoice.supplier_name or "",
        "invoice_id": invoice.invoice_id or "",
        "invoice_date": invoice.invoice_date or "",
        "total_amount": invoice.total_amount if invoice.total_amount is not None else "",
        "status": invoice.status or "Pending Review",
        "xero_json": xero_json,
    }
    return render_template("details.html", invoice=view_model)

@app.route("/download/json/<int:invoice_id>")
def download_invoice_json(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)
    filename = f"invoice_{invoice.id}.json"
    data = invoice.xero_json or "{}"
    return send_file(
        StringIO(data),
        mimetype="application/json",
        as_attachment=True,
        download_name=filename,
    )

@app.route("/download/csv/<int:invoice_id>")
def download_invoice_csv(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)

    rows = [[
        "id", "supplier_name", "invoice_id", "invoice_date", "total_amount", "status"
    ], [
        invoice.id,
        invoice.supplier_name or "",
        invoice.invoice_id or "",
        invoice.invoice_date or "",
        f"{invoice.total_amount:.2f}" if invoice.total_amount is not None else "",
        invoice.status or "",
    ]]

    sio = StringIO()
    writer = csv.writer(sio)
    for r in rows:
        writer.writerow(r)
    sio.seek(0)

    return send_file(
        sio,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"invoice_{invoice.id}.csv",
    )

@app.route("/delete/<int:invoice_id>", methods=["POST"])
def delete_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    db.session.delete(invoice)
    db.session.commit()
    flash(f"Invoice #{invoice_id} deleted", "success")
    return redirect(url_for("dashboard"))


# (Optional) quick env debug endpoint while developing
@app.route("/_debug/env")
def _debug_env():
    return {
        "GOOGLE_APPLICATION_CREDENTIALS": os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        "PROJECT_ID": os.environ.get("PROJECT_ID"),
        "LOCATION": os.environ.get("LOCATION"),
        "PROCESSOR_ID": os.environ.get("PROCESSOR_ID"),
    }, 200

if __name__ == "__main__":
    app.run(debug=True)

