import os
import logging
import json
import io
import math
import uuid
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

# Configure logging with more detail
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_IMAGE = 1
WAITING_FOR_TEXT = 2
WAITING_FOR_SIZE = 3
WAITING_FOR_LAYOUT = 4
WAITING_FOR_ANOTHER_BANNER = 5

# Available sizes and layouts
AVAILABLE_SIZES = ["1200x1200", "1200x1500", "1200x628", "1080x1920"]
AVAILABLE_LAYOUTS = ["Yango_photo", "Yango_pro_app", "Yango_app", "Yango_pro_photo", "Yango_pro_Red", "Yango_Red"]
AVAILABLE_LANGUAGES = ["English", "French", "Portuguese", "Arabic", "Spanish", "Azerbaijani", "Urdu"]

# Download app phrases for Yango_pro_app and Yango_app layouts
DOWNLOAD_APP_PHRASES = {
    "English": "Download the app",
    "French": "Téléchargez l'application",
    "Portuguese": "Baixe o aplicativo",
    "Arabic": "حمّل التطبيق",
    "Spanish": "Descarga la aplicación",
    "Azerbaijani": "Tətbiqi yüklə",
    "Urdu": "App download karein"
}

# Load configuration
try:
    with open("config.json", "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
    SIZES = {k: tuple(v) for k, v in CONFIG["sizes"].items()}
    BASE_STYLE = CONFIG["base_style"]
    YANGO_PRO_APP_STYLE = CONFIG.get("yango_pro_app_style", {})
    LAYOUTS = CONFIG["layouts"]
    logger.info("Configuration loaded successfully")
except Exception as e:
    logger.error(f"Failed to load config.json: {e}")
    # Set defaults if config fails to load
    SIZES = {"1200x1200": (1200, 1200)}
    BASE_STYLE = {}
    LAYOUTS = {}

# Telegram bot setup
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
else:
    logger.info(f"Telegram bot token found: {TOKEN[:10]}...")

try:
    application = ApplicationBuilder().token(TOKEN).build()
    logger.info("Telegram application built successfully")
except Exception as e:
    logger.error(f"Failed to build Telegram application: {e}")
    application = None

# FastAPI app setup
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """Set up webhook on application startup"""
    if not TOKEN:
        logger.error("Cannot set up webhook - TELEGRAM_BOT_TOKEN not set")
        return
    
    if not application:
        logger.error("Cannot set up webhook - Telegram application not initialized")
        return
    
    try:
        # Initialize the application first
        logger.info("Initializing Telegram application...")
        await application.initialize()
        logger.info("✅ Telegram application initialized successfully")
        
        # Get the webhook URL
        webhook_url = os.environ.get("WEBHOOK_URL")
        if not webhook_url:
            # Try to construct from Railway URL
            railway_url = os.environ.get("RAILWAY_STATIC_URL")
            if railway_url:
                webhook_url = f"{railway_url}/telegram-webhook"
            else:
                logger.warning("WEBHOOK_URL not set - webhook will not be configured automatically")
                return
        
        logger.info(f"Setting up webhook to: {webhook_url}")
        await application.bot.set_webhook(url=webhook_url)
        logger.info("✅ Webhook set up successfully on startup")
        
        # Log webhook info
        webhook_info = await application.bot.get_webhook_info()
        logger.info(f"Webhook URL: {webhook_info.url}")
        logger.info(f"Pending updates: {webhook_info.pending_update_count}")
        
    except Exception as e:
        logger.error(f"Failed to set up webhook on startup: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on application shutdown"""
    if application:
        try:
            logger.info("Shutting down Telegram application...")
            await application.shutdown()
            logger.info("✅ Telegram application shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down Telegram application: {e}")

def load_font(path, size):
    # First check font file integrity
    if not check_font_file_integrity(path):
        logger.error(f"Font file integrity check failed for {path}")
        return ImageFont.load_default()
    
    try:
        # Try to load with RAQM layout engine first (best for Arabic/RTL text)
        font = ImageFont.truetype(path, size=size, layout_engine=ImageFont.LAYOUT_RAQM)
        logger.info(f"Successfully loaded font {path} with RAQM layout engine, size {size}")
        
        # Verify Arabic font capabilities
        if "Arabic" in path or "arabic" in path.lower():
            verify_arabic_font(font, path)
        
        return font
    except Exception as e:
        logger.warning(f"Failed to load font {path} with RAQM layout engine: {e}")
        try:
            # Fallback to default layout engine
            font = ImageFont.truetype(path, size=size)
            logger.info(f"Successfully loaded font {path} with default layout engine, size {size}")
            
            # Verify Arabic font capabilities
            if "Arabic" in path or "arabic" in path.lower():
                verify_arabic_font(font, path)
            
            return font
        except Exception as e2:
            logger.error(f"Failed to load font {path} with default layout engine: {e2}")
            logger.warning(f"Using default font for {path}")
            return ImageFont.load_default()

def load_arabic_font_with_fallback(size):
    """Load Arabic font with automatic fallback to system fonts if needed"""
    # Priority list of Arabic fonts to try
    arabic_fonts = [
        "Fonts/YangoGroupHeadline-HeavyArabic.ttf",
        "Fonts/YangoGroupText-Medium.ttf",
        "/System/Library/Fonts/Arial.ttf",  # macOS system font with Arabic support
        "/System/Library/Fonts/Helvetica.ttc",  # macOS system font
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux DejaVu
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Linux Liberation
        "C:/Windows/Fonts/arial.ttf",  # Windows Arial
        "C:/Windows/Fonts/calibri.ttf",  # Windows Calibri
    ]
    
    for font_path in arabic_fonts:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, size=size, layout_engine=ImageFont.LAYOUT_RAQM)
                logger.info(f"Successfully loaded Arabic font: {font_path}")
                
                # Test if this font can render Arabic text
                test_text = "كن أنت السائق اكسب الآن"
                if can_render_arabic(font, test_text):
                    logger.info(f"Font {font_path} can render Arabic text properly")
                    return font
                else:
                    logger.warning(f"Font {font_path} cannot render Arabic text properly")
                    continue
        except Exception as e:
            logger.warning(f"Failed to load font {font_path}: {e}")
            continue
    
    # If no Arabic fonts work, try to use the default font
    logger.warning("No suitable Arabic fonts found, using default font")
    return ImageFont.load_default()

def can_render_arabic(font, text):
    """Check if a font can properly render Arabic text"""
    try:
        for char in text:
            if char.strip():  # Skip spaces
                bbox = font.getbbox(char)
                if bbox == (0, 0, 0, 0):  # Invalid character
                    return False
        return True
    except Exception:
        return False

def force_reload_arabic_font(size):
    """Force reload Arabic font with different parameters to fix rendering issues"""
    try:
        # Try different font loading approaches
        font_paths = [
            "Fonts/YangoGroupHeadline-HeavyArabic.ttf",
            "Fonts/YangoGroupText-Medium.ttf"
        ]
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    # Try with different layout engines and parameters
                    font = ImageFont.truetype(font_path, size=size, layout_engine=ImageFont.LAYOUT_RAQM)
                    logger.info(f"Force reloaded Arabic font: {font_path} with RAQM")
                    return font
                except Exception:
                    try:
                        # Try without layout engine
                        font = ImageFont.truetype(font_path, size=size)
                        logger.info(f"Force reloaded Arabic font: {font_path} without layout engine")
                        return font
                    except Exception:
                        continue
        
        # If all else fails, try system fonts
        logger.warning("All Arabic fonts failed, trying system fonts")
        return load_arabic_font_with_fallback(size)
        
    except Exception as e:
        logger.error(f"Error in force_reload_arabic_font: {e}")
        return ImageFont.load_default()

def verify_arabic_font(font, path):
    """Verify that the Arabic font contains required characters"""
    try:
        # Test some common Arabic characters
        test_chars = "كن أنت السائق اكسب الآن"
        missing_chars = []
        
        for char in test_chars:
            if char.strip():  # Skip spaces
                try:
                    # Try to get the character's bounding box
                    bbox = font.getbbox(char)
                    if bbox == (0, 0, 0, 0):  # Invalid character
                        missing_chars.append(char)
                except Exception:
                    missing_chars.append(char)
        
        if missing_chars:
            logger.warning(f"Font {path} is missing Arabic characters: {missing_chars}")
        else:
            logger.info(f"Font {path} contains all tested Arabic characters")
            
    except Exception as e:
        logger.error(f"Error verifying Arabic font {path}: {e}")

def check_font_file_integrity(path):
    """Check if the font file exists and is readable"""
    import os
    try:
        if not os.path.exists(path):
            logger.error(f"Font file does not exist: {path}")
            return False
        
        file_size = os.path.getsize(path)
        logger.info(f"Font file {path} exists, size: {file_size} bytes")
        
        if file_size == 0:
            logger.error(f"Font file is empty: {path}")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Error checking font file {path}: {e}")
        return False

def is_rtl_text(s: str) -> bool:
    return any('\u0590' <= ch <= '\u08FF' for ch in s)  # Hebrew+Arabic ranges

def normalize_text(text: str) -> str:
    logger.info(f"normalize_text called with: '{text}'")
    if any('\u0600' <= ch <= '\u06FF' for ch in text):  # Arabic
        try:
            logger.info(f"Processing Arabic text: '{text}'")
            # Reshape Arabic text for proper rendering
            reshaped_text = arabic_reshaper.reshape(text)
            logger.info(f"After reshaping: '{reshaped_text}'")
            # Apply bidirectional algorithm for RTL text
            processed_text = get_display(reshaped_text)
            logger.info(f"After bidirectional processing: '{processed_text}'")
            return processed_text
        except Exception as e:
            logger.error(f"Error processing Arabic text '{text}': {e}")
            # Return original text if processing fails
            return text
    return text

def text_width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]

def detect_highlights(text):
    """Detect discount patterns and currency amounts in text"""
    import re
    
    logger.info(f"detect_discount called with text: '{text}'")
    
    # Test with a simple case first
    if "30%" in text:
        logger.info(f"Found '30%' in text: '{text}'")
    
    # Common discount patterns
    patterns = [
        r'\b\d+%?\s*(?:скидка|discount|off|%)\b',  # 20% скидка, 50% off, etc.
        r'\b(?:скидка|discount|off)\s*\d+%?\b',    # скидка 20%, discount 50%, etc.
        r'\b\d+\s*руб?\b',                         # 100 руб, 500 рублей, etc.
        r'\b\d+\s*(?:₽|₸|$|€)\b',                  # 100₽, 500₸, $50, etc.
        r'\b(?:бесплатно|free)\b',                 # бесплатно, free
        r'\b(?:подарок|gift)\b',                   # подарок, gift
        r'\b(?:акция|sale)\b',                     # акция, sale
        r'\b\d+%\b',                               # Just percentage like 30%
    ]
    
    # Currency patterns with ISO codes
    currency_patterns = [
        r'\b\d+(?:\.\d+)?\s*(?:AED|ZMW|OMR|GHS|XAF|XOF|CDF|NAD|ZAR|PKR|MZN|BOB|GTQ|PEN|AZN|AOA|ETB|COP|MAD|NPR)\b',  # Amount with ISO currency
        r'\b(?:AED|ZMW|OMR|GHS|XAF|XOF|CDF|NAD|ZAR|PKR|MZN|BOB|GTQ|PEN|AZN|AOA|ETB|COP|MAD|NPR)\s*\d+(?:\.\d+)?\b',  # ISO currency with amount
        r'\b\d+(?:\.\d+)?\s*(?:dirham|kwacha|rial|cedi|franc|shilling|dollar|quetzal|sol|manat|kwanza|birr|peso|dirham|rupee)\b',  # Amount with currency names
        r'\b(?:dirham|kwacha|rial|cedi|franc|shilling|dollar|quetzal|sol|manat|kwanza|birr|peso|dirham|rupee)\s*\d+(?:\.\d+)?\b',  # Currency names with amount
    ]
    
    # Combine all patterns
    all_patterns = patterns + currency_patterns
    
    for pattern in all_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            logger.info(f"Pattern '{pattern}' matched: '{match.group()}' at positions {match.start()}-{match.end()}")
            yield match.start(), match.end(), match.group()

def wrap_with_limits(draw, text, font, max_width, max_lines, ellipsis):
    logger.info(f"wrap_with_limits called with text: '{text}', max_width: {max_width}")
    text = normalize_text(text)
    logger.info(f"After normalization: '{text}'")
    words = text.split()
    logger.info(f"Words after splitting: {words}")
    lines = []
    curr = ""
    for w in words:
        test = (curr + " " + w).strip()
        if text_width(draw, test, font) <= max_width or not curr:
            curr = test
        else:
            lines.append(curr)
            curr = w
        if max_lines and len(lines) == max_lines - 1:
            # last line to be filled, possibly with ellipsis
            pass
    if curr:
        lines.append(curr)
    
    logger.info(f"Final wrapped lines: {lines}")
    return lines

    # Truncate lines to max_lines
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]

    # Apply ellipsis to last line if needed
    if ellipsis and lines:
        last = lines[-1]
        ell = "…"
        while text_width(draw, last, font) > max_width and len(last) > 0:
            last = last[:-1]
        # now ensure ellipsis fits
        while text_width(draw, last + ell, font) > max_width and len(last) > 0:
            last = last[:-1]
        if last != lines[-1]:
            lines[-1] = last + ell
        else:
            # if still overflowing (rare), append ellipsis safely
            if text_width(draw, last, font) > max_width:
                lines[-1] = ell
    return lines

def resolve_style(style_key, layout_key, banner_key, language="English"):
    # Use Yango_pro_app style for specific banner sizes
    if layout_key in ["Yango_pro_app", "Yango_app"] and banner_key in ["1200x1200", "1200x1500", "1200x628"]:
        if style_key in YANGO_PRO_APP_STYLE:
            base = dict(YANGO_PRO_APP_STYLE[style_key])
            # deep copy nested size dict
            base["size"] = dict(base["size"])
            
            # Override font sizes for 1200x628 in Yango_pro_app and Yango_app layouts
            if banner_key == "1200x628":
                if style_key == "headline":
                    base["size"]["1200x628"] = 84
                elif style_key == "subline":
                    base["size"]["1200x628"] = 32
            
            # Use Arabic font fallback for Arabic language
            if language == "Arabic":
                font = load_arabic_font_with_fallback(base["size"][banner_key])
            else:
                font = load_font(base["font"], base["size"][banner_key])
            return base, font
    
    # Use standard base style for all other cases
    base = dict(BASE_STYLE[style_key])
    # deep copy nested size dict
    base["size"] = dict(base["size"])
    # apply styleOverrides if any
    layout = LAYOUTS.get(layout_key, {})
    overrides = layout.get("styleOverrides", {}).get(style_key, {})
    # merge simple keys
    for k, v in overrides.items():
        if k == "size" and isinstance(v, dict):
            base["size"].update(v)
        else:
            base[k] = v
    
    # Use Arabic font fallback for Arabic language
    if language == "Arabic":
        font = load_arabic_font_with_fallback(base["size"][banner_key])
    else:
        font = load_font(base["font"], base["size"][banner_key])
    return base, font

def line_height_px(font, lh_factor):
    ascent, descent = font.getmetrics()
    return int(math.ceil((ascent + descent) * lh_factor))

def stack_height(draw, blocks, banner_key):
    total = 0
    for lines, base_style, font, key in blocks:
        total += line_height_px(font, base_style["line_height"]) * len(lines)
    return total

def get_gap(layout, prev_key, next_key, banner_key):
    default = layout["gap"].get("default", 24)
    for s in layout["gap"].get("special", []):
        a, b, gap, only_size = s
        if prev_key == a and next_key == b and (only_size is None or only_size == banner_key):
            return gap
    return default

def get_arabic_right_margin(banner_key):
    """Get right margin for Arabic text based on banner size"""
    arabic_right_margins = {
        "1200x1200": 64,
        "1200x1500": 64,
        "1200x628": 40,
        "1080x1920": 120
    }
    return arabic_right_margins.get(banner_key, 64)

def draw_text_with_highlights(draw, text, font, x, y, fill_color, discount_color=(227, 255, 116), discount_text_color=(0, 0, 0)):
    """Draw text with discount and currency highlighting"""
    logger.info(f"draw_text_with_highlights called with text: '{text}'")
    
    if not text.strip():
        return y
    
    # Convert RGBA colors to RGB for PIL compatibility
    if len(fill_color) == 4:
        fill_color = fill_color[:3]
    if len(discount_color) == 4:
        discount_color = discount_color[:3]
    if len(discount_text_color) == 4:
        discount_text_color = discount_text_color[:3]
    
    # Detect discounts and currency amounts in the text
    highlights = list(detect_highlights(text))
    
    if highlights:
        logger.info(f"Found highlights in text '{text}': {highlights}")
    else:
        logger.info(f"No highlights found in text: '{text}'")
    
    if not highlights:
        # No highlights found, draw normal text
        logger.info(f"Drawing normal text: '{text}' at position ({x}, {y}) with font {font.path if hasattr(font, 'path') else 'unknown'}")
        try:
            draw.text((x, y), text, font=font, fill=fill_color)
            bbox = font.getbbox(text)
            logger.info(f"Text bounding box: {bbox}")
            return y + bbox[3]
        except Exception as e:
            logger.error(f"Error drawing text '{text}': {e}")
            # Try to draw character by character to identify problematic characters
            current_x = x
            for i, char in enumerate(text):
                try:
                    char_bbox = font.getbbox(char)
                    logger.info(f"Character '{char}' (U+{ord(char):04X}) bbox: {char_bbox}")
                    if char_bbox != (0, 0, 0, 0):
                        draw.text((current_x, y), char, font=font, fill=fill_color)
                        current_x += char_bbox[2]
                    else:
                        logger.warning(f"Character '{char}' (U+{ord(char):04X}) has invalid bbox")
                        # Try to force reload the font for this character
                        if any('\u0600' <= ch <= '\u06FF' for ch in char):  # Arabic character
                            logger.info(f"Attempting to force reload Arabic font for character '{char}'")
                            try:
                                new_font = force_reload_arabic_font(font.size)
                                new_bbox = new_font.getbbox(char)
                                if new_bbox != (0, 0, 0, 0):
                                    draw.text((current_x, y), char, font=new_font, fill=fill_color)
                                    current_x += new_bbox[2]
                                    logger.info(f"Successfully rendered character '{char}' with reloaded font")
                                else:
                                    logger.error(f"Character '{char}' still has invalid bbox after font reload")
                            except Exception as reload_e:
                                logger.error(f"Failed to reload font for character '{char}': {reload_e}")
                except Exception as char_e:
                    logger.error(f"Error drawing character '{char}' (U+{ord(char):04X}): {char_e}")
            return y + font.getbbox("A")[3]  # Use fallback height
    

    
    # Draw text with highlighting
    current_x = x
    last_end = 0
    
    for start, end, highlight_text in highlights:
        # Draw text before highlight
        if start > last_end:
            before_text = text[last_end:start]
            draw.text((current_x, y), before_text, font=font, fill=fill_color)
            current_x += text_width(draw, before_text, font)
        
        # Calculate highlight background dimensions
        highlight_width = text_width(draw, highlight_text, font)
        highlight_height = font.getbbox(highlight_text)[3]
        
        # Draw highlight background (rounded rectangle)
        bg_x = current_x
        bg_y = y
        bg_width = highlight_width
        bg_height = highlight_height
        
        # Create rounded rectangle background with correct color and corner radius
        try:
            # Use the correct highlight color: #E3FF74 (227, 255, 116)
            correct_highlight_color = (227, 255, 116)
            
            # Create a temporary image for the rounded rectangle
            temp_img = Image.new('RGBA', (bg_width + 16, bg_height + 16), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            
            # Draw rounded rectangle with larger radius (16px for better rounding)
            temp_draw.rounded_rectangle(
                [0, 0, bg_width + 15, bg_height + 15],
                radius=16,
                fill=correct_highlight_color
            )
            
            # Paste the background onto the main image
            main_image = draw._image if hasattr(draw, '_image') else draw.im
            main_image.paste(temp_img, (bg_x - 8, bg_y - 8), temp_img)
            logger.info(f"Successfully drew rounded rectangle for highlight: '{highlight_text}'")
        except Exception as e:
            logger.error(f"Failed to draw rounded rectangle: {e}, falling back to simple rectangle")
            # Fallback to simple rectangle with correct color
            draw.rectangle([bg_x - 8, bg_y - 8, bg_x + bg_width + 8, bg_y + bg_height + 8], fill=(227, 255, 116))
        
        # Draw highlight text in black
        draw.text((current_x, y), highlight_text, font=font, fill=discount_text_color)
        current_x += highlight_width
        last_end = end
    
    # Draw remaining text after last highlight
    if last_end < len(text):
        remaining_text = text[last_end:]
        draw.text((current_x, y), remaining_text, font=font, fill=fill_color)
    
    return y + font.getbbox(text)[3]

def process_background_image(image, banner_key, language="English"):
    """Process 2890x2890 background image with specific scale and offset for each banner size"""
    # Define scale and offset values for each banner size
    background_configs = {
        "1200x1200": {"scale": 0.7, "offset_x": 0, "offset_y": -240},
        "1200x1500": {"scale": 0.8, "offset_x": 0, "offset_y": -341},
        "1200x628": {"scale": 0.6, "offset_x": 267, "offset_y": 0},
        "1080x1920": {"scale": 0.8, "offset_x": 0, "offset_y": -196}
    }
    
    # Arabic-specific overrides
    if language == "Arabic" and banner_key == "1200x628":
        background_configs["1200x628"] = {"scale": 0.6, "offset_x": -267, "offset_y": 0}
    
    if banner_key not in background_configs:
        logger.error(f"Unknown banner key: {banner_key}")
        return image
    
    config = background_configs[banner_key]
    scale = config["scale"]
    offset_x = config["offset_x"]
    offset_y = config["offset_y"]
    
    # Get target dimensions from SIZES
    target_width, target_height = SIZES.get(banner_key, (1200, 1200))
    
    # Calculate scaled dimensions
    scaled_width = int(2890 * scale)
    scaled_height = int(2890 * scale)
    
    # Resize the image to the scaled size
    scaled_image = image.resize((scaled_width, scaled_height), Image.LANCZOS)
    
    # Create a new image with target dimensions
    result = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    
    # Calculate position to paste the scaled image
    paste_x = (target_width - scaled_width) // 2 + offset_x
    paste_y = (target_height - scaled_height) // 2 + offset_y
    
    # Paste the scaled image onto the result
    result.paste(scaled_image, (paste_x, paste_y))
    
    return result

def crop_image_to_size(image, target_width, target_height):
    """Crop image to target size while maintaining aspect ratio"""
    img_width, img_height = image.size
    
    # Calculate aspect ratios
    target_ratio = target_width / target_height
    img_ratio = img_width / img_height
    
    if img_ratio > target_ratio:
        # Image is wider than target, crop width
        new_width = int(img_height * target_ratio)
        new_height = img_height
        left = (img_width - new_width) // 2
        top = 0
        right = left + new_width
        bottom = new_height
    else:
        # Image is taller than target, crop height
        new_width = img_width
        new_height = int(img_width / target_ratio)
        left = 0
        top = (img_height - new_height) // 2
        right = new_width
        bottom = top + new_height
    
    # Crop the image
    cropped = image.crop((left, top, right, bottom))
    
    # Resize to exact target size
    return cropped.resize((target_width, target_height), Image.LANCZOS)

def compose(bg, headline, subline, disclaimer, banner_key, layout_key, apply_overlay=True, language="English"):
    w, h = bg.size
    
    # Apply overlay first (before text)
    if apply_overlay:
        # Use AR folder for Arabic language
        if language == "Arabic":
            ov_path = os.path.join("Overlay", "AR", layout_key, f"{banner_key}.png")
        else:
            ov_path = os.path.join("Overlay", layout_key, f"{banner_key}.png")
        if os.path.exists(ov_path):
            ov = Image.open(ov_path).convert("RGBA").resize((w, h))
            bg = Image.alpha_composite(bg.convert("RGBA"), ov)
            logger.info(f"Applied overlay: {ov_path}")
        else:
            logger.warning(f"Overlay not found: {ov_path}")
    
    # Now draw text on top of the overlay
    draw = ImageDraw.Draw(bg)
    
        # Special handling for 1200x628 size - bypass layout system entirely
    if banner_key == "1200x628":
        # 1200x628: anchor all text blocks to the top, 40px margin from top, 28px spacing between blocks
        y = 40
        block_x = 40
        block_width = 540
        
        # Process headline
        if headline:
            logger.info(f"Processing headline: '{headline}' with language: '{language}'")
            st, font = resolve_style("headline", layout_key, banner_key, language)
            logger.info(f"Resolved style for headline: {st}")
            logger.info(f"Font loaded: {font.path if hasattr(font, 'path') else 'unknown'}")
            
            lines = wrap_with_limits(draw, headline, font, block_width, st.get("max_lines", 0), st.get("ellipsis", False))
            logger.info(f"1200x628 headline wrapped into lines: {lines}")
            logger.info(f"Original headline: '{headline}'")
            line_spacing = int(font.size * 0.15)
            
            for idx, line in enumerate(lines):
                logger.info(f"Processing line {idx}: '{line}'")
                lw, lh = draw.textbbox((0, 0), line, font=font)[2:]
                if language == "Arabic":
                    # Right-align with Arabic right margin for 1200x628
                    right_margin = get_arabic_right_margin(banner_key)
                    draw_x = w - right_margin - lw
                elif layout_key in ["Yango_pro_app", "Yango_app"]:
                    # Use left alignment with 48px margin for Yango_pro_app and Yango_app
                    draw_x = 48
                else:
                    # Use center alignment for other layouts
                    draw_x = block_x + (block_width - lw) // 2
                draw_text_with_highlights(draw, line, font, draw_x, y, (255, 255, 255, 255))
                if idx < len(lines) - 1:
                    y += lh + line_spacing
                else:
                    y += lh
            y += 28
        
        # Process subline (subtitle)
        if subline:
            st, font = resolve_style("subline", layout_key, banner_key, language)
            subtitle_block_width = 460
            subtitle_block_x = 80
            lines = wrap_with_limits(draw, subline, font, subtitle_block_width, st.get("max_lines", 0), st.get("ellipsis", False))
            line_spacing = int(font.size * 0.2)
            for idx, line in enumerate(lines):
                lw, lh = draw.textbbox((0, 0), line, font=font)[2:]
                if language == "Arabic":
                    # Right-align with Arabic right margin for 1200x628
                    right_margin = get_arabic_right_margin(banner_key)
                    draw_x = w - right_margin - lw
                elif layout_key in ["Yango_pro_app", "Yango_app"]:
                    # Use left alignment with 48px margin for Yango_pro_app and Yango_app
                    draw_x = 48
                else:
                    # Use center alignment for other layouts
                    draw_x = subtitle_block_x + (subtitle_block_width - lw) // 2
                draw_text_with_highlights(draw, line, font, draw_x, y, (255, 255, 255, 255))
                if idx < len(lines) - 1:
                    y += lh + line_spacing
                else:
                    y += lh
            y += 28
        
        # Process disclaimer separately
        if disclaimer:
            st, font = resolve_style("disclaimer", layout_key, banner_key, language)
            if layout_key in ["Yango_pro_app", "Yango_app"]:
                # Yango_pro_app and Yango_app specific disclaimer positioning for 1200x628
                lines = wrap_with_limits(draw, disclaimer, font, 750, st.get("max_lines", 0), st.get("ellipsis", False))
                disclaimer_y = 540 + 5  # Move 5px down
                for line in lines:
                    lw, lh = draw.textbbox((0, 0), line, font=font)[2:]
                    if language == "Arabic":
                        # Right-align with Arabic right margin and apply Arabic-specific adjustments for 1200x628
                        right_margin = get_arabic_right_margin(banner_key)
                        # Move 140px to the left for Arabic
                        draw_x = w - right_margin - lw - 140
                    else:
                        draw_x = 200 - 32  # Move 32px left from original position
                    draw_text_with_highlights(draw, line, font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(font, st["line_height"])
            else:
                # Standard disclaimer positioning
                lines = wrap_with_limits(draw, disclaimer, font, block_width, st.get("max_lines", 0), st.get("ellipsis", False))
                # Calculate total height of disclaimer block
                total_height = sum([draw.textbbox((0, 0), line, font=font)[3] for line in lines]) + (len(lines)-1)*10
                # Use 50px margin for 1080x1920, 40px for others
                bottom_margin = 50 if banner_key == "1080x1920" else 40
                disclaimer_y = h - bottom_margin - total_height
                for line in lines:
                    lw, lh = draw.textbbox((0, 0), line, font=font)[2:]
                    draw_x = w - 40 - lw  # Right align with 40px margin
                    draw_text_with_highlights(draw, line, font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(font, st["line_height"])
        
        # Add download app phrase for Yango_pro_app and Yango_app layouts
        if layout_key in ["Yango_pro_app", "Yango_app"]:
            download_phrase = DOWNLOAD_APP_PHRASES.get(language, DOWNLOAD_APP_PHRASES["English"])
            
            # Custom font sizes for download app phrase
            download_font_sizes = {
                "1200x1200": 48,
                "1200x1500": 48,
                "1200x628": 40,
                "1080x1920": 56
            }
            download_font_size = download_font_sizes.get(banner_key, 64)
            # Use Arabic-aware font loading for download phrase
            if language == "Arabic":
                download_font = load_arabic_font_with_fallback(download_font_size)
            else:
                download_font = load_font("Fonts/YangoGroupHeadline-HeavyArabic.ttf", download_font_size)
            
            # 1200x628 specific positioning
            download_x = 200 - 32  # Move 32px left from original position
            download_y = h - 40 - download_font.getbbox(download_phrase)[3] - 70 + 18  # Move 18px down from original position
            
            # Draw the download phrase with appropriate text block width
            if banner_key == "1200x628":
                # No width limitation for 1200x628
                max_width = w - download_x - 40  # Use remaining width (40px right margin)
                lines = wrap_with_limits(draw, download_phrase, download_font, max_width, 2, False)
            else:
                # Use 315px width for other sizes
                lines = wrap_with_limits(draw, download_phrase, download_font, 315, 2, False)
            for line in lines:
                lw, lh = draw.textbbox((0, 0), line, font=download_font)[2:]
                if language == "Arabic":
                    # Right-align with Arabic right margin and apply Arabic-specific adjustments for 1200x628
                    right_margin = get_arabic_right_margin(banner_key)
                    # Move 140px to the left for Arabic
                    draw_x = w - right_margin - lw - 140
                else:
                    draw_x = download_x  # Left-aligned
                draw_text_with_highlights(draw, line, download_font, draw_x, download_y, (255, 255, 255, 255))
                download_y += lh + 5
    else:
        # Standard positioning for other sizes - use layout system
        layout = LAYOUTS.get(layout_key, LAYOUTS["Yango_photo"])
        pad = layout["padding"]
        max_w = w - pad["left"] - pad["right"]

        content_map = {"headline": headline or "", "subline": subline or "", "disclaimer": disclaimer or ""}

        blocks = []
        for key in layout["text_stack"]:
            raw = content_map.get(key, "")
            if not raw.strip():
                continue
            st, font = resolve_style(key, layout_key, banner_key, language)
            lines = wrap_with_limits(draw, raw, font, max_w, st.get("max_lines", 0), st.get("ellipsis", False))
            blocks.append((lines, st, font, key))

        # measure
        total_h = 0
        gaps = []
        for i, (lines, st, font, key) in enumerate(blocks):
            total_h += line_height_px(font, st["line_height"]) * len(lines)
            if i < len(blocks) - 1:
                nxt_key = blocks[i + 1][3]
                gaps.append(get_gap(layout, key, nxt_key, banner_key))
        total_h += sum(gaps) if gaps else 0

        # anchor position
        anchor = layout["anchor"]
        if anchor == "bottom_left":
            x = pad["left"]
            y = h - pad["bottom"] - total_h
        elif anchor == "bottom_center":
            x = (w - max_w) // 2  # Center horizontally
            # Special handling for different banner sizes
            if banner_key == "1080x1920":
                y = h - 250 - total_h  # 50px default + 200px extra = 250px
            elif banner_key == "1200x1500" and layout_key in ["Yango_pro_app", "Yango_app"]:
                y = h - pad["bottom"] - total_h - 200  # Move up by 200px
            elif banner_key == "1200x1200" and layout_key in ["Yango_pro_app", "Yango_app"]:
                y = h - pad["bottom"] - total_h - 170  # Move up by 170px
            elif banner_key == "1200x1200" and layout_key in ["Yango_Red", "Yango_pro_Red"]:
                y = h - pad["bottom"] - total_h - 40  # Move up by 40px
            elif banner_key == "1200x1500" and layout_key in ["Yango_Red", "Yango_pro_Red"]:
                y = h - pad["bottom"] - total_h - 240  # Move up by 240px
            elif banner_key == "1080x1920" and layout_key in ["Yango_Red", "Yango_pro_Red"]:
                y = h - pad["bottom"] - total_h + 170  # Move down by 170px
            else:
                y = h - pad["bottom"] - total_h
            

        elif anchor == "center":
            x = pad["left"]
            y = (h - total_h) // 2
        elif anchor == "top_left":
            x = pad["left"]
            y = pad["top"]
        elif anchor == "top_right":
            x = w - pad["right"] - max_w
            y = pad["top"]
        elif anchor == "bottom_right":
            x = w - pad["right"] - max_w
            y = h - pad["bottom"] - total_h
        else:
            x = pad["left"]
            y = h - pad["bottom"] - total_h

        if banner_key == "1080x1920":
            # Special handling for 1080x1920 - disclaimer positioned separately
            main_blocks = [block for block in blocks if block[3] != "disclaimer"]
            disclaimer_blocks = [block for block in blocks if block[3] == "disclaimer"]
            
            # Move text block up by 30px for Yango_pro_app and Yango_app, otherwise 50px lower
            if layout_key in ["Yango_pro_app", "Yango_app"]:
                y -= 30
            elif layout_key in ["Yango_Red", "Yango_pro_Red"]:
                y += 200  # Move down by 170px for Yango_Red layouts
            else:
                y += 50
            
            # Process main text blocks first
            for i, (lines, st, font, key) in enumerate(main_blocks):
                lh = line_height_px(font, st["line_height"])
                align = st.get("align", "left")
                # Force right alignment for Arabic language
                if language == "Arabic":
                    align = "right"
                # auto right align if RTL text and layout isn't explicitly left
                join_text = " ".join(lines)
                if is_rtl_text(join_text) and anchor in ("top_right", "bottom_right"):
                    align = "right"
                for line in lines:
                    # compute x by align
                    if align == "center":
                        lw = text_width(draw, line, font)
                        dx = (max_w - lw) // 2
                        draw_text_with_highlights(draw, line, font, x + dx, y, (255, 255, 255, 255))
                    elif align == "right":
                        lw = text_width(draw, line, font)
                        if language == "Arabic":
                            # Use Arabic right margin
                            right_margin = get_arabic_right_margin(banner_key)
                            draw_text_with_highlights(draw, line, font, w - right_margin - lw, y, (255, 255, 255, 255))
                        else:
                            draw_text_with_highlights(draw, line, font, x + max_w - lw, y, (255, 255, 255, 255))
                    else:
                        draw_text_with_highlights(draw, line, font, x, y, (255, 255, 255, 255))
                    y += lh
                if i < len(gaps):
                    y += gaps[i]
            
            # Handle disclaimer separately - positioned at bottom with 50px margin
            if disclaimer_blocks:
                disclaimer_lines, disclaimer_st, disclaimer_font, _ = disclaimer_blocks[0]
                disclaimer_y = h - 50  # 50px from bottom
                
                for line in disclaimer_lines:
                    lw = text_width(draw, line, disclaimer_font)
                    # Center align with 50px margin from bottom
                    draw_x = (w - lw) // 2
                    draw_text_with_highlights(draw, line, disclaimer_font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(disclaimer_font, disclaimer_st["line_height"])
        else:
            # Special handling for Yango_pro_app and Yango_app main text blocks in specific sizes
            if layout_key in ["Yango_pro_app", "Yango_app"] and banner_key in ["1200x1200", "1200x1500", "1200x628"]:
                # Filter out disclaimer for 1200x1200 and 1200x1500 (keep custom disclaimer positioning)
                if banner_key in ["1200x1200", "1200x1500"]:
                    filtered_blocks = [block for block in blocks if block[3] != "disclaimer"]
                else:
                    filtered_blocks = blocks
                
                # Process the filtered blocks for Yango_pro_app and Yango_app
                for i, (lines, st, font, key) in enumerate(filtered_blocks):
                    lh = line_height_px(font, st["line_height"])
                    
                    for line in lines:
                        if language == "Arabic":
                            # Right-align with Arabic right margin
                            lw = text_width(draw, line, font)
                            right_margin = get_arabic_right_margin(banner_key)
                            draw_text_with_highlights(draw, line, font, w - right_margin - lw, y, (255, 255, 255, 255))
                        else:
                            # Get left margin from style configuration
                            left_margin = st.get("left_margin", {}).get(banner_key, 150)
                            # Left-align with margin from style configuration
                            draw_text_with_highlights(draw, line, font, left_margin, y, (255, 255, 255, 255))
                        y += lh
                    if i < len(gaps):
                        y += gaps[i]
            # Special handling for Yango_Red and Yango_pro_Red layouts
            elif layout_key in ["Yango_Red", "Yango_pro_Red"] and banner_key in ["1200x1200", "1200x1500"]:
                # Filter out disclaimer for custom disclaimer positioning
                filtered_blocks = [block for block in blocks if block[3] != "disclaimer"]
                
                for i, (lines, st, font, key) in enumerate(filtered_blocks):
                    lh = line_height_px(font, st["line_height"])
                    
                    for line in lines:
                        if language == "Arabic":
                            # Right-align text for Arabic in Yango_Red layouts
                            lw = text_width(draw, line, font)
                            right_margin = get_arabic_right_margin(banner_key)
                            draw_x = w - right_margin - lw
                        else:
                            # Center-align text for non-Arabic
                            lw = text_width(draw, line, font)
                            draw_x = (w - lw) // 2  # Center align
                        draw_text_with_highlights(draw, line, font, draw_x, y, (255, 255, 255, 255))
                        y += lh
                    if i < len(gaps):
                        y += gaps[i]
            else:
                # Standard positioning for other sizes
                for i, (lines, st, font, key) in enumerate(blocks):
                    lh = line_height_px(font, st["line_height"])
                    align = st.get("align", "left")
                    # Force right alignment for Arabic language
                    if language == "Arabic":
                        align = "right"
                    # auto right align if RTL text and layout isn't explicitly left
                    join_text = " ".join(lines)
                    if is_rtl_text(join_text) and anchor in ("top_right", "bottom_right"):
                        align = "right"
                    
                    for line in lines:
                        # compute x by align
                        if align == "center":
                            lw = text_width(draw, line, font)
                            dx = (max_w - lw) // 2
                            draw_text_with_highlights(draw, line, font, x + dx, y, (255, 255, 255, 255))
                        elif align == "right":
                            lw = text_width(draw, line, font)
                            if language == "Arabic":
                                # Use Arabic right margin
                                right_margin = get_arabic_right_margin(banner_key)
                                draw_text_with_highlights(draw, line, font, w - right_margin - lw, y, (255, 255, 255, 255))
                            else:
                                draw_text_with_highlights(draw, line, font, x + max_w - lw, y, (255, 255, 255, 255))
                        else:
                            draw_text_with_highlights(draw, line, font, x, y, (255, 255, 255, 255))
                        y += lh
                    if i < len(gaps):
                        y += gaps[i]
        
        # Add download app phrase for Yango_pro_app and Yango_app layouts in standard positioning
        if layout_key in ["Yango_pro_app", "Yango_app"]:
            download_phrase = DOWNLOAD_APP_PHRASES.get(language, DOWNLOAD_APP_PHRASES["English"])
            
            # Custom font sizes for download app phrase
            download_font_sizes = {
                "1200x1200": 64,
                "1200x1500": 64,
                "1200x628": 48,
                "1080x1920": 64
            }
            download_font_size = download_font_sizes.get(banner_key, 64)
            # Use Arabic-aware font loading for download phrase
            if language == "Arabic":
                download_font = load_arabic_font_with_fallback(download_font_size)
            else:
                download_font = load_font("Fonts/YangoGroupHeadline-HeavyArabic.ttf", download_font_size)
            
            # Position based on banner size
            if banner_key == "1080x1920":
                # Move up by 240px and right by 260px + 220px - 100px = 380px
                download_x = pad["left"] + 380
                download_y = h - pad["bottom"] - download_font.getbbox(download_phrase)[3] - 240
            elif banner_key == "1200x1500":
                # Move 20px left and 20px down from original position
                download_x = pad["left"] + 120 - 20
                download_y = h - pad["bottom"] - download_font.getbbox(download_phrase)[3] - 110 + 20
            elif banner_key == "1200x1200":
                # Move 20px left and 20px down from original position
                download_x = pad["left"] + 120 - 20
                download_y = h - pad["bottom"] - download_font.getbbox(download_phrase)[3] - 110 + 20
            else:
                # Default positioning
                download_x = pad["left"]
                download_y = h - pad["bottom"] - download_font.getbbox(download_phrase)[3]
            
            # Draw the download phrase with appropriate text block width
            if language == "Arabic" and banner_key == "1080x1920":
                # Use 260px width for Arabic in 1080x1920
                lines = wrap_with_limits(draw, download_phrase, download_font, 260, 2, False)
            elif banner_key in ["1200x1200", "1200x1500"]:
                # No width limitation for 1200x1200 and 1200x1500
                max_width = w - download_x - pad["right"]  # Use remaining width
                lines = wrap_with_limits(draw, download_phrase, download_font, max_width, 2, False)
            else:
                # Use 315px width for other sizes
                lines = wrap_with_limits(draw, download_phrase, download_font, 315, 2, False)
            
            for line in lines:
                lw, lh = draw.textbbox((0, 0), line, font=download_font)[2:]
                if language == "Arabic":
                    # Right-align with Arabic right margin and apply Arabic-specific adjustments
                    right_margin = get_arabic_right_margin(banner_key)
                    if banner_key in ["1200x1200", "1200x1500"]:
                        # Move 180px to the left for Arabic
                        draw_x = w - right_margin - lw - 180
                    elif banner_key == "1200x628":
                        # Move 140px to the left for Arabic
                        draw_x = w - right_margin - lw - 140
                    elif banner_key == "1080x1920":
                        # Move 180px to the left for Arabic
                        draw_x = w - right_margin - lw - 180
                    else:
                        draw_x = w - right_margin - lw
                else:
                    draw_x = download_x  # Left-aligned
                draw_text_with_highlights(draw, line, download_font, draw_x, download_y, (255, 255, 255, 255))
                download_y += lh + 5
        
        # Add disclaimer positioning for Yango_pro_app and Yango_app layouts in standard positioning
        if layout_key in ["Yango_pro_app", "Yango_app"] and disclaimer:
            st, font = resolve_style("disclaimer", layout_key, banner_key, language)
            
            if banner_key == "1200x1500":
                # Disclaimer positioning for 1200x1500
                disclaimer_width = w - 274 - 274  # Total width minus left and right offsets
                lines = wrap_with_limits(draw, disclaimer, font, disclaimer_width, st.get("max_lines", 0), st.get("ellipsis", False))
                disclaimer_y = 1350 + 20  # Move 20px down
                for line in lines:
                    lw, lh = draw.textbbox((0, 0), line, font=font)[2:]
                    if language == "Arabic":
                        # Right-align with Arabic right margin and apply Arabic-specific adjustments
                        right_margin = get_arabic_right_margin(banner_key)
                        # Move 180px to the left for Arabic
                        draw_x = w - right_margin - lw - 180
                    else:
                        draw_x = 274 - 20  # Move 20px left from original position
                    draw_text_with_highlights(draw, line, font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(font, st["line_height"])
            elif banner_key == "1200x1200":
                # Disclaimer positioning for 1200x1200
                disclaimer_width = w - 270 - 200  # Total width minus left and right offsets
                lines = wrap_with_limits(draw, disclaimer, font, disclaimer_width, st.get("max_lines", 0), st.get("ellipsis", False))
                disclaimer_y = 1060 + 20  # Move 20px down
                for line in lines:
                    lw, lh = draw.textbbox((0, 0), line, font=font)[2:]
                    if language == "Arabic":
                        # Right-align with Arabic right margin and apply Arabic-specific adjustments
                        right_margin = get_arabic_right_margin(banner_key)
                        # Move 180px to the left for Arabic
                        draw_x = w - right_margin - lw - 180
                    else:
                        draw_x = 250  # Move 20px left from original position
                    draw_text_with_highlights(draw, line, font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(font, st["line_height"])
        
        # Add disclaimer positioning for Yango_Red and Yango_pro_Red layouts
        if layout_key in ["Yango_Red", "Yango_pro_Red"] and disclaimer:
            st, font = resolve_style("disclaimer", layout_key, banner_key, language)
            
            if banner_key == "1200x1200":
                # Disclaimer aligned right, 40px margin from right edge and 40px from bottom
                # Disclaimer block width: 700px
                lines = wrap_with_limits(draw, disclaimer, font, 700, st.get("max_lines", 0), st.get("ellipsis", False))
                disclaimer_y = h - 40  # 40px from bottom
                for line in lines:
                    lw = text_width(draw, line, font)
                    draw_x = w - 40 - lw  # Right align with 40px margin
                    draw_text_with_highlights(draw, line, font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(font, st["line_height"])
            elif banner_key == "1200x1500":
                # Disclaimer aligned right, 80px margin from right edge and 80px from bottom
                # Disclaimer block width: 700px
                lines = wrap_with_limits(draw, disclaimer, font, 700, st.get("max_lines", 0), st.get("ellipsis", False))
                disclaimer_y = h - 80  # 80px from bottom
                for line in lines:
                    lw = text_width(draw, line, font)
                    draw_x = w - 80 - lw  # Right align with 80px margin
                    draw_text_with_highlights(draw, line, font, draw_x, disclaimer_y, (255, 255, 255, 255))
                    disclaimer_y += line_height_px(font, st["line_height"])

    return bg

# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    logger.info(f"Received /start command from user {update.effective_user.id}")
    try:
        welcome_message = """Привет! 🎨 Я помогу создать красивый баннер с текстом.

Давайте создадим баннер пошагово! 

📸 Сначала отправьте мне изображение, которое будет основой для баннера."""
        
        await update.message.reply_text(welcome_message)
        logger.info("Start command response sent successfully")
        return WAITING_FOR_IMAGE
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Произошла ошибка при обработке команды.")
        return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    logger.info(f"Received /help command from user {update.effective_user.id}")
    try:
        help_text = """📋 Справка по использованию бота:

🎨 Создание баннера:
1. Отправьте изображение
2. Введите заголовок
3. Введите подзаголовок
4. Введите дисклеймер
5. Выберите язык
6. Выберите размер
7. Выберите макет

🖼️ Доступные размеры:
• 1200x1200 (квадрат)
• 1200x1500 (вертикальный)
• 1200x628 (горизонтальный)
• 1080x1920 (сторис)

🌍 Поддерживаемые языки:
• English
• French
• Portuguese
• Arabic
• Spanish
• Azerbaijani
• Urdu

🎨 Доступные макеты:
• Yango_photo
• Yango_pro_app (включает фразу "Download the app")
• Yango_app (включает фразу "Download the app")
• Yango_pro_photo
• Yango_pro_Red
• Yango_Red

Команды:
/start - Начать создание баннера
/help - Показать эту справку
/cancel - Отменить текущий процесс"""
        await update.message.reply_text(help_text)
        logger.info("Help command response sent successfully")
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Произошла ошибка при обработке команды.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    await update.message.reply_text(
        "❌ Процесс создания баннера отменен. Используйте /start для начала нового процесса.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle image upload (both photos and document files)"""
    try:
        # Check if it's a photo or document
        if update.message.photo:
            # Handle photo
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
        elif update.message.document:
            # Handle document file
            document = update.message.document
            # Check if it's an image file
            if not document.mime_type or not document.mime_type.startswith('image/'):
                await update.message.reply_text("❌ Пожалуйста, отправьте изображение (файл должен быть изображением).")
                return WAITING_FOR_IMAGE
            file = await context.bot.get_file(document.file_id)
        else:
            await update.message.reply_text("❌ Пожалуйста, отправьте изображение.")
            return WAITING_FOR_IMAGE
        
        # Download the image
        image_data = await file.download_as_bytearray()
        
        # Check and resize image to 2890x2890 if needed
        bg = Image.open(io.BytesIO(image_data))
        img_width, img_height = bg.size
        
        if img_width != 2890 or img_height != 2890:
            logger.info(f"Resizing image from {img_width}x{img_height} to 2890x2890")
            bg = bg.resize((2890, 2890), Image.LANCZOS)
            # Convert back to bytes for storage
            img_byte_arr = io.BytesIO()
            bg.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()
            context.user_data['image_data'] = img_byte_arr
        else:
            context.user_data['image_data'] = image_data
        
        await update.message.reply_text(
            "✅ Изображение получено! Теперь введите текст в следующем формате:\n\n"
            "Заголовок\n"
            "Подзаголовок\n"
            "Дисклеймер\n"
            "Язык\n\n"
            "Каждый элемент должен быть на новой строке.\n"
            "Доступные языки: Русский, English, العربية, Türkçe, Қазақша"
        )
        return WAITING_FOR_TEXT
    except Exception as e:
        logger.error(f"Error handling image: {e}")
        await update.message.reply_text("❌ Ошибка при обработке изображения. Попробуйте еще раз.")
        return ConversationHandler.END

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input (headline, subheadline, disclaimer, language)"""
    try:
        text_lines = update.message.text.strip().split('\n')
        
        if len(text_lines) < 4:
            await update.message.reply_text(
                "❌ Пожалуйста, введите все четыре элемента:\n"
                "1. Заголовок\n"
                "2. Подзаголовок\n"
                "3. Дисклеймер\n"
                "4. Язык\n\n"
                "Каждый элемент должен быть на новой строке.\n"
                "Доступные языки: Русский, English, العربية, Türkçe, Қазақша"
            )
            return WAITING_FOR_TEXT
        
        # Extract the four text elements
        headline = text_lines[0].strip()
        subheadline = text_lines[1].strip()
        disclaimer = text_lines[2].strip()
        language = text_lines[3].strip()
        
        # Validate language
        if language not in AVAILABLE_LANGUAGES:
            await update.message.reply_text(
                "❌ Неверный язык. Пожалуйста, используйте один из доступных языков:\n"
                "Русский, English, العربية, Türkçe, Қазақша"
            )
            return WAITING_FOR_TEXT
        
        # Store in context
        context.user_data['headline'] = headline
        context.user_data['subheadline'] = subheadline
        context.user_data['disclaimer'] = disclaimer
        context.user_data['language'] = language
        
        # Create size keyboard
        size_keyboard = [[size] for size in AVAILABLE_SIZES]
        reply_markup = ReplyKeyboardMarkup(size_keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ Текст и язык ({language}) сохранены! Теперь выберите размер баннера:",
            reply_markup=reply_markup
        )
        return WAITING_FOR_SIZE
        
    except Exception as e:
        logger.error(f"Error handling text input: {e}")
        await update.message.reply_text("❌ Ошибка при обработке текста. Попробуйте еще раз.")
        return ConversationHandler.END



async def handle_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle size selection"""
    size = update.message.text
    if size not in AVAILABLE_SIZES:
        await update.message.reply_text("❌ Неверный размер. Выберите из предложенных вариантов.")
        return WAITING_FOR_SIZE
    
    context.user_data['size'] = size
    
    # Create layout keyboard
    layout_keyboard = [[layout] for layout in AVAILABLE_LAYOUTS]
    reply_markup = ReplyKeyboardMarkup(layout_keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        f"✅ Размер {size} выбран! Теперь выберите макет:",
        reply_markup=reply_markup
    )
    return WAITING_FOR_LAYOUT

async def handle_layout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle layout selection and generate banner"""
    layout = update.message.text
    if layout not in AVAILABLE_LAYOUTS:
        await update.message.reply_text("❌ Неверный макет. Выберите из предложенных вариантов.")
        return WAITING_FOR_LAYOUT
    
    context.user_data['layout'] = layout
    
    await update.message.reply_text(
        "🎨 Создаю баннер... Пожалуйста, подождите.",
        reply_markup=ReplyKeyboardRemove()
    )
    
    try:
        # Generate the banner
        image_data = context.user_data['image_data']
        headline = context.user_data.get('headline', '')
        subheadline = context.user_data.get('subheadline', '')
        disclaimer = context.user_data.get('disclaimer', '')
        language = context.user_data.get('language', 'Русский')
        size = context.user_data['size']
        
        # Open and process the image
        bg = Image.open(io.BytesIO(image_data)).convert("RGBA")
        bg = process_background_image(bg, size, language)
        
        # Apply overlay using the selected layout
        out = compose(bg, headline, subheadline, disclaimer, size, layout, apply_overlay=True, language=language)
        
        # Save and send the result
        out_path = f"result_{uuid.uuid4().hex}.png"
        out.save(out_path, "PNG")
        
        with open(out_path, 'rb') as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=f"✅ Ваш баннер готов!\n\n📏 Размер: {size}\n🎨 Макет: {layout}\n🌍 Язык: {language}\n\nХотите создать еще один баннер с тем же изображением и текстом?"
            )
        
        # Clean up the temporary file
        os.remove(out_path)
        
        # Keep the image and text data, but clear size and layout
        image_data = context.user_data['image_data']
        headline = context.user_data['headline']
        subheadline = context.user_data['subheadline']
        disclaimer = context.user_data['disclaimer']
        language = context.user_data['language']
        
        # Clear context but keep essential data
        context.user_data.clear()
        context.user_data['image_data'] = image_data
        context.user_data['headline'] = headline
        context.user_data['subheadline'] = subheadline
        context.user_data['disclaimer'] = disclaimer
        context.user_data['language'] = language
        
        # Create keyboard for creating another banner
        keyboard = [
            ["🔄 Создать еще один баннер"],
            ["🆕 Начать заново"],
            ["❌ Завершить"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            "Выберите действие:",
            reply_markup=reply_markup
        )
        
        logger.info(f"Banner created successfully for user {update.effective_user.id}")
        return WAITING_FOR_ANOTHER_BANNER
        
    except Exception as e:
        logger.error(f"Error creating banner: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при создании баннера. Попробуйте еще раз с /start.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END

async def handle_another_banner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the choice to create another banner or start over"""
    choice = update.message.text
    
    if choice == "🔄 Создать еще один баннер":
        # Create size keyboard
        size_keyboard = [[size] for size in AVAILABLE_SIZES]
        reply_markup = ReplyKeyboardMarkup(size_keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            "Выберите размер для нового баннера:",
            reply_markup=reply_markup
        )
        return WAITING_FOR_SIZE
        
    elif choice == "🆕 Начать заново":
        await update.message.reply_text(
            "Хорошо! Отправьте новое изображение для создания баннера.",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAITING_FOR_IMAGE
        
    elif choice == "❌ Завершить":
        await update.message.reply_text(
            "Спасибо за использование бота! Используйте /start для создания новых баннеров.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END
        
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите один из предложенных вариантов."
        )
        return WAITING_FOR_ANOTHER_BANNER

# Register Telegram handlers only if application was built successfully
if application:
    # Create conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_FOR_IMAGE: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image)],
            WAITING_FOR_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)],
            WAITING_FOR_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_size)],
            WAITING_FOR_LAYOUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_layout)],
            WAITING_FOR_ANOTHER_BANNER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_another_banner)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("help", help_command)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    logger.info("Telegram handlers registered successfully")
else:
    logger.error("Cannot register handlers - application is None")

# FastAPI routes
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    """Handle Telegram webhook updates"""
    try:
        data = await request.json()
        logger.info(f"Received webhook update: {data.get('update_id', 'unknown')}")
        
        if not application:
            logger.error("Telegram application not available")
            return {"ok": False, "error": "Application not initialized"}
        
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        logger.info("Webhook update processed successfully")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/")
def health():
    """Health check endpoint"""
    return {
        "status": "ok", 
        "service": "telegram_overlay_bot",
        "telegram_token_set": bool(TOKEN),
        "application_ready": bool(application)
    }

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return PlainTextResponse("ok")

@app.get("/layouts")
def get_layouts():
    """Get available sizes and layouts"""
    return JSONResponse({"sizes": list(SIZES.keys()), "layouts": list(LAYOUTS.keys())})

@app.get("/config")
def get_config():
    """Get full configuration for n8n integration"""
    return JSONResponse({
        "sizes": list(SIZES.keys()),
        "layouts": list(LAYOUTS.keys()),
        "languages": AVAILABLE_LANGUAGES,
        "download_phrases": DOWNLOAD_APP_PHRASES,
        "endpoints": {
            "render": "/render",
            "render_json": "/render-json",
            "render_json_file": "/render-json-file",
            "health": "/health",
            "layouts": "/layouts",
            "config": "/config"
        }
    })

@app.post("/render")
async def render_image(
    image: UploadFile = File(...),
    headline: str = Form(""),
    subline: str = Form(""),
    disclaimer: str = Form(""),
    banner_size: str = Form("1200x1200"),
    layout_type: str = Form("Yango_photo"),
    apply_overlay: bool = Form(True),
    language: str = Form("English")
):
    """Render image with text overlay"""
    try:
        data = await image.read()
        bg = Image.open(io.BytesIO(data)).convert("RGBA")
        
        # Check and resize image to 2890x2890 if needed
        img_width, img_height = bg.size
        if img_width != 2890 or img_height != 2890:
            logger.info(f"Resizing image from {img_width}x{img_height} to 2890x2890")
            bg = bg.resize((2890, 2890), Image.LANCZOS)
        
        if banner_size not in SIZES:
            return JSONResponse(
                {"error": f"Unknown banner_size {banner_size}"}, 
                status_code=400
            )
        
        bg = process_background_image(bg, banner_size, language)
        out = compose(bg, headline, subline, disclaimer, banner_size, layout_type, apply_overlay, language)
        
        out_path = f"result_{uuid.uuid4().hex}.png"
        out.save(out_path, "PNG")
        
        return FileResponse(
            out_path, 
            media_type="image/png", 
            filename=os.path.basename(out_path)
        )
    except Exception as e:
        logger.error(f"Error rendering image: {str(e)}")
        return JSONResponse(
            {"error": f"Failed to render image: {str(e)}"}, 
            status_code=500
        )

@app.post("/render-json")
async def render_image_json(request: Request):
    """Render image with JSON payload (n8n-friendly)"""
    try:
        data = await request.json()
        
        # Extract parameters
        image_base64 = data.get("image")
        headline = data.get("headline", "")
        subline = data.get("subline", "")
        disclaimer = data.get("disclaimer", "")
        banner_size = data.get("banner_size", "1200x1200")
        layout_type = data.get("layout_type", "Yango_photo")
        apply_overlay = data.get("apply_overlay", True)
        language = data.get("language", "English")
        return_file = data.get("return_file", False)  # New parameter
        
        # Validate required fields
        if not image_base64:
            return JSONResponse(
                {"error": "image field is required (base64 encoded)"}, 
                status_code=400
            )
        
        # Decode base64 image
        try:
            import base64
            # Remove data URL prefix if present
            if image_base64.startswith('data:image'):
                image_base64 = image_base64.split(',')[1]
            
            image_data = base64.b64decode(image_base64)
            bg = Image.open(io.BytesIO(image_data)).convert("RGBA")
        except Exception as e:
            return JSONResponse(
                {"error": f"Invalid base64 image: {str(e)}"}, 
                status_code=400
            )
        
        # Validate banner size
        if banner_size not in SIZES:
            return JSONResponse(
                {"error": f"Unknown banner_size {banner_size}"}, 
                status_code=400
            )
        
        # Check and resize image to 2890x2890 if needed
        img_width, img_height = bg.size
        if img_width != 2890 or img_height != 2890:
            logger.info(f"Resizing image from {img_width}x{img_height} to 2890x2890")
            bg = bg.resize((2890, 2890), Image.LANCZOS)
        
        # Process image
        bg = process_background_image(bg, banner_size, language)
        out = compose(bg, headline, subline, disclaimer, banner_size, layout_type, apply_overlay, language)
        
        # Save result
        out_path = f"result_{uuid.uuid4().hex}.png"
        out.save(out_path, "PNG")
        
        # Return file if requested
        if return_file:
            return FileResponse(
                out_path, 
                media_type="image/png", 
                filename=f"banner_{banner_size}_{layout_type}_{uuid.uuid4().hex[:8]}.png"
            )
        
        # Read the result and encode as base64
        with open(out_path, "rb") as f:
            result_data = f.read()
            result_base64 = base64.b64encode(result_data).decode('utf-8')
        
        # Clean up
        os.remove(out_path)
        
        return JSONResponse({
            "success": True,
            "image": f"data:image/png;base64,{result_base64}",
            "filename": f"banner_{banner_size}_{layout_type}_{uuid.uuid4().hex[:8]}.png",
            "size": banner_size,
            "layout": layout_type,
            "language": language
        })
        
    except Exception as e:
        logger.error(f"Error rendering image: {str(e)}")
        return JSONResponse(
            {"error": f"Failed to render image: {str(e)}"}, 
            status_code=500
        )

@app.post("/render-json-file")
async def render_image_json_file(request: Request):
    """Render image with JSON payload and return file (n8n-friendly)"""
    try:
        data = await request.json()
        
        # Extract parameters
        image_base64 = data.get("image")
        headline = data.get("headline", "")
        subline = data.get("subline", "")
        disclaimer = data.get("disclaimer", "")
        banner_size = data.get("banner_size", "1200x1200")
        layout_type = data.get("layout_type", "Yango_photo")
        apply_overlay = data.get("apply_overlay", True)
        language = data.get("language", "English")
        
        # Validate required fields
        if not image_base64:
            return JSONResponse(
                {"error": "image field is required (base64 encoded)"}, 
                status_code=400
            )
        
        # Decode base64 image
        try:
            import base64
            # Remove data URL prefix if present
            if image_base64.startswith('data:image'):
                image_base64 = image_base64.split(',')[1]
            
            image_data = base64.b64decode(image_base64)
            bg = Image.open(io.BytesIO(image_data)).convert("RGBA")
        except Exception as e:
            return JSONResponse(
                {"error": f"Invalid base64 image: {str(e)}"}, 
                status_code=400
            )
        
        # Validate banner size
        if banner_size not in SIZES:
            return JSONResponse(
                {"error": f"Unknown banner_size {banner_size}"}, 
                status_code=400
            )
        
        # Check and resize image to 2890x2890 if needed
        img_width, img_height = bg.size
        if img_width != 2890 or img_height != 2890:
            logger.info(f"Resizing image from {img_width}x{img_height} to 2890x2890")
            bg = bg.resize((2890, 2890), Image.LANCZOS)
        
        # Process image
        bg = process_background_image(bg, banner_size, language)
        out = compose(bg, headline, subline, disclaimer, banner_size, layout_type, apply_overlay, language)
        
        # Save result
        out_path = f"result_{uuid.uuid4().hex}.png"
        out.save(out_path, "PNG")
        
        return FileResponse(
            out_path, 
            media_type="image/png", 
            filename=f"banner_{banner_size}_{layout_type}_{uuid.uuid4().hex[:8]}.png"
        )
        
    except Exception as e:
        logger.error(f"Error rendering image: {str(e)}")
        return JSONResponse(
            {"error": f"Failed to render image: {str(e)}"}, 
            status_code=500
        )

# Add a test endpoint to verify the bot is working
@app.get("/test-bot")
async def test_bot():
    """Test endpoint to verify bot functionality"""
    if not application:
        return JSONResponse({"error": "Bot not initialized"}, status_code=500)
    
    try:
        bot_info = await application.bot.get_me()
        return {
            "bot_name": bot_info.first_name,
            "bot_username": bot_info.username,
            "bot_id": bot_info.id,
            "can_join_groups": bot_info.can_join_groups,
            "can_read_all_group_messages": bot_info.can_read_all_group_messages,
            "supports_inline_queries": bot_info.supports_inline_queries
        }
    except Exception as e:
        logger.error(f"Error getting bot info: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
