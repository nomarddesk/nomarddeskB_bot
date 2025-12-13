import os
import json
import base64
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    CallbackContext, 
    CallbackQueryHandler, 
    ConversationHandler
)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Conversation states
NAME, AMOUNT, DATE, CATEGORY = range(4)

@dataclass
class ReceiptData:
    """Store receipt data from AI analysis"""
    store_name: str = ""
    total_amount: float = 0.0
    date: str = ""
    currency: str = "NGN"
    items: List[Dict] = None
    summary: str = ""
    confidence: float = 0.0
    recipient: str = ""
    transaction_id: str = ""
    
    def __post_init__(self):
        if self.items is None:
            self.items = []

class OpenAIAnalyzer:
    """Analyze receipts using OpenAI GPT-4 Vision"""
    
    def __init__(self, api_key: str = None):
        self.client = None
        self.available = False
        
        if api_key:
            try:
                # Try to import OpenAI
                from openai import OpenAI
                self.client = OpenAI(api_key=api_key)
                self.available = True
                logger.info("✅ OpenAI initialized")
            except ImportError:
                logger.warning("OpenAI library not available")
            except Exception as e:
                logger.error(f"OpenAI init failed: {e}")
    
    async def analyze_receipt(self, image_bytes: bytes) -> ReceiptData:
        """Analyze receipt image using GPT-4 Vision"""
        if not self.available or not self.client:
            logger.warning("OpenAI not available")
            return ReceiptData()
        
        try:
            # Encode image to base64
            image_b64 = base64.b64encode(image_bytes).decode('utf-8')
            
            # Prepare prompt specifically for payment receipts
            prompt = """You are a financial receipt analysis expert. Analyze this payment receipt image and extract the following information in JSON format:

{
    "store_name": "Name of the store/business or recipient",
    "total_amount": 0.00,
    "date": "YYYY-MM-DD",
    "currency": "NGN or USD or other",
    "recipient": "Name of recipient",
    "transaction_id": "Transaction number if available",
    "items": [
        {"name": "item name or description", "price": 0.00}
    ],
    "summary": "Brief description of payment",
    "confidence": 0.95
}

IMPORTANT: This is a payment receipt, likely from a banking app or payment system. Look for:
1. "PAY" or "Payment" text
2. Amount in Naira (₦) or other currency
3. Recipient name
4. Transaction date
5. Transaction ID/Number
6. Status (Successful, Completed, etc.)

Rules:
1. Return ONLY valid JSON, no other text
2. If date is not available, use today's date
3. Convert amounts to numbers (remove commas, currency symbols)
4. Look carefully for Naira amounts (₦ or NGN)
5. Confidence should be 0.0 to 1.0
6. Date must be in YYYY-MM-DD format
7. Extract transaction ID if available"""
            
            # Call OpenAI API synchronously (OpenAI SDK doesn't have async for vision yet)
            import asyncio
            from functools import partial
            
            # Create a partial function for the sync call
            def make_openai_call():
                return self.client.chat.completions.create(
                    model="gpt-4-vision-preview",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=1500,
                    temperature=0.1
                )
            
            # Run in thread pool to avoid blocking
            response = await asyncio.get_event_loop().run_in_executor(None, make_openai_call)
            
            # Parse response
            content = response.choices[0].message.content
            
            # Clean and parse JSON
            try:
                # Clean the response
                content = content.strip()
                
                # Remove markdown code blocks
                if content.startswith("```json"):
                    content = content[7:]
                elif content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                
                # Parse JSON
                data = json.loads(content)
                
                # Convert to ReceiptData
                receipt = ReceiptData(
                    store_name=data.get("store_name", ""),
                    total_amount=float(data.get("total_amount", 0)),
                    date=data.get("date", datetime.now().strftime('%Y-%m-%d')),
                    currency=data.get("currency", "NGN"),
                    recipient=data.get("recipient", ""),
                    transaction_id=data.get("transaction_id", ""),
                    items=data.get("items", []),
                    summary=data.get("summary", ""),
                    confidence=float(data.get("confidence", 0))
                )
                
                logger.info(f"✅ AI analysis complete: {receipt.store_name} - {receipt.currency} {receipt.total_amount}")
                return receipt
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AI response: {e}")
                logger.error(f"Response content: {content[:500]}")
                return ReceiptData()
                
        except Exception as e:
            logger.error(f"OpenAI analysis error: {str(e)}")
            return ReceiptData()
    
    def format_receipt_for_display(self, receipt: ReceiptData) -> str:
        """Format receipt data for display"""
        lines = []
        lines.append("🤖 **AI Receipt Analysis**")
        lines.append("─" * 40)
        
        if receipt.store_name and receipt.store_name != "Unknown Store":
            lines.append(f"🏪 **Store/Recipient:** {receipt.store_name}")
        elif receipt.recipient:
            lines.append(f"👤 **Recipient:** {receipt.recipient}")
        
        if receipt.total_amount > 0:
            lines.append(f"💰 **Amount:** {receipt.currency} {receipt.total_amount:,.2f}")
        
        if receipt.date:
            lines.append(f"📅 **Date:** {receipt.date}")
        
        if receipt.transaction_id:
            lines.append(f"🔢 **Transaction ID:** {receipt.transaction_id[:20]}...")
        
        if receipt.items:
            lines.append("\n📋 **Items:**")
            for i, item in enumerate(receipt.items[:3], 1):
                name = item.get('name', 'Item')
                price = item.get('price', 0)
                lines.append(f"  {i}. {name} - {receipt.currency} {price:,.2f}")
            
            if len(receipt.items) > 3:
                lines.append(f"  ... and {len(receipt.items) - 3} more items")
        
        if receipt.summary:
            lines.append(f"\n📝 **Summary:** {receipt.summary}")
        
        if receipt.confidence > 0:
            confidence_percent = receipt.confidence * 100
            lines.append(f"\n🎯 **Confidence:** {confidence_percent:.1f}%")
        
        return "\n".join(lines)

class GoogleSheetManager:
    """Manages Google Sheets operations"""
    
    def __init__(self):
        self.sheet = None
        self._initialize()
    
    def _initialize(self):
        """Initialize Google Sheets connection"""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            
            creds_json = os.getenv('GOOGLE_CREDS_JSON')
            sheet_url = os.getenv('SHEET_URL')
            
            if not creds_json:
                logger.error("GOOGLE_CREDS_JSON not found")
                return
            
            if not sheet_url:
                logger.error("SHEET_URL not found")
                return
            
            creds_dict = json.loads(creds_json)
            SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
            
            credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            client = gspread.authorize(credentials)
            
            self.sheet = client.open_by_url(sheet_url).sheet1
            
            # Setup headers
            self._setup_headers()
            
            logger.info("✅ Google Sheets initialized")
            
        except ImportError:
            logger.error("Google Sheets libraries not installed")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets: {e}")
            self.sheet = None
    
    def _setup_headers(self):
        """Setup headers in sheet"""
        try:
            existing = self.sheet.row_values(1)
            
            if not existing or len(existing) < 12:
                headers = [
                    'ID', 'Timestamp', 'User ID', 'Username', 'Name',
                    'Amount', 'Currency', 'Date', 'Category', 'Description',
                    'Store', 'Recipient', 'Transaction ID', 'AI Confidence', 
                    'Items Count', 'AI Summary', 'Has Image'
                ]
                self.sheet.insert_row(headers, 1)
                logger.info("📝 Added headers to sheet")
        except Exception as e:
            logger.error(f"Failed to setup headers: {e}")
    
    def get_next_id(self) -> int:
        """Get next transaction ID"""
        try:
            if not self.sheet:
                return 1
            
            ids = self.sheet.col_values(1)[1:]  # Skip header
            if not ids:
                return 1
            
            numeric_ids = []
            for id_str in ids:
                try:
                    numeric_ids.append(int(id_str))
                except:
                    continue
            
            return max(numeric_ids, default=0) + 1
        except:
            return 1
    
    def add_transaction(self, data: Dict) -> bool:
        """Add transaction to sheet"""
        try:
            if not self.sheet:
                return False
            
            next_id = self.get_next_id()
            
            # Format items for sheet
            items = data.get('items', [])
            items_summary = ""
            if items:
                item_names = [item.get('name', '')[:20] for item in items[:3]]
                items_summary = ", ".join(item_names)
                if len(items) > 3:
                    items_summary += f" (+{len(items)-3} more)"
            
            row = [
                next_id,
                datetime.now().isoformat(),
                data.get('user_id', ''),
                data.get('user_name', ''),
                data.get('name', ''),
                data.get('amount', 0),
                data.get('currency', 'NGN'),
                data.get('date', ''),
                data.get('category', ''),
                data.get('description', ''),
                data.get('store', ''),
                data.get('recipient', ''),
                data.get('transaction_id', ''),
                data.get('confidence', 0),
                len(items),
                data.get('summary', ''),
                '✅' if data.get('has_image') else '❌'
            ]
            
            self.sheet.append_row(row)
            logger.info(f"✅ Added transaction ID {next_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding transaction: {e}")
            return False
    
    def get_transactions(self, name: str = None) -> List[Dict]:
        """Get transactions"""
        try:
            if not self.sheet:
                return []
            
            records = self.sheet.get_all_records()
            
            if name:
                name_lower = name.lower()
                records = [r for r in records if str(r.get('Name', '')).lower() == name_lower]
            
            return records
            
        except Exception as e:
            logger.error(f"Error getting transactions: {e}")
            return []
    
    def get_total(self, name: str = None) -> float:
        """Calculate total amount"""
        transactions = self.get_transactions(name)
        total = 0.0
        
        for t in transactions:
            try:
                total += float(t.get('Amount', 0))
            except:
                continue
        
        return total
    
    def get_names(self) -> List[str]:
        """Get unique names"""
        try:
            if not self.sheet:
                return []
            
            records = self.sheet.get_all_records()
            names = {str(r.get('Name', '')).strip() for r in records if str(r.get('Name', '')).strip()}
            return sorted(list(names))
            
        except Exception as e:
            logger.error(f"Error getting names: {e}")
            return []

class AIReceiptBot:
    """AI-powered receipt tracking bot"""
    
    def __init__(self):
        # Initialize OpenAI
        openai_key = os.getenv('OPENAI_API_KEY')
        self.ai_analyzer = OpenAIAnalyzer(openai_key)
        
        # Initialize Google Sheets
        self.sheets = GoogleSheetManager()
        
        logger.info("🤖 AI Receipt Bot initialized")
    
    async def start(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        welcome = """
🤖 **AI Payment Receipt Tracker**

I can automatically scan and analyze your payment receipts!

**Send me a screenshot of:**
• Bank transfer receipts
• Payment confirmations  
• Mobile money transactions
• POS receipts
• Any payment confirmation

**I'll extract:**
💰 Amount • 📅 Date • 👤 Recipient • 🏪 Store • 🔢 Transaction ID

**Commands:**
/add - Add manually
/search [name] - Search transactions
/total [name] - Calculate total
/list - List all names
/help - Help guide

**Just send me a receipt screenshot to get started!** 📸
"""
        await update.message.reply_text(welcome)
    
    async def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        help_text = """
📚 **How to use this bot:**

1. **Take a screenshot** of any payment receipt:
   - Bank app transfers
   - Mobile money (OPay, Palmpay, etc.)
   - POS receipts
   - Online payment confirmations

2. **Send the screenshot** to me

3. **AI will analyze** and extract:
   • Amount (NGN, USD, etc.)
   • Date
   • Recipient name
   • Transaction ID
   • Payment description

4. **Confirm or edit** the details

5. **✅ Saved to Google Sheets**

**Commands:**
• /start - Welcome message
• /add - Manual entry
• /search [name] - Search transactions
• /total [name] - Calculate total
• /list - List all names
• /stats - Get statistics

**Example:**
/search John
/total November
/list
"""
        await update.message.reply_text(help_text)
    
    async def handle_photo(self, update: Update, context: CallbackContext):
        """Handle receipt photo with AI analysis"""
        try:
            user = update.effective_user
            logger.info(f"📸 Photo received from {user.first_name}")
            
            # Download photo
            photo = update.message.photo[-1]
            file = await photo.get_file()
            
            # Download as bytes
            image_bytes = await file.download_as_bytearray()
            
            # Store in context
            context.user_data['image_bytes'] = image_bytes
            context.user_data['user_id'] = user.id
            context.user_data['user_name'] = user.full_name
            
            # Start AI analysis
            await update.message.reply_text("🤖 AI is analyzing your payment receipt...")
            
            # Analyze with OpenAI
            receipt_data = await self.ai_analyzer.analyze_receipt(image_bytes)
            context.user_data['receipt_data'] = receipt_data
            
            # Show analysis results
            display_text = self.ai_analyzer.format_receipt_for_display(receipt_data)
            
            # Check if AI found anything useful
            if receipt_data.total_amount > 0 or receipt_data.store_name or receipt_data.recipient:
                # Add action buttons
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Save with AI Data", callback_data="save_ai"),
                        InlineKeyboardButton("✏️ Edit Details", callback_data="edit_manual")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    display_text + "\n\n**What would you like to do?**",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                # AI analysis failed or found nothing
                await update.message.reply_text(
                    "❌ AI couldn't extract clear information from this image.\n\n"
                    "**Try this:**\n"
                    "1. Make sure the receipt is clearly visible\n"
                    "2. Try a different screenshot\n"
                    "3. Or enter details manually\n\n"
                    "**Common issues:**\n"
                    "• Blurry image\n"
                    "• Text too small\n"
                    "• Complex background"
                )
                
                # Start manual entry
                keyboard = [
                    [InlineKeyboardButton("✏️ Enter Manually", callback_data="edit_manual")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "Choose an option:",
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            logger.error(f"Error handling photo: {str(e)}")
            await update.message.reply_text(
                f"❌ Error processing image: {str(e)}\n\n"
                "Please try again or enter details manually with /add"
            )
    
    async def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "save_ai":
            # Save with AI data
            receipt_data = context.user_data.get('receipt_data', ReceiptData())
            
            # Determine name for transaction
            if receipt_data.recipient:
                transaction_name = receipt_data.recipient
            elif receipt_data.store_name and receipt_data.store_name != "Unknown Store":
                transaction_name = receipt_data.store_name
            else:
                transaction_name = "Payment"
            
            # Save to Google Sheets
            transaction_data = {
                'user_id': context.user_data.get('user_id'),
                'user_name': context.user_data.get('user_name'),
                'name': transaction_name,
                'amount': receipt_data.total_amount,
                'currency': receipt_data.currency,
                'date': receipt_data.date,
                'category': 'Payment',
                'store': receipt_data.store_name,
                'recipient': receipt_data.recipient,
                'transaction_id': receipt_data.transaction_id,
                'description': receipt_data.summary,
                'items': receipt_data.items,
                'confidence': receipt_data.confidence,
                'summary': receipt_data.summary,
                'has_image': True
            }
            
            if self.sheets.add_transaction(transaction_data):
                response = f"""
✅ **Payment Saved Successfully!**

🤖 **AI Analysis:**
👤 **Recipient:** {transaction_name}
💰 **Amount:** {receipt_data.currency} {receipt_data.total_amount:,.2f}
📅 **Date:** {receipt_data.date}
"""
                
                if receipt_data.transaction_id:
                    response += f"🔢 **Transaction ID:** {receipt_data.transaction_id[:15]}...\n"
                
                response += f"🎯 **Confidence:** {receipt_data.confidence * 100:.1f}%\n\n"
                response += "💾 **Saved to Google Sheets**"
                
                await query.edit_message_text(response)
            else:
                await query.edit_message_text("❌ Failed to save to Google Sheets. Please check setup.")
            
            context.user_data.clear()
            
        elif query.data == "edit_manual":
            # Start manual editing
            receipt_data = context.user_data.get('receipt_data', ReceiptData())
            
            # Pre-fill with AI data if available
            default_name = receipt_data.recipient or receipt_data.store_name or "Payment"
            default_amount = receipt_data.total_amount or 0
            default_date = receipt_data.date or datetime.now().strftime('%Y-%m-%d')
            
            context.user_data['default_name'] = default_name
            context.user_data['default_amount'] = default_amount
            context.user_data['default_date'] = default_date
            
            await query.edit_message_text(
                f"✏️ **Manual Entry**\n\n"
                f"Recipient/Store name (press Enter for '{default_name}'):"
            )
            return NAME
            
        elif query.data == "cancel":
            await query.edit_message_text("❌ Operation cancelled.")
            context.user_data.clear()
    
    async def add_manual(self, update: Update, context: CallbackContext):
        """Handle /add command for manual entry"""
        context.user_data['user_id'] = update.effective_user.id
        context.user_data['user_name'] = update.effective_user.full_name
        
        await update.message.reply_text(
            "✏️ **Manual Payment Entry**\n\n"
            "Enter recipient or store name:"
        )
        return NAME
    
    async def get_name(self, update: Update, context: CallbackContext):
        """Get transaction name"""
        name = update.message.text.strip()
        
        if not name:
            # Use default if available
            name = context.user_data.get('default_name', 'Payment')
        
        context.user_data['name'] = name
        
        # Check for default amount
        default_amount = context.user_data.get('default_amount', 0)
        
        if default_amount > 0:
            await update.message.reply_text(
                f"💰 Amount in NGN (press Enter for ₦{default_amount:,.2f}):"
            )
        else:
            await update.message.reply_text("💰 Enter amount in NGN (e.g., 48000.00):")
        
        return AMOUNT
    
    async def get_amount(self, update: Update, context: CallbackContext):
        """Get transaction amount"""
        amount_text = update.message.text.strip()
        
        if amount_text == "":
            # Use default amount
            amount = context.user_data.get('default_amount', 0)
        else:
            try:
                # Clean amount string - handle Naira format
                amount_text = amount_text.replace('₦', '').replace('NGN', '').replace(',', '').replace('$', '').strip()
                amount = float(amount_text)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number (e.g., 48000.00):")
                return AMOUNT
        
        context.user_data['amount'] = amount
        
        # Check for default date
        default_date = context.user_data.get('default_date', '')
        
        if default_date:
            await update.message.reply_text(
                f"📅 Date (YYYY-MM-DD, press Enter for {default_date}):"
            )
        else:
            await update.message.reply_text("📅 Enter date (YYYY-MM-DD or 'today'):")
        
        return DATE
    
    async def get_date(self, update: Update, context: CallbackContext):
        """Get transaction date"""
        date_text = update.message.text.strip()
        
        if date_text == "":
            # Use default date
            date_text = context.user_data.get('default_date', '')
        elif date_text.lower() == 'today':
            date_text = datetime.now().strftime('%Y-%m-%d')
        
        # Validate date
        try:
            datetime.strptime(date_text, '%Y-%m-%d')
            context.user_data['date'] = date_text
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid date format. Please use YYYY-MM-DD:"
            )
            return DATE
        
        # Category selection for payments
        keyboard = [
            [
                InlineKeyboardButton("💸 Transfer", callback_data="Transfer"),
                InlineKeyboardButton("🛍️ Shopping", callback_data="Shopping")
            ],
            [
                InlineKeyboardButton("🍔 Food", callback_data="Food"),
                InlineKeyboardButton("🚗 Transport", callback_data="Transport")
            ],
            [
                InlineKeyboardButton("🏠 Bills", callback_data="Bills"),
                InlineKeyboardButton("💼 Business", callback_data="Business")
            ],
            [
                InlineKeyboardButton("🎬 Entertainment", callback_data="Entertainment"),
                InlineKeyboardButton("❓ Other", callback_data="Other")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📊 Select payment category:",
            reply_markup=reply_markup
        )
        
        return CATEGORY
    
    async def get_category(self, update: Update, context: CallbackContext):
        """Handle category selection"""
        query = update.callback_query
        await query.answer()
        
        category = query.data
        context.user_data['category'] = category
        
        # Save transaction
        receipt_data = context.user_data.get('receipt_data', ReceiptData())
        
        transaction_data = {
            'user_id': context.user_data.get('user_id'),
            'user_name': context.user_data.get('user_name'),
            'name': context.user_data.get('name'),
            'amount': context.user_data.get('amount'),
            'currency': 'NGN',
            'date': context.user_data.get('date'),
            'category': category,
            'store': context.user_data.get('name'),
            'recipient': context.user_data.get('name'),
            'description': receipt_data.summary if receipt_data else '',
            'items': receipt_data.items if receipt_data else [],
            'confidence': receipt_data.confidence if receipt_data else 0,
            'summary': receipt_data.summary if receipt_data else '',
            'has_image': 'image_bytes' in context.user_data
        }
        
        if self.sheets.add_transaction(transaction_data):
            response = f"""
✅ **Payment Saved!**

📝 **Recipient:** {transaction_data['name']}
💰 **Amount:** ₦{transaction_data['amount']:,.2f}
📅 **Date:** {transaction_data['date']}
📊 **Category:** {transaction_data['category']}

💾 Saved to Google Sheets
"""
            
            if 'image_bytes' in context.user_data:
                response += "📸 Includes receipt image\n"
            
            await query.edit_message_text(response)
        else:
            await query.edit_message_text("❌ Failed to save. Please check Google Sheets setup.")
        
        context.user_data.clear()
        return ConversationHandler.END
    
    async def search_transactions(self, update: Update, context: CallbackContext):
        """Handle /search command"""
        if context.args:
            name = ' '.join(context.args)
            transactions = self.sheets.get_transactions(name)
            
            if not transactions:
                await update.message.reply_text(f"🔍 No transactions found for '{name}'")
                return
            
            # Format response
            response = f"📊 **Transactions for {name}**\n\n"
            total = 0.0
            
            for i, t in enumerate(transactions[-10:], 1):  # Last 10
                amount = float(t.get('Amount', 0))
                total += amount
                
                date = t.get('Date', 'N/A')[:10]
                category = t.get('Category', 'N/A')
                currency = t.get('Currency', 'NGN')
                
                response += f"{i}. **{date}** - {currency} {amount:,.2f}\n"
                response += f"   📊 {category}\n\n"
            
            response += f"💰 **Total:** ₦{total:,.2f}\n"
            response += f"📈 {len(transactions)} transactions total"
            
            if len(transactions) > 10:
                response += f" (showing last 10)"
            
        else:
            await update.message.reply_text(
                "🔍 **Search Transactions**\n\n"
                "Usage: /search [name]\n"
                "Example: /search Funke\n"
                "Example: /search OPay"
            )
            return
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    async def total_command(self, update: Update, context: CallbackContext):
        """Handle /total command"""
        name = ' '.join(context.args) if context.args else None
        
        total = self.sheets.get_total(name)
        
        if name:
            # Get some stats
            transactions = self.sheets.get_transactions(name)
            count = len(transactions)
            avg = total / count if count > 0 else 0
            
            response = f"""
💰 **Financial Summary for {name}**

📊 Total Transactions: {count}
💰 Total Amount: ₦{total:,.2f}
📈 Average per transaction: ₦{avg:,.2f}

"""
            
            if count > 0:
                # Get last transaction
                last = transactions[-1]
                last_date = last.get('Date', '')[:10]
                last_amount = float(last.get('Amount', 0))
                last_currency = last.get('Currency', 'NGN')
                
                response += f"📅 Last transaction: {last_date} - {last_currency} {last_amount:,.2f}"
            
        else:
            # Overall total
            names = self.sheets.get_names()
            count = sum(len(self.sheets.get_transactions(name)) for name in names)
            
            response = f"""
💰 **Overall Financial Summary**

👥 People in database: {len(names)}
📊 Total Transactions: {count}
💰 Total Amount: ₦{total:,.2f}
"""
        
        await update.message.reply_text(response)
    
    async def list_names(self, update: Update, context: CallbackContext):
        """Handle /list command"""
        names = self.sheets.get_names()
        
        if not names:
            await update.message.reply_text("📭 No transactions yet. Send a receipt to get started!")
            return
        
        response = "👥 **People/Stores in Database**\n\n"
        
        for i, name in enumerate(names, 1):
            total = self.sheets.get_total(name)
            count = len(self.sheets.get_transactions(name))
            
            response += f"{i}. **{name}**\n"
            response += f"   📊 {count} transactions | 💰 ₦{total:,.2f}\n\n"
        
        response += f"📈 Total: {len(names)} entries"
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    async def stats_command(self, update: Update, context: CallbackContext):
        """Handle /stats command for statistics"""
        # Get all transactions
        transactions = self.sheets.get_transactions()
        
        if not transactions:
            await update.message.reply_text("📭 No transactions yet.")
            return
        
        # Calculate stats
        total_amount = sum(float(t.get('Amount', 0)) for t in transactions)
        avg_amount = total_amount / len(transactions)
        
        # Get categories
        categories = {}
        for t in transactions:
            cat = t.get('Category', 'Unknown')
            amount = float(t.get('Amount', 0))
            categories[cat] = categories.get(cat, 0) + amount
        
        # Sort categories
        sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        
        # Format response
        response = "📈 **Expense Statistics**\n\n"
        response += f"📊 Total Transactions: {len(transactions)}\n"
        response += f"💰 Total Amount: ₦{total_amount:,.2f}\n"
        response += f"📈 Average: ₦{avg_amount:,.2f}\n\n"
        
        response += "🏷️ **Spending by Category:**\n"
        for cat, amount in sorted_cats[:5]:  # Top 5 categories
            percentage = (amount / total_amount) * 100
            response += f"• {cat}: ₦{amount:,.2f} ({percentage:.1f}%)\n"
        
        await update.message.reply_text(response)
    
    async def cancel(self, update: Update, context: CallbackContext):
        """Cancel operation"""
        context.user_data.clear()
        await update.message.reply_text("❌ Operation cancelled.")
        return ConversationHandler.END

def main():
    """Start the bot"""
    print("🚀 Starting AI Payment Receipt Bot...")
    print("=" * 50)
    
    # Check environment variables
    token = os.getenv('TELEGRAM_TOKEN')
    openai_key = os.getenv('OPENAI_API_KEY')
    
    if not token:
        print("❌ TELEGRAM_TOKEN not found!")
        return
    
    if not openai_key:
        print("⚠️ OPENAI_API_KEY not found - AI features will be disabled")
    else:
        print("✅ OpenAI API key found")
    
    # Check Google Sheets
    if not os.getenv('GOOGLE_CREDS_JSON'):
        print("⚠️ GOOGLE_CREDS_JSON not found")
    if not os.getenv('SHEET_URL'):
        print("⚠️ SHEET_URL not found")
    
    # Initialize bot
    bot = AIReceiptBot()
    
    # Create application with webhook to avoid conflict
    app = Application.builder().token(token).build()
    
    # Add commands
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("search", bot.search_transactions))
    app.add_handler(CommandHandler("total", bot.total_command))
    app.add_handler(CommandHandler("list", bot.list_names))
    app.add_handler(CommandHandler("stats", bot.stats_command))
    
    # Photo handler
    app.add_handler(MessageHandler(filters.PHOTO, bot.handle_photo))
    
    # Button handler
    app.add_handler(CallbackQueryHandler(bot.button_handler, pattern="^(save_ai|edit_manual|cancel)$"))
    
    # Conversation handler for manual entry
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add", bot.add_manual),
            CallbackQueryHandler(bot.button_handler, pattern="^edit_manual$")
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_name)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_amount)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_date)],
            CATEGORY: [CallbackQueryHandler(bot.get_category)]
        },
        fallbacks=[CommandHandler("cancel", bot.cancel)],
        per_message=True  # Add this to fix conversation tracking
    )
    
    app.add_handler(conv_handler)
    
    # Start bot with specific parameters to avoid conflict
    print("🤖 Bot is starting...")
    print("📱 Visit Telegram and send /start to your bot")
    print("📸 Try sending a payment receipt screenshot!")
    print("=" * 50)
    
    # Use specific update types and drop pending updates to avoid conflict
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True  # This will clear any pending updates
    )

if __name__ == '__main__':
    main()
