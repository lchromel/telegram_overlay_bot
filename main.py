import os
import logging
import json
import io
import math
import uuid
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

SIZES = {k: tuple(v) for k, v in CONFIG["sizes"].items()}
BASE_STYLE = CONFIG["base_style"]
LAYOUTS = CONFIG["layouts"]

# Telegram bot setup
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
application = ApplicationBuilder().token(TOKEN).build()

# FastAPI app setup
app = FastAPI()

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

def compose(bg, headline, subline, disclaimer, banner_key, layout_key, apply_overlay=True):
    w, h = bg.size
    draw = ImageDraw.Draw(bg)
    layout = LAYOUTS.get(layout_key, LAYOUTS["L1_basic"])
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

    # draw
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
                draw.text((x + dx, y), line, font=font, fill=(0, 0, 0, 255))
            elif align == "right":
                lw = text_width(draw, line, font)
                draw.text((x + max_w - lw, y), line, font=font, fill=(0, 0, 0, 255))
            else:
                draw.text((x, y), line, font=font, fill=(0, 0, 0, 255))
            y += lh
        if i < len(gaps):
            y += gaps[i]

    # overlay
    if apply_overlay:
        ov_path = os.path.join("Overlay", f"{banner_key}.png")
        if os.path.exists(ov_path):
            ov = Image.open(ov_path).convert("RGBA").resize((w, h))
            bg = Image.alpha_composite(bg.convert("RGBA"), ov)
    return bg

# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_message = """Привет! ✅ Я работаю на Railway через webhook

Доступные команды:
/start - Показать это сообщение
/help - Показать справку по использованию
/layouts - Показать доступные размеры и макеты

Для обработки изображений используйте веб-интерфейс или API."""
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """📋 Справка по использованию бота:

🖼️ Доступные размеры изображений:
• 1200x1200 (квадрат)
• 1200x1500 (вертикальный)
• 1200x628 (горизонтальный)
• 1080x1920 (сторис)

🎨 Доступные макеты:
• L1_basic - Базовый (слева внизу)
• L2_center - По центру
• L3_top_left - Слева вверху
• L4_top_right - Справа вверху
• L5_bottom_right - Справа внизу
• L6_split_headline - Разделенный заголовок

🌐 Веб-интерфейс: /render endpoint
📡 API: POST /render с параметрами"""
    await update.message.reply_text(help_text)

async def layouts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /layouts command"""
    sizes_text = "📏 Доступные размеры:\n" + "\n".join([f"• {size}" for size in SIZES.keys()])
    layouts_text = "🎨 Доступные макеты:\n" + "\n".join([f"• {layout}" for layout in LAYOUTS.keys()])
    
    await update.message.reply_text(f"{sizes_text}\n\n{layouts_text}")

# Register Telegram handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("layouts", layouts_command))

# FastAPI routes
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    """Handle Telegram webhook updates"""
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
def health():
    """Health check endpoint"""
    return {"status": "ok", "service": "telegram_overlay_bot"}

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
    layout_type: str = Form("L1_basic"),
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
        bg = bg.resize((w, h), Image.LANCZOS)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
