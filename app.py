import os
import re
import json
from flask import Flask, request, render_template, redirect, url_for, session
from werkzeug.utils import secure_filename
from google.cloud import documentai
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from requests_oauthlib import OAuth2Session # New import for OAuth

load_dotenv()

# --- CONFIGURATION ---
app = Flask(__name__)
# A secret key is needed for the session to work
app.config['SECRET_KEY'] = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invoices.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- GOOGLE AI DETAILS ---
PROJECT_ID = "invoice-processor-mvp"
LOCATION = "eu"
PROCESSOR_ID = "76b22179c654ba14"
MIME_TYPE = "application/pdf"

# --- XERO CONFIGURATION ---
XERO_CLIENT_ID = os.getenv("XERO_CLIENT_ID")
XERO_CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET")
XERO_REDIRECT_URI = 'http://127.0.0.1:5000/callback'
XERO_AUTH_URL = 'https://login.xero.com/identity/connect/authorize'
XERO_TOKEN_URL = 'https://identity.xero.com/connect/token'
XERO_CONNECTIONS_URL = 'https://api.xero.com/connections'
XERO_INVOICES_URL = 'https://api.xero.com/api.xro/2.0/Invoices'
XERO_SCOPES = ["openid"]

# --- DATABASE MODEL (Updated Status) ---
class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(100), nullable=False)
    supplier_name = db.Column(db.String(100))
    invoice_id = db.Column(db.String(50))
    total_amount = db.Column(db.Float)
    invoice_date = db.Column(db.String(50))
    status = db.Column(db.String(20), default='Pending Review')
    xero_json = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- HELPER & TRANSFORMATION FUNCTIONS (No changes here) ---
def _parse_line_item(line_item_text):
    match = re.match(r'(\d+)\s+(.*?)\s+\$?([\d.]+)\s+\$?([\d.]+)', line_item_text)
    if match: return { "Description": match.group(2).strip(), "Quantity": float(match.group(1)), "UnitAmount": float(match.group(3)), "AccountCode": "400" }
    return None

def _safe_float_conversion(text_value):
    if not text_value: return 0.0
    cleaned_text = re.sub(r'[^\d.]', '', text_value)
    if not cleaned_text: return 0.0
    try: return float(cleaned_text)
    except ValueError: return 0.0

def process_and_transform_for_xero(filepath):
    # This function remains the same
    try:
        opts = {"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
        client = documentai.DocumentProcessorServiceClient(client_options=opts)
        name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)
        with open(filepath, "rb") as image: image_content = image.read()
        raw_document = documentai.RawDocument(content=image_content, mime_type=MIME_TYPE)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        result = client.process_document(request=request)
        document = result.document
        raw_data = {"line_items": []}
        for entity in document.entities:
            field = entity.type_
            value = entity.mention_text.replace('\n', ' ').replace('\r', '')
            if field == 'line_item': raw_data["line_items"].append(value)
            else: raw_data[field] = value
    except Exception as e: return {"error": str(e)}
    total_amount_float = _safe_float_conversion(raw_data.get('total_amount'))
    structured_line_items = [_f for _f in (_parse_line_item(i) for i in raw_data.get("line_items", [])) if _f]
    xero_invoice = {"Type": "ACCPAY","Contact": {"Name": raw_data.get("supplier_name", "Unknown Supplier")},"Date": raw_data.get("invoice_date", "YYYY-MM-DD"),"DueDate": raw_data.get("due_date", "YYYY-MM-DD"),"LineAmountTypes": "Exclusive","LineItems": structured_line_items,"Status": "DRAFT","InvoiceNumber": raw_data.get("invoice_id"),"CurrencyCode": raw_data.get("currency", "GBP"),"Total": total_amount_float}
    return {"Invoices": [xero_invoice]}

# --- WEB ROUTES ---
@app.route('/')
def dashboard():
    return render_template('dashboard.html', invoices=Invoice.query.order_by(Invoice.uploaded_at.desc()).all())

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'invoice' not in request.files: return redirect(request.url)
        file = request.files['invoice']
        if file.filename == '': return redirect(request.url)
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            xero_data = process_and_transform_for_xero(filepath)
            if "error" in xero_data:
                print(f"Error processing {filename}: {xero_data['error']}")
                return redirect(url_for('dashboard'))
            invoice_details = xero_data['Invoices'][0]
            new_invoice = Invoice(original_filename=filename, supplier_name=invoice_details['Contact'].get('Name'),invoice_id=invoice_details.get('InvoiceNumber'), total_amount=invoice_details.get('Total'),invoice_date=invoice_details.get('Date'), xero_json=json.dumps(xero_data))
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

# --- NEW XERO AUTHENTICATION ROUTES ---

@app.route('/xero-auth')
def xero_auth():
    xero = OAuth2Session(XERO_CLIENT_ID, redirect_uri=XERO_REDIRECT_URI, scope=XERO_SCOPES)
    authorization_url, state = xero.authorization_url(XERO_AUTH_URL)
    session['oauth_state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    xero = OAuth2Session(XERO_CLIENT_ID, state=session['oauth_state'], redirect_uri=XERO_REDIRECT_URI)
    token = xero.fetch_token(XERO_TOKEN_URL, client_secret=XERO_CLIENT_SECRET, authorization_response=request.url)
    session['xero_token'] = token

    # Get the Tenant ID (required for all API calls)
    connections = xero.get(XERO_CONNECTIONS_URL)
    connections_list = connections.json()
    for conn in connections_list:
        if conn['tenantType'] == 'ORGANISATION':
            session['xero_tenant_id'] = conn['tenantId']
            break

    return redirect(url_for('dashboard'))

@app.route('/send-to-xero/<int:invoice_id>')
def send_to_xero(invoice_id):
    if 'xero_token' not in session:
        return redirect(url_for('xero_auth'))

    invoice = db.get_or_404(Invoice, invoice_id)
    xero_data = json.loads(invoice.xero_json)

    xero = OAuth2Session(XERO_CLIENT_ID, token=session['xero_token'])
    
    headers = {
        'Authorization': f"Bearer {session['xero_token']['access_token']}",
        'Xero-Tenant-Id': session['xero_tenant_id'],
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    response = xero.post(XERO_INVOICES_URL, json=xero_data, headers=headers)

    if response.status_code == 200:
        invoice.status = 'Exported'
        db.session.commit()
    else:
        print("Error sending to Xero:", response.text)
        # In a real app, you would show an error message to the user
        
    return redirect(url_for('invoice_detail', invoice_id=invoice_id))

# --- INITIALIZATION ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)