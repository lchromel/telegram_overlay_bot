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
AVAILABLE_LAYOUTS = ["Yango_photo", "Yango_pro_app", "Yango_pro_photo", "Yango_pro_Red", "Yango_Red"]
AVAILABLE_LANGUAGES = ["Русский", "English", "العربية", "Türkçe", "Қазақша"]

# Load configuration
try:
    with open("config.json", "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
    SIZES = {k: tuple(v) for k, v in CONFIG["sizes"].items()}
    BASE_STYLE = CONFIG["base_style"]
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
    try:
        return ImageFont.truetype(path, size=size, layout_engine=ImageFont.LAYOUT_RAQM)
    except Exception:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            return ImageFont.load_default()

def is_rtl_text(s: str) -> bool:
    return any('\u0590' <= ch <= '\u08FF' for ch in s)  # Hebrew+Arabic ranges

def normalize_text(text: str) -> str:
    if any('\u0600' <= ch <= '\u06FF' for ch in text):  # Arabic
        text = arabic_reshaper.reshape(text)
        text = get_display(text)
    return text

def text_width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]

def wrap_with_limits(draw, text, font, max_width, max_lines, ellipsis):
    text = normalize_text(text)
    words = text.split()
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

def resolve_style(style_key, layout_key, banner_key):
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

def compose(bg, headline, subline, disclaimer, banner_key, layout_key, apply_overlay=True):
    w, h = bg.size
    
    # Apply overlay first (before text)
    if apply_overlay:
        ov_path = os.path.join("Overlay", layout_key, f"{banner_key}.png")
        if os.path.exists(ov_path):
            ov = Image.open(ov_path).convert("RGBA").resize((w, h))
            bg = Image.alpha_composite(bg.convert("RGBA"), ov)
            logger.info(f"Applied overlay: {ov_path}")
        else:
            logger.warning(f"Overlay not found: {ov_path}")
    
    # Now draw text on top of the overlay
    draw = ImageDraw.Draw(bg)
    layout = LAYOUTS.get(layout_key, LAYOUTS["Yango_photo"])
    pad = layout["padding"]
    max_w = w - pad["left"] - pad["right"]

    content_map = {"headline": headline or "", "subline": subline or "", "disclaimer": disclaimer or ""}

    blocks = []
    for key in layout["text_stack"]:
        raw = content_map.get(key, "")
        if not raw.strip():
            continue
        st, font = resolve_style(key, layout_key, banner_key)
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

    # Special handling for 1200x628 size
    if banner_key == "1200x628":
        # 1200x628: anchor all text blocks to the top, 40px margin from top, 28px spacing between blocks
        y = 40
        block_x = 40
        block_width = 540
        
        for i, (lines, st, font, key) in enumerate(blocks):
            lh = line_height_px(font, st["line_height"])
            
            # Special positioning for subline (subtitle)
            if key == "subline":
                subtitle_block_width = 460
                subtitle_block_x = 80
                current_x = subtitle_block_x
                current_width = subtitle_block_width
            else:
                current_x = block_x
                current_width = block_width
            
            for line in lines:
                lw = text_width(draw, line, font)
                # Center within the block
                draw_x = current_x + (current_width - lw) // 2
                draw.text((draw_x, y), line, font=font, fill=(255, 255, 255, 255))  # White text
                y += lh
            
            # Add spacing between blocks (except after the last block)
            if i < len(blocks) - 1:
                y += 28
    else:
        # Standard positioning for other sizes
        for i, (lines, st, font, key) in enumerate(blocks):
            lh = line_height_px(font, st["line_height"])
            align = st.get("align", "left")
            # auto right align if RTL text and layout isn't explicitly left
            join_text = " ".join(lines)
            if is_rtl_text(join_text) and anchor in ("top_right", "bottom_right"):
                align = "right"
            for line in lines:
                # compute x by align
                if align == "center":
                    lw = text_width(draw, line, font)
                    dx = (max_w - lw) // 2
                    draw.text((x + dx, y), line, font=font, fill=(255, 255, 255, 255))  # White text
                elif align == "right":
                    lw = text_width(draw, line, font)
                    draw.text((x + max_w - lw, y), line, font=font, fill=(255, 255, 255, 255))  # White text
                else:
                    draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))  # White text
                y += lh
            if i < len(gaps):
                y += gaps[i]

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
5. Выберите размер
6. Выберите макет

🖼️ Доступные размеры:
• 1200x1200 (квадрат)
• 1200x1500 (вертикальный)
• 1200x628 (горизонтальный)
• 1080x1920 (сторис)

🎨 Доступные макеты:
• Yango_photo
• Yango_pro_app
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
    """Handle image upload"""
    try:
        # Get the largest photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Download the image
        image_data = await file.download_as_bytearray()
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
        w, h = SIZES.get(size, (1200, 1200))
        bg = crop_image_to_size(bg, w, h)
        
        # Apply overlay using the selected layout
        out = compose(bg, headline, subheadline, disclaimer, size, layout, apply_overlay=True)
        
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
            WAITING_FOR_IMAGE: [MessageHandler(filters.PHOTO, handle_image)],
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

@app.post("/render")
async def render_image(
    image: UploadFile = File(...),
    headline: str = Form(""),
    subline: str = Form(""),
    disclaimer: str = Form(""),
    banner_size: str = Form("1200x1200"),
    layout_type: str = Form("Yango_photo"),
    apply_overlay: bool = Form(True)
):
    """Render image with text overlay"""
    try:
        data = await image.read()
        bg = Image.open(io.BytesIO(data)).convert("RGBA")
        
        if banner_size not in SIZES:
            return JSONResponse(
                {"error": f"Unknown banner_size {banner_size}"}, 
                status_code=400
            )
        
        w, h = SIZES[banner_size]
        bg = crop_image_to_size(bg, w, h)
        out = compose(bg, headline, subline, disclaimer, banner_size, layout_type, apply_overlay)
        
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
