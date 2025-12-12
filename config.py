import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Google Sheets Configuration
GOOGLE_CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')

# Tesseract OCR path (for Windows, optional)
TESSERACT_PATH = os.getenv('TESSERACT_PATH', None)

# Google Sheets column headers
COLUMN_HEADERS = [
    "Date",
    "Time",
    "User ID",
    "Username",
    "Store Name",
    "Total Amount",
    "Tax Amount",
    "Items",
    "Payment Method",
    "Raw Text",
    "Image File",
    "Timestamp"
]
