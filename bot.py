import logging
from telegram import Update, Bot
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes,
    ConversationHandler
)
from telegram.constants import ParseMode
import io
import os
from datetime import datetime

from config import BOT_TOKEN
from image_processor import ReceiptProcessor
from google_sheets import GoogleSheetsHandler

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for conversation
WAITING_FOR_RECEIPT, CONFIRM_DATA = range(2)

class ReceiptBot:
    def __init__(self):
        self.bot_token = BOT_TOKEN
        self.gs_handler = GoogleSheetsHandler()
        self.user_data_cache = {}
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a welcome message when the command /start is issued."""
        user = update.effective_user
        welcome_message = (
            f"👋 Hello {user.first_name}!\n\n"
            "I'm your Receipt Recording Bot! 📄\n\n"
            "Send me a photo of your receipt and I'll:\n"
            "1. 📸 Extract text from the image\n"
            "2. 🔍 Parse important information\n"
            "3. 📊 Save it to Google Sheets\n\n"
            "Just send me a receipt photo to get started!"
        )
        
        await update.message.reply_text(welcome_message)
        return WAITING_FOR_RECEIPT
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a help message."""
        help_text = (
            "📋 **How to use this bot:**\n\n"
            "1. Take a clear photo of your receipt\n"
            "2. Send it to this bot\n"
            "3. The bot will extract and display the information\n"
            "4. Confirm to save it to Google Sheets\n\n"
            "**Tips for better results:**\n"
            "• Ensure good lighting\n"
            "• Keep the receipt flat\n"
            "• Capture the entire receipt\n"
            "• Avoid glare and shadows\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help message\n"
            "/cancel - Cancel current operation"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_receipt_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle receipt photo upload."""
        user = update.effective_user
        message = update.message
        
        if not message.photo:
            await message.reply_text("Please send a photo of your receipt.")
            return WAITING_FOR_RECEIPT
        
        # Get the highest quality photo
        photo = message.photo[-1]
        
        # Inform user we're processing
        processing_msg = await message.reply_text(
            "🔄 Processing your receipt... Please wait."
        )
        
        try:
            # Download the photo
            photo_file = await context.bot.get_file(photo.file_id)
            photo_bytes = await photo_file.download_as_bytearray()
            
            # Process the image
            extracted_text = ReceiptProcessor.extract_text(bytes(photo_bytes))
            
            if not extracted_text:
                await processing_msg.edit_text(
                    "❌ Could not extract text from the image. "
                    "Please try again with a clearer photo."
                )
                return WAITING_FOR_RECEIPT
            
            # Parse receipt data
            receipt_data = ReceiptProcessor.parse_receipt_text(extracted_text)
            
            # Add user info
            receipt_data["user_id"] = user.id
            receipt_data["username"] = user.username or user.first_name
            receipt_data["raw_text"] = extracted_text[:1000]  # Limit raw text
            receipt_data["image_file"] = f"{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            
            # Cache data for confirmation
            self.user_data_cache[user.id] = {
                "receipt_data": receipt_data,
                "photo_bytes": photo_bytes
            }
            
            # Prepare summary message
            summary = self._format_receipt_summary(receipt_data)
            
            await processing_msg.edit_text(
                f"✅ Text extracted successfully!\n\n"
                f"{summary}\n\n"
                f"Should I save this to Google Sheets? (Yes/No)"
            )
            
            return CONFIRM_DATA
            
        except Exception as e:
            logger.error(f"Error processing receipt: {e}")
            await processing_msg.edit_text(
                "❌ An error occurred while processing your receipt. "
                "Please try again."
            )
            return WAITING_FOR_RECEIPT
    
    def _format_receipt_summary(self, receipt_data):
        """Format receipt data for display."""
        summary = (
            f"🏪 **Store:** {receipt_data.get('store_name', 'Not found')}\n"
            f"💰 **Total:** ${receipt_data.get('total_amount', '0.00')}\n"
            f"📅 **Date:** {receipt_data.get('date', 'Not found')}\n"
            f"⏰ **Time:** {receipt_data.get('time', 'Not found')}\n"
            f"💳 **Payment:** {receipt_data.get('payment_method', 'Not found')}\n"
        )
        
        if receipt_data.get('tax_amount'):
            summary += f"🧾 **Tax:** ${receipt_data.get('tax_amount')}\n"
        
        return summary
    
    async def confirm_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle confirmation to save data."""
        user = update.effective_user
        message_text = update.message.text.lower()
        
        if user.id not in self.user_data_cache:
            await update.message.reply_text(
                "No receipt data found. Please send a receipt photo first."
            )
            return WAITING_FOR_RECEIPT
        
        if message_text in ['yes', 'y', 'save', 'confirm']:
            # Save to Google Sheets
            receipt_data = self.user_data_cache[user.id]["receipt_data"]
            
            saving_msg = await update.message.reply_text("💾 Saving to Google Sheets...")
            
            success = self.gs_handler.append_receipt_data(receipt_data)
            
            if success:
                await saving_msg.edit_text(
                    f"✅ Receipt data saved successfully!\n\n"
                    f"📊 **Summary saved:**\n"
                    f"• Store: {receipt_data.get('store_name')}\n"
                    f"• Total: ${receipt_data.get('total_amount')}\n"
                    f"• Date: {receipt_data.get('date')}\n\n"
                    f"You can send another receipt or use /help for more options."
                )
            else:
                await saving_msg.edit_text(
                    "❌ Failed to save to Google Sheets. Please try again later."
                )
            
            # Clear cache
            del self.user_data_cache[user.id]
            
        elif message_text in ['no', 'n', 'cancel']:
            await update.message.reply_text(
                "❌ Receipt not saved. You can send another receipt if you'd like."
            )
            del self.user_data_cache[user.id]
        else:
            await update.message.reply_text(
                "Please reply with 'Yes' to save or 'No' to cancel."
            )
            return CONFIRM_DATA
        
        return WAITING_FOR_RECEIPT
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the current operation."""
        user = update.effective_user
        
        if user.id in self.user_data_cache:
            del self.user_data_cache[user.id]
        
        await update.message.reply_text(
            "Operation cancelled. Send a receipt photo to start again."
        )
        return ConversationHandler.END
    
    def run(self):
        """Run the bot."""
        # Create the Application
        application = Application.builder().token(self.bot_token).build()
        
        # Create conversation handler
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.start)],
            states={
                WAITING_FOR_RECEIPT: [
                    MessageHandler(filters.PHOTO, self.handle_receipt_photo),
                    CommandHandler('help', self.help_command),
                    CommandHandler('cancel', self.cancel)
                ],
                CONFIRM_DATA: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.confirm_save),
                    CommandHandler('cancel', self.cancel)
                ]
            },
            fallbacks=[CommandHandler('cancel', self.cancel)]
        )
        
        # Add handlers
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler('help', self.help_command))
        
        # Start the bot
        print("🤖 Bot is starting...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """Main function to run the bot."""
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN not found in environment variables!")
        print("Please create a .env file with your Telegram Bot Token.")
        return
    
    bot = ReceiptBot()
    bot.run()

if __name__ == '__main__':
    main()
