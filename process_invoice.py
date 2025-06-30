from google.cloud import documentai
import re # Import the regular expression library

# --- Your variables ---
PROJECT_ID = "invoice-processor-mvp"
LOCATION = "eu"
PROCESSOR_ID = "76b2217fc65dba14"
MIME_TYPE = "application/pdf"

def process_the_invoice(filepath):
    """Takes a path to a PDF file and returns the extracted data as a dictionary."""
    try:
        opts = {"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
        client = documentai.DocumentProcessorServiceClient(client_options=opts)
        name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)

        with open(filepath, "rb") as image:
            image_content = image.read()
        raw_document = documentai.RawDocument(content=image_content, mime_type=MIME_TYPE)

        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        result = client.process_document(request=request)
        document = result.document

        # --- Create a dictionary to hold the results ---
        results_dict = {}
        for entity in document.entities:
            # Clean up the text
            clean_text = entity.mention_text.replace('\n', ' ').replace('\r', '')
            
            # For 'total_amount', try to convert it to a number
            if entity.type_ == 'total_amount':
                try:
                    # Remove currency symbols and convert to float
                    numeric_value = float(re.sub(r'[^\d.]', '', clean_text))
                    results_dict[entity.type_] = numeric_value
                except ValueError:
                    results_dict[entity.type_] = None # Or some other default
            else:
                results_dict[entity.type_] = clean_text
        
        return results_dict

    except Exception as e:
        print(f"Error during processing: {e}")
        return {"error": str(e)}

# The 'if __name__ == "__main__"' block can stay as it is for testing