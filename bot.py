import os
import json
import base64
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from io import BytesIO

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

# Google Sheets
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    print("Google Sheets not available")

# OpenAI
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("OpenAI not available")

# Image processing
try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    print("Pillow not available")

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
    currency: str = "USD"
    items: List[Dict] = None
    summary: str = ""
    confidence: float = 0.0
    
    def __post_init__(self):
        if self.items is None:
            self.items = []

class OpenAIAnalyzer:
    """Analyze receipts using OpenAI GPT-4 Vision"""
    
    def __init__(self, api_key: str = None):
        self.client = None
        self.available = OPENAI_AVAILABLE
        
        if api_key and OPENAI_AVAILABLE:
            try:
                self.client = AsyncOpenAI(api_key=api_key)
                logger.info("✅ OpenAI initialized")
            except Exception as e:
                logger.error(f"OpenAI init failed: {e}")
                self.available = False
        else:
            self.available = False
    
    async def analyze_receipt(self, image_bytes: bytes) -> ReceiptData:
        """Analyze receipt image using GPT-4 Vision"""
        if not self.available or not self.client:
            logger.warning("OpenAI not available")
            return ReceiptData()
        
        try:
            # Encode image to base64
            image_b64 = base64.b64encode(image_bytes).decode('utf-8')
            
            # Prepare prompt for receipt analysis
            prompt = """You are a receipt analysis expert. Analyze this receipt image and extract the following information in JSON format:

{
    "store_name": "Name of the store/business",
    "total_amount": 0.00,
    "date": "YYYY-MM-DD",
    "currency": "USD or other",
    "items": [
        {"name": "item name", "price": 0.00, "quantity": 1}
    ],
    "summary": "Brief description of purchase",
    "confidence": 0.95
}

Rules:
1. Return ONLY valid JSON, no other text
2. If date is not available, use today's date
3. If total amount is not clear, estimate from items
4. Confidence should be 0.0 to 1.0 based on how clear the receipt is
5. Date must be in YYYY-MM-DD format
6. Keep item list concise, max 10 items"""
            
            # Call OpenAI API
            response = await self.client.chat.completions.create(
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
                max_tokens=1000,
                temperature=0.1
            )
            
            # Parse response
            content = response.choices[0].message.content
            
            # Extract JSON from response
            try:
                # Clean the response
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
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
                    currency=data.get("currency", "USD"),
                    items=data.get("items", []),
                    summary=data.get("summary", ""),
                    confidence=float(data.get("confidence", 0))
                )
                
                logger.info(f"✅ AI analysis complete: {receipt.store_name} - ${receipt.total_amount}")
                return receipt
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AI response: {e}")
                logger.error(f"Response: {content}")
                return ReceiptData()
                
        except Exception as e:
            logger.error(f"OpenAI analysis error: {e}")
            return ReceiptData()
    
    def format_receipt_for_display(self, receipt: ReceiptData) -> str:
        """Format receipt data for display"""
        lines = []
        lines.append("🤖 **AI Receipt Analysis**")
        lines.append("─" * 40)
        
        if receipt.store_name:
            lines.append(f"🏪 **Store:** {receipt.store_name}")
        
        if receipt.total_amount > 0:
            lines.append(f"💰 **Total:** {receipt.currency} {receipt.total_amount:.2f}")
        
        if receipt.date:
            lines.append(f"📅 **Date:** {receipt.date}")
        
        if receipt.items:
            lines.append("\n🛒 **Items:**")
            for i, item in enumerate(receipt.items[:5], 1):  # Show first 5 items
                name = item.get('name', 'Item')
                price = item.get('price', 0)
                qty = item.get('quantity', 1)
                lines.append(f"  {i}. {name} - {receipt.currency} {price:.2f} x{qty}")
            
            if len(receipt.items) > 5:
                lines.append(f"  ... and {len(receipt.items) - 5} more items")
        
        if receipt.summary:
            lines.append(f"\n📝 **Summary:** {receipt.summary}")
        
        if receipt.confidence > 0:
            confidence_percent = receipt.confidence * 100
            lines.append(f"\n🎯 **Confidence:** {confidence_percent:.1f}%")
        
        return "\n".join(lines)

class GoogleSheetManager:
    """Manages Google Sheets operations"""
    
    def __init__(self):
        if not GOOGLE_AVAILABLE:
            raise ImportError("Google Sheets libraries not available")
        
        self.sheet = None
        self._initialize()
    
    def _initialize(self):
        """Initialize Google Sheets connection"""
        try:
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
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets: {e}")
            self.sheet = None
    
    def _setup_headers(self):
        """Setup headers in sheet"""
        try:
            existing = self.sheet.row_values(1)
            
            if not existing or len(existing) < 10:
                headers = [
                    'ID', 'Timestamp', 'User ID', 'Username', 'Name',
                    'Amount', 'Date', 'Category', 'Description',
                    'Store', 'AI Confidence', 'Items Count', 'Currency',
                    'AI Summary', 'Has Image'
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
            
            return max([int(id) for id in ids if id.isdigit()], default=0) + 1
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
                data.get('date', ''),
                data.get('category', ''),
                data.get('description', ''),
                data.get('store', ''),
                data.get('confidence', 0),
                len(items),
                data.get('currency', 'USD'),
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
        self.sheets = None
        if GOOGLE_AVAILABLE:
            try:
                self.sheets = GoogleSheetManager()
                logger.info("✅ Google Sheets ready")
            except Exception as e:
                logger.error(f"Google Sheets failed: {e}")
        
        logger.info("🤖 AI Receipt Bot initialized")
    
    async def start(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        user = update.effective_user
        
        welcome = f"""
👋 Hello {user.first_name}!

🤖 **AI Receipt Tracker Bot**

I'm powered by AI to automatically scan and analyze your receipts!

**What I can do:**
• 📸 Scan receipt images using AI
• 🧠 Extract store, amount, date, items
• 💾 Save to Google Sheets
• 🔍 Search and analyze expenses

**Commands:**
/start - Welcome message
/add - Add manually
/search [name] - Find transactions
/total [name] - Calculate total
/list - List all names
/help - Detailed help

**Just send me a receipt photo to get started!** 📸
"""
        await update.message.reply_text(welcome)
    
    async def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        help_text = """
📚 **AI Receipt Bot Help**

**Quick Start:**
1. Send a receipt photo
2. AI will analyze it automatically
3. Confirm details
4. ✅ Saved to Google Sheets

**Commands:**
• /start - Welcome message
• /add - Manual entry
• /search [name] - Search transactions
• /total [name] - Calculate total
• /list - List all names
• /stats - Get statistics

**AI Features:**
• Automatic receipt scanning
• Itemized breakdown
• Store name detection
• Date and amount extraction

**Example:**
/search John
/total Shopping
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
            image_bytes = await file.download_as_bytearray()
            
            # Store in context
            context.user_data['image_bytes'] = image_bytes
            context.user_data['user_id'] = user.id
            context.user_data['user_name'] = user.full_name
            
            # Start AI analysis
            await update.message.reply_text("🤖 AI is analyzing your receipt...")
            
            # Analyze with OpenAI
            receipt_data = await self.ai_analyzer.analyze_receipt(image_bytes)
            context.user_data['receipt_data'] = receipt_data
            
            # Show analysis results
            if receipt_data.store_name or receipt_data.total_amount > 0:
                display_text = self.ai_analyzer.format_receipt_for_display(receipt_data)
                
                # Add action buttons
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Save with AI Data", callback_data="save_ai"),
                        InlineKeyboardButton("✏️ Edit Details", callback_data="edit_manual")
                    ],
                    [
                        InlineKeyboardButton("🔄 Re-analyze", callback_data="reanalyze"),
                        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    display_text + "\n\n**What would you like to do?**",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                # AI analysis failed
                await update.message.reply_text(
                    "❌ AI couldn't analyze the receipt properly.\n\n"
                    "Please enter details manually or try another photo."
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
            logger.error(f"Error handling photo: {e}")
            await update.message.reply_text(
                "❌ Error processing image. Please try again or enter manually."
            )
    
    async def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "save_ai":
            # Save with AI data
            receipt_data = context.user_data.get('receipt_data')
            
            if not receipt_data:
                await query.edit_message_text("❌ No receipt data found.")
                return
            
            # Save to Google Sheets
            if self.sheets:
                transaction_data = {
                    'user_id': context.user_data.get('user_id'),
                    'user_name': context.user_data.get('user_name'),
                    'name': receipt_data.store_name or "Receipt",
                    'amount': receipt_data.total_amount,
                    'date': receipt_data.date,
                    'category': 'Shopping',
                    'store': receipt_data.store_name,
                    'description': receipt_data.summary,
                    'items': receipt_data.items,
                    'confidence': receipt_data.confidence,
                    'currency': receipt_data.currency,
                    'summary': receipt_data.summary,
                    'has_image': True
                }
                
                if self.sheets.add_transaction(transaction_data):
                    response = f"""
✅ **Saved Successfully!**

🤖 AI Analysis Complete:
🏪 Store: {receipt_data.store_name}
💰 Total: {receipt_data.currency} {receipt_data.total_amount:.2f}
📅 Date: {receipt_data.date}
🎯 Confidence: {receipt_data.confidence * 100:.1f}%

💾 Saved to Google Sheets
"""
                    await query.edit_message_text(response)
                else:
                    await query.edit_message_text("❌ Failed to save to Google Sheets.")
            else:
                await query.edit_message_text("❌ Google Sheets not available.")
            
            context.user_data.clear()
            
        elif query.data == "edit_manual":
            # Start manual editing
            receipt_data = context.user_data.get('receipt_data', ReceiptData())
            
            # Pre-fill with AI data if available
            default_name = receipt_data.store_name or ""
            default_amount = receipt_data.total_amount or 0
            default_date = receipt_data.date or datetime.now().strftime('%Y-%m-%d')
            
            context.user_data['default_name'] = default_name
            context.user_data['default_amount'] = default_amount
            context.user_data['default_date'] = default_date
            
            await query.edit_message_text(
                f"✏️ **Manual Entry**\n\n"
                f"Store name (press Enter for '{default_name}'):"
            )
            return NAME
            
        elif query.data == "reanalyze":
            # Re-analyze the image
            image_bytes = context.user_data.get('image_bytes')
            
            if image_bytes:
                await query.edit_message_text("🔄 Re-analyzing with AI...")
                
                receipt_data = await self.ai_analyzer.analyze_receipt(image_bytes)
                context.user_data['receipt_data'] = receipt_data
                
                display_text = self.ai_analyzer.format_receipt_for_display(receipt_data)
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Save with AI Data", callback_data="save_ai"),
                        InlineKeyboardButton("✏️ Edit Details", callback_data="edit_manual")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    display_text + "\n\n**What would you like to do?**",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text("❌ No image found to re-analyze.")
                
        elif query.data == "cancel":
            await query.edit_message_text("❌ Operation cancelled.")
            context.user_data.clear()
    
    async def add_manual(self, update: Update, context: CallbackContext):
        """Handle /add command for manual entry"""
        context.user_data['user_id'] = update.effective_user.id
        context.user_data['user_name'] = update.effective_user.full_name
        
        await update.message.reply_text(
            "✏️ **Manual Entry**\n\n"
            "Enter the store or person's name:"
        )
        return NAME
    
    async def get_name(self, update: Update, context: CallbackContext):
        """Get transaction name"""
        name = update.message.text.strip()
        
        if not name:
            # Use default if available
            name = context.user_data.get('default_name', '')
        
        if not name:
            await update.message.reply_text("❌ Name cannot be empty. Please enter a name:")
            return NAME
        
        context.user_data['name'] = name
        
        # Check for default amount
        default_amount = context.user_data.get('default_amount', 0)
        
        if default_amount > 0:
            await update.message.reply_text(
                f"💰 Amount (press Enter for ${default_amount:.2f}):"
            )
        else:
            await update.message.reply_text("💰 Enter the amount (e.g., 25.50):")
        
        return AMOUNT
    
    async def get_amount(self, update: Update, context: CallbackContext):
        """Get transaction amount"""
        amount_text = update.message.text.strip()
        
        if amount_text == "":
            # Use default amount
            amount = context.user_data.get('default_amount', 0)
        else:
            try:
                # Clean amount string
                amount_text = amount_text.replace('$', '').replace(',', '').strip()
                amount = float(amount_text)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number:")
                return AMOUNT
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be greater than 0. Please enter amount:")
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
        
        # Category selection
        keyboard = [
            [
                InlineKeyboardButton("🍔 Food", callback_data="Food"),
                InlineKeyboardButton("🛍️ Shopping", callback_data="Shopping")
            ],
            [
                InlineKeyboardButton("🚗 Transport", callback_data="Transport"),
                InlineKeyboardButton("🏠 Bills", callback_data="Bills")
            ],
            [
                InlineKeyboardButton("🏥 Medical", callback_data="Medical"),
                InlineKeyboardButton("🎬 Entertainment", callback_data="Entertainment")
            ],
            [
                InlineKeyboardButton("💼 Business", callback_data="Business"),
                InlineKeyboardButton("❓ Other", callback_data="Other")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📊 Select category:",
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
        if self.sheets:
            receipt_data = context.user_data.get('receipt_data', ReceiptData())
            
            transaction_data = {
                'user_id': context.user_data.get('user_id'),
                'user_name': context.user_data.get('user_name'),
                'name': context.user_data.get('name'),
                'amount': context.user_data.get('amount'),
                'date': context.user_data.get('date'),
                'category': category,
                'store': context.user_data.get('name'),
                'description': receipt_data.summary if receipt_data else '',
                'items': receipt_data.items if receipt_data else [],
                'confidence': receipt_data.confidence if receipt_data else 0,
                'currency': receipt_data.currency if receipt_data else 'USD',
                'summary': receipt_data.summary if receipt_data else '',
                'has_image': 'image_bytes' in context.user_data
            }
            
            if self.sheets.add_transaction(transaction_data):
                response = f"""
✅ **Transaction Saved!**

📝 Name: {transaction_data['name']}
💰 Amount: ${transaction_data['amount']:.2f}
📅 Date: {transaction_data['date']}
📊 Category: {transaction_data['category']}

💾 Saved to Google Sheets
"""
                
                if 'image_bytes' in context.user_data:
                    response += "📸 Includes receipt image\n"
                
                await query.edit_message_text(response)
            else:
                await query.edit_message_text("❌ Failed to save. Please check Google Sheets setup.")
        else:
            await query.edit_message_text("❌ Google Sheets not available.")
        
        context.user_data.clear()
        return ConversationHandler.END
    
    async def search_transactions(self, update: Update, context: CallbackContext):
        """Handle /search command"""
        if not self.sheets:
            await update.message.reply_text("❌ Google Sheets not available.")
            return
        
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
                store = t.get('Store', '')
                
                response += f"{i}. **{date}** - ${amount:.2f}\n"
                response += f"   📊 {category}"
                if store:
                    response += f" | 🏪 {store}"
                response += "\n\n"
            
            response += f"💰 **Total: ${total:.2f}**\n"
            response += f"📈 {len(transactions)} transactions total"
            
            if len(transactions) > 10:
                response += f" (showing last 10)"
            
        else:
            await update.message.reply_text(
                "🔍 **Search Transactions**\n\n"
                "Usage: /search [name]\n"
                "Example: /search John\n"
                "Example: /search Starbucks"
            )
            return
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    async def total_command(self, update: Update, context: CallbackContext):
        """Handle /total command"""
        if not self.sheets:
            await update.message.reply_text("❌ Google Sheets not available.")
            return
        
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
💰 Total Amount: ${total:.2f}
📈 Average per transaction: ${avg:.2f}

"""
            
            if count > 0:
                # Get last transaction
                last = transactions[-1]
                last_date = last.get('Date', '')[:10]
                last_amount = float(last.get('Amount', 0))
                
                response += f"📅 Last transaction: {last_date} - ${last_amount:.2f}"
            
        else:
            # Overall total
            names = self.sheets.get_names()
            count = sum(len(self.sheets.get_transactions(name)) for name in names)
            
            response = f"""
💰 **Overall Financial Summary**

👥 People in database: {len(names)}
📊 Total Transactions: {count}
💰 Total Amount: ${total:.2f}
"""
        
        await update.message.reply_text(response)
    
    async def list_names(self, update: Update, context: CallbackContext):
        """Handle /list command"""
        if not self.sheets:
            await update.message.reply_text("❌ Google Sheets not available.")
            return
        
        names = self.sheets.get_names()
        
        if not names:
            await update.message.reply_text("📭 No transactions yet. Send a receipt to get started!")
            return
        
        response = "👥 **People in Database**\n\n"
        
        for i, name in enumerate(names, 1):
            total = self.sheets.get_total(name)
            count = len(self.sheets.get_transactions(name))
            
            response += f"{i}. **{name}**\n"
            response += f"   📊 {count} transactions | 💰 ${total:.2f}\n\n"
        
        response += f"📈 Total: {len(names)} people"
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    async def stats_command(self, update: Update, context: CallbackContext):
        """Handle /stats command for statistics"""
        if not self.sheets:
            await update.message.reply_text("❌ Google Sheets not available.")
            return
        
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
        response += f"💰 Total Amount: ${total_amount:.2f}\n"
        response += f"📈 Average: ${avg_amount:.2f}\n\n"
        
        response += "🏷️ **Spending by Category:**\n"
        for cat, amount in sorted_cats[:5]:  # Top 5 categories
            percentage = (amount / total_amount) * 100
            response += f"• {cat}: ${amount:.2f} ({percentage:.1f}%)\n"
        
        # Most frequent store
        stores = {}
        for t in transactions:
            store = t.get('Store', 'Unknown')
            stores[store] = stores.get(store, 0) + 1
        
        if stores:
            top_store = max(stores.items(), key=lambda x: x[1])
            response += f"\n🏪 **Most Frequent Store:** {top_store[0]} ({top_store[1]} times)"
        
        await update.message.reply_text(response)
    
    async def cancel(self, update: Update, context: CallbackContext):
        """Cancel operation"""
        context.user_data.clear()
        await update.message.reply_text("❌ Operation cancelled.")
        return ConversationHandler.END

def main():
    """Start the bot"""
    print("🚀 Starting AI Receipt Bot...")
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
    
    # Initialize bot
    bot = AIReceiptBot()
    
    # Create application
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
    app.add_handler(CallbackQueryHandler(bot.button_handler, pattern="^(save_ai|edit_manual|reanalyze|cancel)$"))
    
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
        fallbacks=[CommandHandler("cancel", bot.cancel)]
    )
    
    app.add_handler(conv_handler)
    
    # Start bot
    print("🤖 Bot is running...")
    print("📱 Visit Telegram and send /start to your bot")
    print("📸 Try sending a receipt photo for AI analysis!")
    print("=" * 50)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
