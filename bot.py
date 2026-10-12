import os
import re
import time
import uuid
import threading
import logging
from pathlib import Path
from urllib.parse import urlparse

import telebot
import yt_dlp
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------------- CONFIGURATION ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "YOUR_OWNER_USERNAME").strip()
TUTORIALS_URL = os.getenv(
    "TUTORIALS_URL", "https://t.me/YOUR_TUTORIAL_CHANNEL"
).strip()

CHANNEL_URL = "https://youtube.com/@bubiloworld_1m?feature=shared"
CAPTION = (
    "Downloaded Successfully! Power by: @AnycontentDownloaderbot"
)

MAX_FILE_SIZE = 49 * 1024 * 1024  # 49 MB
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("Please set the BOT_TOKEN environment variable.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)

# Limit simultaneous downloads to reduce memory/disk pressure.
download_slots = threading.Semaphore(2)


# ---------------- FLASK KEEP-ALIVE ----------------

@app.get("/")
def home():
    return "Anycontent Downloader Bot is running!", 200


@app.get("/health")
def health():
    return {"status": "ok"}, 200


def keep_alive():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ---------------- KEYBOARD ----------------

def main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)

    keyboard.add(
        InlineKeyboardButton(
            "📢 SUBSCRIBE CHANNEL",
            url=CHANNEL_URL
        ),
        InlineKeyboardButton(
            "🎬 ALL TUTORIALS",
            url=TUTORIALS_URL
        ),
        InlineKeyboardButton(
            "👨‍💻 CONTACT OWNER",
            url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}"
        )
    )
    return keyboard


# ---------------- URL VALIDATION ----------------

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.watch",
    "www.fb.watch",
    "web.facebook.com",
}


def extract_valid_url(text):
    match = re.search(r"https?://[^\s<>]+", text or "")
    if not match:
        return None

    url = match.group(0).rstrip(".,!?)]}")
    parsed = urlparse(url)

    if parsed.scheme != "https":
        return None

    if parsed.hostname not in ALLOWED_HOSTS:
        return None

    if parsed.username or parsed.password:
        return None

    return url


# ---------------- START COMMAND ----------------

@bot.message_handler(commands=["start"])
def start(message):
    welcome = (
        "✨ WELCOME TO ANYCONTENT DOWNLOADER ✨\n\n"
        "🎬 Your simple video downloading assistant!\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ YouTube Videos\n"
        "✅ Instagram Reels & Videos\n"
        "✅ Facebook Videos\n"
        "⚡ Simple link-based interface\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📥 Send a supported public video link to begin.\n\n"
        "Please download only content you own or have "
        "permission to download, and follow each platform's rules."
    )

    bot.send_message(
        message.chat.id,
        welcome,
        reply_markup=main_keyboard()
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "Send a supported YouTube, Instagram, or Facebook video URL.\n"
        "Only public, accessible videos are supported. Some videos "
        "may require permissions or may not be downloadable."
    )


# ---------------- DOWNLOAD PROGRESS ----------------

def download_video(url, job_id, status_message):
    output_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    progress_state = {"half": False}

    def progress_hook(data):
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get(
                "total_bytes_estimate"
            )
            downloaded = data.get("downloaded_bytes", 0)

            if total and downloaded >= total * 0.50:
                if not progress_state["half"]:
                    progress_state["half"] = True
                    try:
                        bot.edit_message_text(
                            "⬇️ Downloading (50%)...",
                            status_message.chat.id,
                            status_message.message_id
                        )
                    except Exception:
                        pass

        elif data.get("status") == "finished":
            logger.info("Download finished: %s", job_id)

    options = {
        "outtmpl": output_template,
        "format": (
            "best[ext=mp4][filesize<49M]/"
            "best[filesize<49M]/best"
        ),
        "noplaylist": True,
        "max_filesize": MAX_FILE_SIZE,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "restrictfilenames": True,
        "overwrites": False,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        filepath = Path(downloader.prepare_filename(info))

    # A postprocessor may change the final extension.
    if not filepath.exists():
        candidates = list(DOWNLOAD_DIR.glob(f"{job_id}.*"))
        candidates = [p for p in candidates if p.is_file()]
        if not candidates:
            raise FileNotFoundError("Downloaded file was not found.")
        filepath = candidates[0]

    if filepath.stat().st_size > MAX_FILE_SIZE:
        raise ValueError("The video exceeds the 49 MB upload limit.")

    return filepath


# ---------------- VIDEO LINK HANDLER ----------------

@bot.message_handler(
    content_types=["text"],
    func=lambda message: not (message.text or "").startswith("/")
)
def handle_video_link(message):
    url = extract_valid_url(message.text)

    if not url:
        bot.reply_to(
            message,
            "❌ Please send a valid HTTPS link from YouTube, "
            "Instagram, or Facebook."
        )
        return

    status = bot.reply_to(message, "🔍 Analyzing...")

    if not download_slots.acquire(blocking=False):
        bot.edit_message_text(
            "⏳ The bot is busy. Please try again shortly.",
            status.chat.id,
            status.message_id
        )
        return

    job_id = uuid.uuid4().hex
    filepath = None

    try:
        filepath = download_video(url, job_id, status)

        bot.edit_message_text(
            "⬆️ Uploading (100%)...",
            status.chat.id,
            status.message_id
        )

        with filepath.open("rb") as video:
            bot.send_video(
                chat_id=message.chat.id,
                video=video,
                caption=CAPTION,
                supports_streaming=True,
                timeout=180
            )

        bot.edit_message_text(
            "✅ Downloaded and sent successfully!",
            status.chat.id,
            status.message_id
        )

    except Exception as error:
        logger.exception("Download job failed: %s", job_id)

        try:
            bot.edit_message_text(
                "❌ Unable to download this video.\n\n"
                "It may be private, unsupported, unavailable, "
                "too large, or restricted by the platform.\n"
                "Please try another accessible link.",
                status.chat.id,
                status.message_id
            )
        except Exception:
            pass

    finally:
        # Delete the temporary file even if upload fails.
        if filepath and filepath.exists():
            try:
                os.remove(filepath)
            except OSError:
                logger.exception("Could not remove %s", filepath)

        # Clean up any remaining files created for this job.
        for leftover in DOWNLOAD_DIR.glob(f"{job_id}.*"):
            try:
                os.remove(leftover)
            except OSError:
                logger.exception("Could not remove %s", leftover)

        download_slots.release()


# ---------------- RUN BOT ----------------

if __name__ == "__main__":
    threading.Thread(
        target=keep_alive,
        daemon=True
    ).start()

    logger.info("Anycontent Downloader Bot is starting...")
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
  )
  
