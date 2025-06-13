import os
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

user_text = {}

def wrap_text(text, font, draw, max_width):
    words = text.split()
    lines = []
    current_line = ''
    for word in words:
        test_line = current_line + (' ' if current_line else '') + word
        w, _ = draw.textbbox((0, 0), test_line, font=font)[2:]
        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines

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

        # Cover and crop logic
        out_w, out_h = output_size
        scale = max(out_w / width, out_h / height)
        new_w = int(width * scale)
        new_h = int(height * scale)
        resized = base_image.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - out_w) // 2
        top = (new_h - out_h) // 2
        right = left + out_w
        bottom = top + out_h
        cropped = resized.crop((left, top, right, bottom))
        
        # Load and resize overlay
        with Image.open(overlay_path) as overlay_img:
            overlay = overlay_img.resize(output_size)
        
        # Create a new image for text
        text_layer = Image.new("RGBA", output_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_layer)
        
        # Set font sizes for each block and format
        if output_size in [(1200, 1200), (1200, 1500)]:
            headline_font = ImageFont.truetype("Fonts/YangoGroupHeadline-HeavyArabic.ttf", 124)
            subheadline_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 48)
            disclaimer_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 14)
        elif output_size == (1200, 628):
            headline_font = ImageFont.truetype("Fonts/YangoGroupHeadline-HeavyArabic.ttf", 92)
            subheadline_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 32)
            disclaimer_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 12)
        else:
            headline_font = ImageFont.truetype("Fonts/YangoGroupHeadline-HeavyArabic.ttf", 72)
            subheadline_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 48)
            disclaimer_font = ImageFont.truetype("Fonts/YangoGroupText-Medium.ttf", 48)

        # Calculate max text width
        if output_size == (1200, 628):
            max_text_width = 564
        else:
            shortest_side = min(out_w, out_h)
            max_text_width = int(shortest_side * 0.8)

        # Helper to get line spacing for each block type
        def get_line_spacing(font, block_type):
            if block_type == 'headline':
                return int(font.size * 0.15)
            elif block_type == 'subheadline':
                return int(font.size * 0.2)
            else:
                return 10  # default for disclaimer

        # 1200x1200 and 1200x1500: anchor all text blocks to the bottom with 24px spacing between blocks, order: disclaimer, subheadline, headline
        if output_size in [(1200, 1200), (1200, 1500)]:
            blocks = []
            if headline:
                headline_lines = wrap_text(headline, headline_font, draw, max_text_width)
                blocks.append((headline_lines, headline_font, 'headline'))
            if subtitle:
                subtitle_lines = wrap_text(subtitle, subheadline_font, draw, max_text_width)
                blocks.append((subtitle_lines, subheadline_font, 'subheadline'))
            if disclaimer:
                disclaimer_lines = wrap_text(disclaimer, disclaimer_font, draw, max_text_width)
                blocks.append((disclaimer_lines, disclaimer_font, 'disclaimer'))
            # Calculate total height of all blocks (including 24px spacing between blocks)
            block_heights = []
            for lines, font, block_type in blocks:
                line_spacing = get_line_spacing(font, block_type)
                block_height = sum([draw.textbbox((0, 0), line, font=font)[3] for line in lines]) + (len(lines)-1)*line_spacing
                block_heights.append(block_height)
            total_blocks_height = sum(block_heights) + (len(blocks)-1)*24
            # Start y so the whole stack fits above the bottom margin
            y = out_h - 50 - total_blocks_height  # 50px bottom margin
            for (lines, font, block_type), block_height in zip(blocks, block_heights):
                line_spacing = get_line_spacing(font, block_type)
                for idx, line in enumerate(lines):
                    w, h = draw.textbbox((0, 0), line, font=font)[2:]
                    x = (out_w - w) // 2
                    draw.text((x, y), line, font=font, fill="white")
                    if idx < len(lines) - 1:
                        y += h + line_spacing
                    else:
                        y += h
                y += 24  # 24px block spacing
        # 1200x628: anchor all text blocks to the top, 40px margin from top, 28px spacing between blocks
        elif output_size == (1200, 628):
            y = 40
            block_x = 40
            block_width = 564
            if headline:
                lines = wrap_text(headline, headline_font, draw, max_text_width)
                line_spacing = int(headline_font.size * 0.15)
                for idx, line in enumerate(lines):
                    w, h = draw.textbbox((0, 0), line, font=headline_font)[2:]
                    x = block_x + (block_width - w) // 2
                    draw.text((x, y), line, font=headline_font, fill="white")
                    if idx < len(lines) - 1:
                        y += h + line_spacing
                    else:
                        y += h
                y += 28
            if subtitle:
                lines = wrap_text(subtitle, subheadline_font, draw, max_text_width)
                line_spacing = int(subheadline_font.size * 0.2)
                for idx, line in enumerate(lines):
                    w, h = draw.textbbox((0, 0), line, font=subheadline_font)[2:]
                    x = block_x + (block_width - w) // 2
                    draw.text((x, y), line, font=subheadline_font, fill="white")
                    if idx < len(lines) - 1:
                        y += h + line_spacing
                    else:
                        y += h
                y += 28
            if disclaimer:
                lines = wrap_text(disclaimer, disclaimer_font, draw, max_text_width)
                for line in lines:
                    w, h = draw.textbbox((0, 0), line, font=disclaimer_font)[2:]
                    x = out_w - w - 50
                    draw.text((x, y), line, font=disclaimer_font, fill="white")
                    y += h + 10
        else:
            # Center-align headline with wrapping
            if headline:
                lines = wrap_text(headline, headline_font, draw, max_text_width)
                y = 100
                line_spacing = int(headline_font.size * 0.15)
                for idx, line in enumerate(lines):
                    w, h = draw.textbbox((0, 0), line, font=headline_font)[2:]
                    x = (out_w - w) // 2
                    draw.text((x, y), line, font=headline_font, fill="white")
                    if idx < len(lines) - 1:
                        y += h + line_spacing
                    else:
                        y += h
            # Center-align subtitle with wrapping
            if subtitle:
                lines = wrap_text(subtitle, subheadline_font, draw, max_text_width)
                y = 200
                line_spacing = int(subheadline_font.size * 0.2)
                for idx, line in enumerate(lines):
                    w, h = draw.textbbox((0, 0), line, font=subheadline_font)[2:]
                    x = (out_w - w) // 2
                    draw.text((x, y), line, font=subheadline_font, fill="white")
                    if idx < len(lines) - 1:
                        y += h + line_spacing
                    else:
                        y += h
            # Disclaimer alignment with wrapping
            if disclaimer:
                lines = wrap_text(disclaimer, disclaimer_font, draw, max_text_width)
                total_height = sum([draw.textbbox((0, 0), line, font=disclaimer_font)[3] for line in lines]) + (len(lines)-1)*10
                y = out_h - 100 - total_height + 10  # Adjust so last line is at -100
                for idx, line in enumerate(lines):
                    w, h = draw.textbbox((0, 0), line, font=disclaimer_font)[2:]
                    x = (out_w - w) // 2
                    draw.text((x, y), line, font=disclaimer_font, fill="white")
                    y += h + 10

        # Combine all layers
        result = Image.alpha_composite(cropped.convert("RGBA"), overlay)
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
