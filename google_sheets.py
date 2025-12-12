import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_ID, COLUMN_HEADERS
from datetime import datetime

class GoogleSheetsHandler:
    def __init__(self):
        self.scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        self.credentials = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE, 
            scopes=self.scope
        )
        self.client = gspread.authorize(self.credentials)
        self.sheet = None
        
    def get_or_create_sheet(self, sheet_name="Receipts"):
        """Get or create the spreadsheet sheet"""
        try:
            spreadsheet = self.client.open_by_key(SPREADSHEET_ID)
            try:
                self.sheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                self.sheet = spreadsheet.add_worksheet(
                    title=sheet_name, 
                    rows=1000, 
                    cols=len(COLUMN_HEADERS)
                )
                self.sheet.append_row(COLUMN_HEADERS)
        except Exception as e:
            print(f"Error accessing Google Sheets: {e}")
            return None
        return self.sheet
    
    def append_receipt_data(self, receipt_data):
        """Append receipt data to Google Sheets"""
        try:
            if not self.sheet:
                self.get_or_create_sheet()
            
            # Prepare data row
            row_data = [
                receipt_data.get("date", ""),
                receipt_data.get("time", ""),
                receipt_data.get("user_id", ""),
                receipt_data.get("username", ""),
                receipt_data.get("store_name", ""),
                receipt_data.get("total_amount", ""),
                receipt_data.get("tax_amount", ""),
                receipt_data.get("items", ""),
                receipt_data.get("payment_method", ""),
                receipt_data.get("raw_text", ""),
                receipt_data.get("image_file", ""),
                datetime.now().isoformat()
            ]
            
            self.sheet.append_row(row_data)
            return True
        except Exception as e:
            print(f"Error appending data to Google Sheets: {e}")
            return False
