#!/usr/bin/env python3
"""
Script to set up Telegram webhook for the bot
Run this script to configure the webhook URL
"""

import os
import asyncio
from telegram import Bot
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def setup_webhook():
    """Set up the webhook for the Telegram bot"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return False
    
    # Get the webhook URL from environment or use a default
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        # Try to construct from Railway URL
        railway_url = os.environ.get("RAILWAY_STATIC_URL")
        if railway_url:
            webhook_url = f"{railway_url}/telegram-webhook"
        else:
            logger.error("WEBHOOK_URL or RAILWAY_STATIC_URL environment variable not set!")
            logger.info("Please set WEBHOOK_URL to your bot's webhook endpoint")
            return False
    
    try:
        bot = Bot(token=token)
        
        # Get bot info
        bot_info = await bot.get_me()
        logger.info(f"Bot info: {bot_info.first_name} (@{bot_info.username})")
        
        # Set webhook
        logger.info(f"Setting webhook to: {webhook_url}")
        result = await bot.set_webhook(url=webhook_url)
        
        if result:
            logger.info("✅ Webhook set successfully!")
            
            # Get webhook info
            webhook_info = await bot.get_webhook_info()
            logger.info(f"Webhook URL: {webhook_info.url}")
            logger.info(f"Webhook pending updates: {webhook_info.pending_update_count}")
            
            return True
        else:
            logger.error("❌ Failed to set webhook")
            return False
            
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return False

async def delete_webhook():
    """Delete the webhook (for testing with polling)"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return False
    
    try:
        bot = Bot(token=token)
        result = await bot.delete_webhook()
        if result:
            logger.info("✅ Webhook deleted successfully!")
            return True
        else:
            logger.error("❌ Failed to delete webhook")
            return False
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
        return False

async def get_webhook_info():
    """Get current webhook information"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return False
    
    try:
        bot = Bot(token=token)
        webhook_info = await bot.get_webhook_info()
        
        logger.info("Current webhook info:")
        logger.info(f"  URL: {webhook_info.url}")
        logger.info(f"  Has custom certificate: {webhook_info.has_custom_certificate}")
        logger.info(f"  Pending updates: {webhook_info.pending_update_count}")
        logger.info(f"  Last error date: {webhook_info.last_error_date}")
        logger.info(f"  Last error message: {webhook_info.last_error_message}")
        logger.info(f"  Max connections: {webhook_info.max_connections}")
        logger.info(f"  Allowed updates: {webhook_info.allowed_updates}")
        
        return True
    except Exception as e:
        logger.error(f"Error getting webhook info: {e}")
        return False

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "setup":
            asyncio.run(setup_webhook())
        elif command == "delete":
            asyncio.run(delete_webhook())
        elif command == "info":
            asyncio.run(get_webhook_info())
        else:
            logger.error("Unknown command. Use: setup, delete, or info")
    else:
        logger.info("Usage: python setup_webhook.py [setup|delete|info]")
        logger.info("  setup  - Set up the webhook")
        logger.info("  delete - Delete the webhook")
        logger.info("  info   - Show current webhook info")
