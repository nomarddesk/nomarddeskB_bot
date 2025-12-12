import os
import re
import io
import time
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from google.cloud import vision
import gspread

# --- CONFIGURATION (UPDATE THESE VALUES) ---

# 1. Telegram Bot Token
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# 2. Google Sheet Configuration
SHEET_ID = "YOUR_GOOGLE_SHEET_ID"  # The ID from the URL
CREDS_FILE = "service_account.json"  # Rename your downloaded JSON file to this

# 3. Google Cloud Vision Authentication
# IMPORTANT: This line is crucial for Google Cloud Vision API to find your credentials.
# Replace 'path/to/your/json/keyfile.json' with the actual path if it's not in the same directory.
# os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDS_FILE 
# Note: Since the vision client is initialized inside a function, we will pass the file content if needed, 
# but for a simple local setup, setting the environment variable is standard. 
# We'll stick to passing the image content directly for simplicity.

# ---------------------------------------------


# --- 1. GOOGLE SHEETS & VISION CLIENTS ---

# Initialize gspread client
try:
    gc = gspread.service_account(filename=CREDS_FILE)
    spreadsheet = gc.open_by_key(SHEET_ID)
    worksheet = spreadsheet.sheet1  # Assumes you are using the first sheet (Sheet1)
    print("✅ Google Sheets connection established.")
except Exception as e:
    print(f"❌ Error connecting to Google Sheets: {e}")
    # Exit or handle error gracefully in a production bot

vision_client = vision.ImageAnnotatorClient()


# --- 2. CORE LOGIC FUNCTIONS ---

def perform_ocr(image_bytes: bytes) -> str:
    """Detects text in the image using Google Cloud Vision."""
    image = vision.Image(content=image_bytes)

    # Use DOCUMENT_TEXT_DETECTION for dense text like receipts
    response = vision_client.document_text_detection(image=image)
    
    if response.full_text_annotation:
        return response.full_text_annotation.text
    return ""

def parse_receipt_data(raw_text: str) -> dict:
    """
    Analyzes the raw text to extract structured receipt data.
    
    NOTE: This is the most challenging part. The RegEx patterns below 
    are basic examples and may need to be expanded for different receipt formats.
    """
    
    # Simple RegEx for Total: looks for keywords like TOTAL, SUB, AMOUNT followed by a number
    # (can include $ or € and has two decimal places)
    total_match = re.search(r'(TOTAL|GRAND\s*TOTAL|AMOUNT\s*DUE|SUBTOTAL)\s*[:\$€]?\s*(\d+[\.,]\d{2})', 
                            raw_text, re.IGNORECASE)
    
    # Simple RegEx for Date: looks for various date formats (DD/MM/YY, MM-DD-YYYY, etc.)
    date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', raw_text)

    # Simple Vendor Name: Takes the first non-numeric line (very weak, but a starting point)
    vendor_name = "Unknown Vendor"
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    if lines:
        for line in lines:
            if not re.search(r'\d', line) and len(line) > 3:
                vendor_name = line
                break
    
    # Format the total amount correctly
    total_amount = total_match.group(2).replace(',', '.') if total_match else "N/A"
    
    return {
        "Date": date_match.group(1) if date_match else "N/A",
        "Vendor": vendor_name,
        "Total Amount": total_amount,
        "Raw Text": raw_text.replace('\n', ' | '), # Replace newlines for spreadsheet cell
        "Timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

def save_to_sheet(data: dict) -> None:
    """Appends the parsed data as a new row in the Google Sheet."""
    # The order of this list must match the column order in your Google Sheet!
    row_data = [
        data.get("Timestamp"),
        data.get("Date"), 
        data.get("Vendor"), 
        data.get("Total Amount"), 
        data.get("Raw Text")
    ]
    worksheet.append_row(row_data)


# --- 3. TELEGRAM BOT HANDLER ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with instructions."""
    await update.message.reply_text(
        "👋 Hello! Send me a photo of your receipt, and I will extract the text and save the data to your Google Sheet."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming photos, runs OCR, processes data, and saves to Google Sheets."""
    
    # 1. User Feedback
    await update.message.reply_text("🔎 Receipt received. Please wait while I analyze the image...")
    
    try:
        # 2. Get the highest quality file and download it as bytes
        photo_file = await update.message.photo[-1].get_file()
        
        # Download the file content into an in-memory buffer (BytesIO)
        file_bytes = io.BytesIO()
        await photo_file.download_to_memory(file_bytes)
        image_bytes = file_bytes.getvalue()
        
        # 3. Perform OCR
        raw_text = perform_ocr(image_bytes)
        
        if not raw_text:
            await update.message.reply_text("⚠️ Could not detect any text on the image. Please try a clearer photo.")
            return

        # 4. Parse the data
        parsed_data = parse_receipt_data(raw_text)
        
        # 5. Save to Google Sheets
        save_to_sheet(parsed_data)
        
        # 6. Confirmation Message
        confirmation_message = (
            f"✅ **Receipt Saved Successfully!**\n\n"
            f"**Vendor:** {parsed_data['Vendor']}\n"
            f"**Date:** {parsed_data['Date']}\n"
            f"**Total:** {parsed_data['Total Amount']}\n\n"
            f"View your sheet: [Link to your sheet](https://docs.google.com/spreadsheets/d/{SHEET_ID})"
        )
        await update.message.reply_markdown(confirmation_message)

    except Exception as e:
        print(f"An error occurred: {e}")
        await update.message.reply_text(f"❌ An error occurred during processing. Check the logs for details.")

# --- 4. BOT RUNNER ---

def main():
    """Starts the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    
    # Handler for any photo message
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo))
    
    # Start the Bot
    print("🤖 Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    # Add an instruction to the user's terminal
    print(f"\n* IMPORTANT: Ensure your Google Sheet has the following header row in Sheet1 (A1:E1):")
    print(f"| Timestamp | Date | Vendor | Total Amount | Raw Text |")
    print(f"* Make sure '{CREDS_FILE}' is in the same directory and shared with the Service Account email.")
    print("-" * 50)
    
    # Check for placeholder tokens
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or SHEET_ID == "YOUR_GOOGLE_SHEET_ID":
        print("🚨 Please update BOT_TOKEN and SHEET_ID in the script before running.")
    else:
        main()
