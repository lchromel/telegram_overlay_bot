import os
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

user_text = {}

def process_image(image_path, headline, subtitle, disclaimer):
    with Image.open(image_path) as base_image:
        width, height = base_image.size
        aspect_ratio = width / height

        # Determine output size and overlay path
        if abs(aspect_ratio - 1.0) < 0.15:
            output_size = (1200, 1200)
            overlay_path = "Overlay/1200x1200.png"
        elif aspect_ratio > 1.0:
            output_size = (1200, 628)
            overlay_path = "Overlay/1200x628.png"
        else:
            output_size = (1200, 1500)
            overlay_path = "Overlay/1200x1500.png"

        # Resize base image
        resized = base_image.resize(output_size)
        
        # Load and resize overlay
        with Image.open(overlay_path) as overlay_img:
            overlay = overlay_img.resize(output_size)
        
        # Create a new image for text
        text_layer = Image.new("RGBA", output_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_layer)
        
        # Load fonts
        headline_font = ImageFont.truetype("Fonts/YangoGroupHeadline-HeavyArabic.ttf", 72)
        body_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 48)

        # Draw text
        if headline:
            draw.text((50, 100), headline, font=headline_font, fill="white")
        if subtitle:
            draw.text((50, 200), subtitle, font=body_font, fill="white")
        if disclaimer:
            draw.text((50, output_size[1] - 100), disclaimer, font=body_font, fill="white")

        # Combine all layers
        result = Image.alpha_composite(resized.convert("RGBA"), overlay)
        result = Image.alpha_composite(result, text_layer)
        
        output_path = "result.png"
        result.save(output_path)
        return output_path

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Пришли картинку, а затем текст в формате:\nЗаголовок | Подзаголовок | Дисклеймер")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text[update.message.from_user.id] = update.message.text.split("|")
    await update.message.reply_text("✅ Текст сохранён. Теперь пришли изображение.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_text:
        await update.message.reply_text("⚠️ Сначала пришли текст в формате: Заголовок | Подзаголовок | Дисклеймер")
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = f"temp_{user_id}.jpg"
    await file.download_to_drive(file_path)

    headline, subtitle, disclaimer = (user_text[user_id] + ["", "", ""])[:3]
    result_path = process_image(file_path, headline.strip(), subtitle.strip(), disclaimer.strip())

    await update.message.reply_photo(photo=open(result_path, "rb"))
    os.remove(file_path)
    os.remove(result_path)

if __name__ == "__main__":
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set.")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.run_polling()
