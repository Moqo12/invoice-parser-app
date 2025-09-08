# Invoice Parser Application

This is a simple web application built with Python and Flask that uses Google Cloud Document AI to automatically parse and extract key information from PDF invoices.

## Features
-   **File Upload:** Upload PDF invoices through a web interface.
-   **Data Extraction:** Uses Google's pre-trained Invoice Parser model to extract fields like supplier name, invoice ID, date, total amount, tax, and currency.
-   **Dashboard View:** Displays all processed invoices in a clean, simple dashboard.
-   **Data Export:** Export all captured invoice data to a CSV file.

## Tech Stack
-   **Backend:** Python, Flask
-   **Database:** SQLAlchemy
-   **AI / OCR:** Google Cloud Document AI API
-   **Version Control:** Git & GitHub

## Setup and Installation

Follow these steps to get the application running locally.

### 1. Prerequisites
-   Python 3.8+
-   Git
-   A Google Cloud Platform (GCP) project with the Document AI API enabled.
-   A GCP service account key with permissions to access the Document AI API.

### 2. Clone the Repository
```bash
git clone [https://github.com/Moqo12/invoice-parser-app.git](https://github.com/Moqo12/invoice-parser-app.git)
cd invoice-parser-app