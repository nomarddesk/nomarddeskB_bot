import os
import logging
import json
import re
import base64
import asyncio
from datetime import datetime
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, ConversationHandler

import gspread
from google.oauth2.service_account import Credentials
from PIL import Image
import pytesseract
import io

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
CONFIRM_DETAILS, NAME, AMOUNT, DATE, CATEGORY = range(5)

class ReceiptOCRProcessor:
    """Handles receipt analysis using OCR (Tesseract)"""
    
    def __init__(self):
        try:
            # Configure Tesseract path (if needed)
            # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'  # Windows
            # pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'  # Linux
            logger.info("✅ OCR Processor initialized")
        except Exception as e:
            logger.error(f"OCR initialization failed: {e}")
    
    async def extract_text_from_image(self, image_bytes: bytes) -> Dict[str, any]:
        """Extract text from receipt image using OCR"""
        try:
            # Convert bytes to image
            image = Image.open(io.BytesIO(image_bytes))
            
            # Perform OCR
            text = pytesseract.image_to_string(image)
            
            # Process extracted text
            processed_data = self._process_extracted_text(text)
            
            return processed_data
            
        except Exception as e:
            logger.error(f"OCR error: {e}")
            return {
                "error": f"OCR processing failed: {str(e)}",
                "raw_text": "",
                "store_name": "Unknown Store",
                "total_amount": 0.00,
                "date": datetime.now().strftime('%Y-%m-%d'),
                "summary": "Could not process receipt"
            }
    
    def _process_extracted_text(self, text: str) -> Dict[str, any]:
        """Process OCR text to extract useful information"""
        result = {
            "raw_text": text,
            "store_name": "Unknown Store",
            "total_amount": 0.00,
            "date": datetime.now().strftime('%Y-%m-%d'),
            "currency": "USD",
            "summary": "Receipt processed via OCR",
            "items": []
        }
        
        try:
            lines = text.split('\n')
            
            # Look for store name (usually in first few lines)
            for i in range(min(5, len(lines))):
                line = lines[i].strip()
                if len(line) > 3 and line.upper() == line:
                    result["store_name"] = line
                    break
            
            # Look for total amount (patterns like TOTAL, AMOUNT, BALANCE)
            total_patterns = [r'TOTAL[\s:]*[\$€£]?\s*(\d+\.?\d*)',
                            r'AMOUNT[\s:]*[\$€£]?\s*(\d+\.?\d*)',
                            r'BALANCE[\s:]*[\$€£]?\s*(\d+\.?\d*)',
                            r'\$?\s*(\d+\.\d{2})\s*$']
            
            for line in lines:
                for pattern in total_patterns:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        try:
                            amount = float(match.group(1))
                            if amount > result["total_amount"]:
                                result["total_amount"] = amount
                        except:
                            pass
            
            # Look for date
            date_patterns = [
                r'\d{2}/\d{2}/\d{4}',
                r'\d{4}-\d{2}-\d{2}',
                r'\d{2}-\d{2}-\d{4}',
                r'\w{3}\s+\d{1,2},\s+\d{4}'
            ]
            
            for line in lines:
                for pattern in date_patterns:
                    match = re.search(pattern, line)
                    if match:
                        result["date"] = match.group()
                        break
                if "date" in result:
                    break
            
        except Exception as e:
            logger.error(f"Text processing error: {e}")
        
        return result
    
    def format_receipt_for_display(self, receipt_data: Dict) -> str:
        """Format receipt data for user display"""
        response = "📸 **Receipt Analysis:**\n\n"
        
        if receipt_data.get('store_name'):
            response += f"🏪 **Store:** {receipt_data['store_name']}\n"
        
        if receipt_data.get('total_amount'):
            currency = receipt_data.get('currency', 'USD')
            response += f"💰 **Total:** {currency} {receipt_data['total_amount']:.2f}\n"
        
        if receipt_data.get('date'):
            response += f"📅 **Date:** {receipt_data['date']}\n"
        
        if "raw_text" in receipt_data and receipt_data["raw_text"]:
            # Show first 200 chars of extracted text
            preview = receipt_data["raw_text"][:200] + "..." if len(receipt_data["raw_text"]) > 200 else receipt_data["raw_text"]
            response += f"\n📝 **Extracted Text:**\n{preview}\n"
        
        response += "\nWould you like to save this receipt?"
        return response

class GoogleSheetManager:
    """Manages Google Sheets operations"""
    
    def __init__(self):
        logger.info("Initializing Google Sheets...")
        
        # Get credentials from environment
        creds_json = os.getenv('GOOGLE_CREDS_JSON')
        sheet_url = os.getenv('SHEET_URL')
        
        if not creds_json:
            raise ValueError("GOOGLE_CREDS_JSON environment variable is missing")
        
        if not sheet_url:
            raise ValueError("SHEET_URL environment variable is missing")
        
        try:
            # Parse credentials
            creds_dict = json.loads(creds_json)
            logger.info(f"Service account: {creds_dict.get('client_email')}")
            
            # Set up scopes and credentials
            SCOPES = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            self.client = gspread.authorize(creds)
            logger.info("✅ Google Sheets authorized")
            
            # Open the spreadsheet
            self.spreadsheet = self.client.open_by_url(sheet_url)
            self.sheet = self.spreadsheet.sheet1
            logger.info(f"✅ Sheet opened: {self.sheet.title}")
            
            # Initialize headers if empty
            self._initialize_headers()
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets: {e}")
            raise
    
    def _initialize_headers(self):
        """Initialize sheet headers if needed"""
        try:
            existing_headers = self.sheet.row_values(1)
            
            if not existing_headers:
                headers = [
                    'ID', 'Timestamp', 'User ID', 'User Name', 'Name', 
                    'Amount', 'Date', 'Category', 'Description', 
                    'Store', 'OCR Text', 'Image Available'
                ]
                self.sheet.append_row(headers)
                logger.info("📝 Initialized sheet headers")
            else:
                logger.info(f"Headers already exist: {existing_headers}")
                
        except Exception as e:
            logger.error(f"Failed to init headers: {e}")
    
    def get_next_id(self) -> int:
        """Get next transaction ID"""
        try:
            # Get all IDs from column A (skip header)
            ids = self.sheet.col_values(1)[1:]  # Skip header row
            if not ids:
                return 1
            return max(int(id) for id in ids if id.isdigit()) + 1
        except:
            return 1
    
    def add_transaction(self, data: Dict) -> bool:
        """Add a new transaction to the sheet"""
        try:
            next_id = self.get_next_id()
            
            row = [
                next_id,
                datetime.now().isoformat(),
                data.get('user_id', ''),
                data.get('user_name', ''),
                data.get('name', ''),
                data.get('amount', 0),
                data.get('date', ''),
                data.get('category', ''),
                data.get('description', ''),
                data.get('store', ''),
                data.get('ocr_text', '')[:50000],  # Limit text length
                'Yes' if data.get('has_image') else 'No'
            ]
            
            self.sheet.append_row(row)
            logger.info(f"✅ Added transaction ID {next_id}: {data.get('name')} - ${data.get('amount')}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding transaction: {e}")
            return False
    
    def get_transactions(self, name: str = None) -> List[Dict]:
        """Get transactions, optionally filtered by name"""
        try:
            records = self.sheet.get_all_records()
            
            if name:
                # Filter by name (case-insensitive)
                transactions = [
                    rec for rec in records 
                    if rec.get('Name', '').lower() == name.lower()
                ]
            else:
                transactions = records
            
            # Convert to proper types
            for t in transactions:
                t['Amount'] = float(t.get('Amount', 0))
            
            return transactions
            
        except Exception as e:
            logger.error(f"Error fetching transactions: {e}")
            return []
    
    def get_total_amount(self, name: str = None) -> float:
        """Calculate total amount, optionally for a specific person"""
        transactions = self.get_transactions(name)
        return sum(t.get('Amount', 0) for t in transactions)
    
    def get_all_names(self) -> List[str]:
        """Get list of all unique names"""
        try:
            records = self.sheet.get_all_records()
            names = {rec.get('Name', '').strip() for rec in records if rec.get('Name', '').strip()}
            return list(names)
        except Exception as e:
            logger.error(f"Error getting names: {e}")
            return []

class ReceiptBot:
    """Main bot class"""
    
    def __init__(self):
        try:
            self.sheet_manager = GoogleSheetManager()
            self.ocr_processor = ReceiptOCRProcessor()
            logger.info("✅ Bot initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise
    
    async def start(self, update: Update, context: CallbackContext):
        """Send welcome message"""
        welcome_text = """
🤖 **Receipt Tracker Bot** 📊

I can help you:
1. 📸 Scan receipt images and extract text
2. 💾 Save transaction details to Google Sheets
3. 📊 View your transaction history
4. 💰 Calculate totals

**Commands:**
/add - Add transaction manually
/search [name] - Find transactions
/total [name] - Calculate total amount
/list - List all people
/help - Show help

Just send me a receipt photo to get started!
"""
        await update.message.reply_text(welcome_text)
    
    async def handle_photo(self, update: Update, context: CallbackContext):
        """Handle receipt photo upload"""
        try:
            user = update.effective_user
            logger.info(f"Photo received from {user.first_name}")
            
            # Notify user
            await update.message.reply_text("📸 Processing receipt...")
            
            # Download the photo
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            
            # Store in context
            context.user_data['receipt_photo'] = photo_bytes
            context.user_data['has_image'] = True
            context.user_data['user_id'] = user.id
            context.user_data['user_name'] = user.full_name
            
            # Process with OCR
            receipt_data = await self.ocr_processor.extract_text_from_image(photo_bytes)
            context.user_data['ocr_data'] = receipt_data
            context.user_data['ocr_text'] = receipt_data.get('raw_text', '')
            
            # Show results
            analysis = self.ocr_processor.format_receipt_for_display(receipt_data)
            
            # Add confirmation buttons
            keyboard = [
                [InlineKeyboardButton("✅ Save Transaction", callback_data="save_photo")],
                [InlineKeyboardButton("✏️ Enter Manually", callback_data="manual_entry")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(analysis, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error processing photo: {e}")
            await update.message.reply_text("❌ Error processing image. Please try again or enter details manually.")
    
    async def handle_confirmation(self, update: Update, context: CallbackContext):
        """Handle confirmation callback"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'save_photo':
            # Auto-fill from OCR data
            ocr_data = context.user_data.get('ocr_data', {})
            
            # Prepare transaction
            transaction = {
                'user_id': context.user_data.get('user_id'),
                'user_name': context.user_data.get('user_name'),
                'name': ocr_data.get('store_name', 'Unknown'),
                'amount': ocr_data.get('total_amount', 0),
                'date': ocr_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                'category': 'Shopping',
                'store': ocr_data.get('store_name', 'Unknown'),
                'description': 'Receipt scan',
                'ocr_text': context.user_data.get('ocr_text', ''),
                'has_image': True
            }
            
            # Save to sheet
            if self.sheet_manager.add_transaction(transaction):
                response = f"""
✅ **Saved Successfully!**

🏪 Store: {transaction['store']}
💰 Amount: ${transaction['amount']:.2f}
📅 Date: {transaction['date']}
📊 Category: {transaction['category']}
"""
                await query.edit_message_text(response)
            else:
                await query.edit_message_text("❌ Failed to save to Google Sheets.")
            
            context.user_data.clear()
            
        elif query.data == 'manual_entry':
            await query.edit_message_text("Please enter the person's name for this receipt:")
            return NAME
    
    async def add_manual(self, update: Update, context: CallbackContext):
        """Start manual transaction addition"""
        context.user_data['user_id'] = update.effective_user.id
        context.user_data['user_name'] = update.effective_user.full_name
        
        await update.message.reply_text("📝 Please enter the name for this transaction:")
        return NAME
    
    async def handle_name(self, update: Update, context: CallbackContext):
        """Get transaction name"""
        context.user_data['name'] = update.message.text
        await update.message.reply_text("💰 Enter the amount (e.g., 25.50):")
        return AMOUNT
    
    async def handle_amount(self, update: Update, context: CallbackContext):
        """Get transaction amount"""
        try:
            amount = float(update.message.text.replace('$', '').replace(',', ''))
            context.user_data['amount'] = amount
            await update.message.reply_text("📅 Enter the date (YYYY-MM-DD or 'today'):")
            return DATE
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a number:")
            return AMOUNT
    
    async def handle_date(self, update: Update, context: CallbackContext):
        """Get transaction date"""
        date_text = update.message.text.strip()
        if date_text.lower() == 'today':
            date_text = datetime.now().strftime('%Y-%m-%d')
        context.user_data['date'] = date_text
        
        # Show categories
        keyboard = [
            [InlineKeyboardButton("🍔 Food", callback_data="Food")],
            [InlineKeyboardButton("🛍️ Shopping", callback_data="Shopping")],
            [InlineKeyboardButton("🚗 Transport", callback_data="Transport")],
            [InlineKeyboardButton("🏠 Utilities", callback_data="Utilities")],
            [InlineKeyboardButton("🎬 Entertainment", callback_data="Entertainment")],
            [InlineKeyboardButton("❓ Other", callback_data="Other")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("Select category:", reply_markup=reply_markup)
        return CATEGORY
    
    async def handle_category(self, update: Update, context: CallbackContext):
        """Handle category selection"""
        query = update.callback_query
        await query.answer()
        
        context.user_data['category'] = query.data
        
        # Prepare final transaction
        transaction = {
            'user_id': context.user_data.get('user_id'),
            'user_name': context.user_data.get('user_name'),
            'name': context.user_data.get('name'),
            'amount': context.user_data.get('amount'),
            'date': context.user_data.get('date'),
            'category': context.user_data.get('category'),
            'store': context.user_data.get('name', 'Unknown'),
            'description': 'Manual entry',
            'has_image': context.user_data.get('has_image', False)
        }
        
        # Save to sheet
        if self.sheet_manager.add_transaction(transaction):
            response = f"""
✅ **Transaction Saved!**

📝 Name: {transaction['name']}
💰 Amount: ${transaction['amount']:.2f}
📅 Date: {transaction['date']}
📊 Category: {transaction['category']}
"""
            await query.edit_message_text(response)
        else:
            await query.edit_message_text("❌ Failed to save transaction.")
        
        context.user_data.clear()
        return ConversationHandler.END
    
    async def search_transactions(self, update: Update, context: CallbackContext):
        """Search transactions"""
        name = ' '.join(context.args) if context.args else None
        
        if name:
            transactions = self.sheet_manager.get_transactions(name)
            
            if not transactions:
                await update.message.reply_text(f"No transactions found for '{name}'")
                return
            
            response = f"📊 Transactions for {name}:\n\n"
            total = 0
            
            for i, t in enumerate(transactions[:10], 1):  # Show last 10
                response += f"{i}. {t.get('Date', 'N/A')} - ${t.get('Amount', 0):.2f}\n"
                response += f"   {t.get('Category', 'N/A')} - {t.get('Store', '')}\n"
                if t.get('Description'):
                    response += f"   📝 {t.get('Description')[:50]}\n"
                response += "\n"
                total += t.get('Amount', 0)
            
            if len(transactions) > 10:
                response += f"... and {len(transactions) - 10} more\n"
            
            response += f"\n💰 **Total: ${total:.2f}**"
            
        else:
            transactions = self.sheet_manager.get_transactions()
            total = sum(t.get('Amount', 0) for t in transactions)
            response = f"📊 All Transactions Summary:\n"
            response += f"📈 Total Transactions: {len(transactions)}\n"
            response += f"💰 Total Amount: ${total:.2f}\n\n"
            response += "Use /search [name] to see specific transactions"
        
        await update.message.reply_text(response)
    
    async def total_amount(self, update: Update, context: CallbackContext):
        """Calculate total amount"""
        name = ' '.join(context.args) if context.args else None
        
        if name:
            total = self.sheet_manager.get_total_amount(name)
            await update.message.reply_text(f"💰 **Total for {name}: ${total:.2f}**")
        else:
            total = self.sheet_manager.get_total_amount()
            await update.message.reply_text(f"💰 **Overall Total: ${total:.2f}**")
    
    async def list_names(self, update: Update, context: CallbackContext):
        """List all names in database"""
        names = self.sheet_manager.get_all_names()
        
        if names:
            response = "👥 **People in Database:**\n\n"
            for i, name in enumerate(sorted(names), 1):
                total = self.sheet_manager.get_total_amount(name)
                response += f"{i}. {name} - ${total:.2f}\n"
            
            response += "\nUse /search [name] for details"
        else:
            response = "No transactions recorded yet."
        
        await update.message.reply_text(response)
    
    async def help_command(self, update: Update, context: CallbackContext):
        """Show help"""
        help_text = """
🤖 **Available Commands:**

**Basic Commands:**
/start - Welcome message
/help - Show this message

**Transaction Management:**
/add - Add transaction manually
/search [name] - Search transactions
/total [name] - Calculate total amount
/list - List all people

**Quick Actions:**
📸 Just send a receipt photo to scan it!
💬 Reply to prompts when asked

**Examples:**
/search John
/total Jane
"""
        await update.message.reply_text(help_text)
    
    async def cancel(self, update: Update, context: CallbackContext):
        """Cancel operation"""
        context.user_data.clear()
        await update.message.reply_text("Operation cancelled.")
        return ConversationHandler.END

def main():
    """Start the bot"""
    print("🚀 Starting Receipt Tracker Bot...")
    
    # Get Telegram token
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN environment variable is missing!")
        print("Please set it: export TELEGRAM_TOKEN='your_token_here'")
        return
    
    try:
        # Create bot instance
        bot = ReceiptBot()
        
        # Create application
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", bot.start))
        application.add_handler(CommandHandler("help", bot.help_command))
        application.add_handler(CommandHandler("search", bot.search_transactions))
        application.add_handler(CommandHandler("total", bot.total_amount))
        application.add_handler(CommandHandler("list", bot.list_names))
        
        # Photo handler
        application.add_handler(MessageHandler(filters.PHOTO, bot.handle_photo))
        
        # Callback query handler
        application.add_handler(CallbackQueryHandler(bot.handle_confirmation))
        
        # Manual entry conversation handler
        manual_conversation = ConversationHandler(
            entry_points=[CommandHandler("add", bot.add_manual)],
            states={
                NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_name)],
                AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_amount)],
                DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_date)],
                CATEGORY: [CallbackQueryHandler(bot.handle_category)]
            },
            fallbacks=[CommandHandler("cancel", bot.cancel)],
        )
        application.add_handler(manual_conversation)
        
        # Start the bot
        print("✅ Bot is running...")
        print("📱 Visit Telegram and send /start to your bot")
        print("📸 Try sending a receipt photo!")
        
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")
        print("\n💡 Troubleshooting tips:")
        print("1. Check if TELEGRAM_TOKEN is set correctly")
        print("2. Verify GOOGLE_CREDS_JSON contains valid JSON")
        print("3. Ensure SHEET_URL points to a valid Google Sheet")
        print("4. Make sure the Google service account has edit permissions")

if __name__ == '__main__':
    main()
