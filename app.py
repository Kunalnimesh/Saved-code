import re
from flask import Flask, render_template, request, send_file, jsonify
from io import BytesIO
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTTextLine
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, 
                           NumberObject, TextStringObject)
import requests

app = Flask(__name__)

# Extract document name

def extract_document_name(text):
        """
        Extracts the document name from the text in the format 
        (DOCUMENT_NAME.pdf, Lines X-Y).

        Args:
            text (str): The input text containing the document name.

        Returns:
            str: The extracted document name, or None if not found.
        """
        pattern = r'\(([^,]+\.pdf), Lines \d+-\d+\)'
        match = re.search(pattern, text)

        if match:
            return match.group(1)  # Return the document name part of the match
        return None  # Return None if no match is found

def is_within_bbox(bbox: list[float], constraint_bbox: list[float], margin=10):
    x0, y0, x1, y1 = bbox
    cx0, cy0, cx1, cy1 = constraint_bbox
    return cx0 <= x0 + margin and cy0 <= y0 + margin and cx1 >= x1 - margin and cy1 >= y1 - margin

def extract_line_bboxes(page_layout, target_line_number):
    """Extract bounding boxes of text lines on a page."""
    line_bboxes = []
    current_line_number = 0
    
    for element in page_layout:
        if isinstance(element, LTTextContainer):
            for text_line in element:
                if isinstance(text_line, LTTextLine):
                    current_line_number += 1
                    if current_line_number == target_line_number:
                        line_bbox = list(text_line.bbox)
                        line_bboxes.append(line_bbox)
                        break
    return line_bboxes

def highlight_annotation(bounds: list[list[float]], color=[1, 1, 0]):
    x0, y0, x1, y1 = [list(sub_list) for sub_list in zip(*bounds)]
    rect_bbox = [min(x0), min(y0), max(x1), max(y1)]

    quad_points = []
    for bbox in bounds:
        x1, y1, x2, y2 = bbox
        quad_points.extend([x1, y2, x2, y2, x1, y1, x2, y1])

    return DictionaryObject({
        NameObject("/F"): NumberObject(4),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Highlight"),
        NameObject("/C"): ArrayObject([FloatObject(c) for c in color]),
        NameObject("/Rect"): ArrayObject([FloatObject(c) for c in rect_bbox]),
        NameObject("/QuadPoints"): ArrayObject([FloatObject(c) for c in quad_points]),
    })

import re

def extract_lines(text):
    """
    Extracts line numbers from the chatbot response, always using page 1.
    The format to match is (hrhbtb.pdf, p. X, lines Y-Z), but page number X is ignored.
    
    Args:
        text (str): The text to extract line numbers from
    
    Returns:
        list: A list of tuples in the format (1, line_number)
    
    Example:
        >>> extract_lines("This can be found on p. 3, lines 5-7")
        [(1, 5), (1, 6), (1, 7)]
    """
    pattern = r'Lines (\d+)-(\d+)'
    
    # Find all matches in the text
    matches = re.findall(pattern, text)
    
    # Process and return the results as a list of tuples
    result = []
    for match in matches:
        start_line = int(match[0])
        end_line = int(match[1])
        # Create tuples for each line in the range, always using page 1
        for line in range(start_line, end_line + 1):
            result.append((1, line))
    
    return result

@app.route('/process-response', methods=['POST'])
def process_response():
    data = request.get_json()
    chatbot_response = data.get('response')

    if chatbot_response:
        extracted_lines = extract_lines(chatbot_response)
        return jsonify(extracted_lines)
    else:
        return jsonify([]), 400


@app.route('/view-pdf')
def view_pdf():
    
    # Get the chatbot response passed as a query parameter
    chatbot_response = request.args.get('response', '')
    
    document_name = extract_document_name(chatbot_response)

    PDF_URL = f"https://ioclhrchatgpt.blob.core.windows.net/hrhbtb-pdf-chunks/{document_name}"

    # Fetch PDF file from the external URL
    response = requests.get(PDF_URL)
    if response.status_code != 200:
        return "Unable to fetch the PDF.", 500

    pdf_content = BytesIO(response.content)
    
    # Ensure the chatbot response contains relevant PDF references
    if not chatbot_response:
        return "No chatbot response provided."

    # Extract page and line pairs from the chatbot response
    page_line_pairs = extract_lines(chatbot_response)  # Extract lines dynamically

    if not page_line_pairs:
        return "No lines to highlight.", 400

    try:
        # Open the PDF from fetched content
        reader = PdfReader(pdf_content)
        writer = PdfWriter()

        # Iterate through all the pages in the document
        for page_num, page in enumerate(reader.pages, start=1):
            page_layout = list(extract_pages(pdf_content))[page_num - 1]

            lines_to_highlight = [line_num for (p, line_num) in page_line_pairs if p == page_num]

            if lines_to_highlight:
                for line_number in lines_to_highlight:
                    line_bboxes = extract_line_bboxes(page_layout, line_number)
                    if line_bboxes:
                        highlight = highlight_annotation(line_bboxes)
                        if "/Annots" in page:
                            page["/Annots"].append(highlight)
                        else:
                            page[NameObject("/Annots")] = ArrayObject([highlight])

            writer.add_page(page)

        # Save the highlighted PDF to a BytesIO object
        output_stream = BytesIO()
        writer.write(output_stream)
        output_stream.seek(0)

        return send_file(
            output_stream,
            mimetype='application/pdf',
            as_attachment=False,
            download_name="highlighted_pdf.pdf"
        )

    except Exception as e:
        return f"An error occurred: {str(e)}", 500
@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5001)
