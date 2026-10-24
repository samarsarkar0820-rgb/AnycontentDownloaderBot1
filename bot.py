import os
import re
import uuid
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import telebot
import yt_dlp
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "YOUR_OWNER_USERNAME").strip().lstrip("@")
TUTORIALS_URL = os.getenv("TUTORIALS_URL", "https://t.me/YOUR_TUTORIAL_CHANNEL").strip()
CHANNEL_URL = "https://youtube.com/@bubiloworld_1m?feature=shared"
CAPTION = "Downloaded Successfully! Power by: @AnycontentDownloaderbot"

MAX_FILE_SIZE = 49 * 1024 * 1024
MAX_MEDIA_PER_JOB = 10  # Telegram media groups allow up to 10 items
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("Please set the BOT_TOKEN environment variable.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)
download_slots = threading.Semaphore(2)

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be",
    "instagram.com", "www.instagram.com", "m.instagram.com",
    "facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com",
    "fb.watch", "www.fb.watch",
}

# ---------------- WEB SERVICE ----------------
@app.get("/")
def home():
    return "Anycontent Downloader Bot is running!", 200

@app.get("/health")
def health():
    return {"status": "ok"}, 200

def keep_alive():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), use_reloader=False)

# ---------------- UI ----------------
def main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("📢 SUBSCRIBE CHANNEL", url=CHANNEL_URL),
        InlineKeyboardButton("🎬 ALL TUTORIALS", url=TUTORIALS_URL),
        InlineKeyboardButton("👨‍💻 CONTACT OWNER", url=f"https://t.me/{OWNER_USERNAME}")
    )
    return keyboard

def extract_valid_url(text):
    match = re.search(r"https?://[^\s<>]+", text or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,!?)]}")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        return None
    if parsed.username or parsed.password:
        return None
    return url

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "✨ WELCOME TO ANYCONTENT DOWNLOADER ✨\n\n"
        "Send a supported public YouTube, Instagram, or Facebook link.\n\n"
        "✅ YouTube videos & Shorts\n"
        "✅ Instagram Reels, videos, image posts & supported carousels\n"
        "✅ Facebook Reels, videos, photo posts & supported albums\n"
        "ℹ️ Stories and some posts may require access that is not available to this bot.\n\n"
        "Download only content you own or have permission to save, and follow platform rules.",
        reply_markup=main_keyboard()
    )

@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "Send a public HTTPS link from YouTube, Instagram, or Facebook. "
        "The bot tries video extraction first, then image/gallery extraction. "
        "Private, expired, login-protected, or unsupported links may fail."
    )

# ---------------- DOWNLOADERS ----------------
def download_with_ytdlp(url, job_dir):
    """Try video/media extraction using yt-dlp. Returns downloaded file paths."""
    template = str(job_dir / "%(autonumber)03d_%(id)s.%(ext)s")
    options = {
        "outtmpl": template,
        "format": "best[ext=mp4][filesize<49M]/best[filesize<49M]/best",
        "noplaylist": False,
        "playlistend": MAX_MEDIA_PER_JOB,
        "max_filesize": MAX_FILE_SIZE,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "overwrites": False,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.extract_info(url, download=True)
    return [p for p in job_dir.rglob("*") if p.is_file() and p.stat().st_size > 0]

def download_with_gallery_dl(url, job_dir):
    """Fallback for image posts/carousels and supported galleries."""
    executable = shutil.which("gallery-dl")
    if not executable:
        raise RuntimeError("gallery-dl is not installed")
    # Keep output within this job's temporary folder.
    result = subprocess.run(
        [executable, "--destination", str(job_dir), url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=150
    )
    files = [p for p in job_dir.rglob("*") if p.is_file() and p.stat().st_size > 0]
    if not files:
        detail = (result.stderr or result.stdout or "No media returned")[-700:]
        raise RuntimeError(f"gallery-dl could not retrieve media: {detail}")
    return files

def download_media(url, job_dir):
    errors = []
    try:
        files = download_with_ytdlp(url, job_dir)
        files = [p for p in files if p.is_file() and p.stat().st_size > 0]
        if files:
            return files[:MAX_MEDIA_PER_JOB]
    except Exception as exc:
        logger.warning("yt-dlp failed: %s", exc)
        errors.append(str(exc))

    # Clean partial files before trying the gallery/image downloader.
    for p in list(job_dir.rglob("*")):
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    try:
        files = download_with_gallery_dl(url, job_dir)
        return files[:MAX_MEDIA_PER_JOB]
    except Exception as exc:
        logger.warning("gallery-dl failed: %s", exc)
        errors.append(str(exc))
    raise RuntimeError("Both download methods failed. " + " | ".join(errors)[-900:])

def is_image(path):
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}

def is_video(path):
    return path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

def send_downloaded_files(chat_id, files, status):
    valid = []
    for path in files:
        try:
            if path.is_file() and 0 < path.stat().st_size <= MAX_FILE_SIZE:
                valid.append(path)
        except OSError:
            continue

    if not valid:
        raise RuntimeError("No media file under the Telegram 49 MB upload limit was found.")

    valid = valid[:MAX_MEDIA_PER_JOB]
    try:
        bot.edit_message_text("⬆️ Uploading media...", status.chat.id, status.message_id)
    except Exception:
        pass

    # Telegram media groups support photos/videos. Send groups in batches of at most 10.
    if len(valid) > 1 and all(is_image(p) or is_video(p) for p in valid):
        media = []
        opened = []
        try:
            for index, path in enumerate(valid):
                fh = path.open("rb")
                opened.append(fh)
                caption = CAPTION if index == 0 else None
                if is_image(path):
                    media.append(InputMediaPhoto(fh, caption=caption))
                elif is_video(path):
                    media.append(InputMediaVideo(fh, caption=caption, supports_streaming=True))
                else:
                    raise RuntimeError("Unsupported file type in media group.")
            bot.send_media_group(chat_id, media, timeout=180)
            return
        finally:
            for fh in opened:
                fh.close()

    # One file at a time, including mixed media or document-like files.
    for index, path in enumerate(valid):
        caption = CAPTION if index == 0 else None
        with path.open("rb") as fh:
            if is_image(path):
                bot.send_photo(chat_id, fh, caption=caption, timeout=180)
            elif is_video(path):
                bot.send_video(chat_id, fh, caption=caption, supports_streaming=True, timeout=180)
            else:
                bot.send_document(chat_id, fh, caption=caption, timeout=180)

# ---------------- LINK HANDLER ----------------
@bot.message_handler(content_types=["text"], func=lambda message: not (message.text or "").startswith("/"))
def handle_link(message):
    url = extract_valid_url(message.text)
    if not url:
        bot.reply_to(message, "❌ Send a valid HTTPS link from YouTube, Instagram, or Facebook.")
        return

    status = bot.reply_to(message, "🔍 Analyzing link...")
    if not download_slots.acquire(blocking=False):
        bot.edit_message_text("⏳ The bot is busy. Please try again shortly.", status.chat.id, status.message_id)
        return

    job_dir = DOWNLOAD_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        bot.edit_message_text("⬇️ Downloading media. Please wait...", status.chat.id, status.message_id)
        files = download_media(url, job_dir)
        send_downloaded_files(message.chat.id, files, status)
        try:
            bot.edit_message_text("✅ Finished! Check the media sent above.", status.chat.id, status.message_id)
        except Exception:
            pass
    except Exception:
        logger.exception("Download job failed")
        try:
            bot.edit_message_text(
                "❌ Could not download this link.\n\n"
                "The post may be private, expired, login-protected, unsupported, or larger than 49 MB. "
                "Try another public link. Check Render Logs for the technical error.",
                status.chat.id, status.message_id
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        download_slots.release()

# ---------------- RUN ----------------
if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    logger.info("Anycontent Downloader Bot is starting...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
