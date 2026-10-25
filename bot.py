import os, re, glob, shutil, tempfile, subprocess, sys
from threading import Thread
from flask import Flask
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "AnycontentDownloaderbot").strip().lstrip("@")
TUTORIALS_URL = os.getenv("TUTORIALS_URL", "").strip()
MAX_SEND_BYTES = 49 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
CHUNK = 1024 * 1024
URL_RE = re.compile(r"https?://\S+", re.I)
if not BOT_TOKEN:
    raise RuntimeError("Please set BOT_TOKEN in Render Environment Variables.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

@app.get("/")
def home():
    return "Anycontent Downloader Bot is running", 200

def keep_alive():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

def buttons(url):
    kb = InlineKeyboardMarkup()
    ext = os.path.splitext(url.split("?", 1)[0])[1].lower()
    direct_ext = {".zip",".rar",".7z",".pdf",".jpg",".jpeg",".png",".webp",".gif",".mp4",".mov",".mkv",".mp3",".wav",".apk",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".txt",".csv",".epub",".webm"}
    label = "⬇️ ফাইল ডাউনলোড করুন" if ext in direct_ext else "🔗 মূল লিংক খুলুন"
    kb.add(InlineKeyboardButton(label, url=url))
    return kb

def fallback(chat_id, url, reason):
    bot.send_message(chat_id, f"⚠️ {reason}\n\nবট সরাসরি ফাইল পাঠাতে পারেনি। নিচের লিংকে চাপ দিয়ে মূল সাইট/ফাইল খুলে ডাউনলোডের চেষ্টা করো।", reply_markup=buttons(url), disable_web_page_preview=True)

def safe_name(name):
    name = os.path.basename(name or "download.bin")
    name = re.sub(r'[\\\\/*?:"<>|]+', "_", name).strip(" .")
    return (name or "download.bin")[:180]

def direct_download(url, folder):
    try:
        with requests.get(url, stream=True, allow_redirects=True, timeout=(15, 45),
                          headers={"User-Agent":"Mozilla/5.0"}) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower()
            final = r.url
            if "text/html" in ctype:
                return None
            length = r.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                return None
            filename = ""
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r'filename\\*?=(?:UTF-8\\'\\')?["\\']?([^"\\';]+)', cd, re.I)
            if m: filename = requests.utils.unquote(m.group(1))
            if not filename: filename = os.path.basename(final.split("?",1)[0]) or "download.bin"
            path = os.path.join(folder, safe_name(filename))
            total = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if not chunk: continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        return None
                    f.write(chunk)
            return path if total else None
    except Exception:
        return None

def ytdlp_download(url, folder):
    opts = {
        "outtmpl": os.path.join(folder, "%(title).120B-%(id)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "retries": 2, "socket_timeout": 25, "max_filesize": MAX_DOWNLOAD_BYTES,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        files = [p for p in glob.glob(os.path.join(folder, "*")) if os.path.isfile(p)]
        return max(files, key=os.path.getsize) if files else None
    except Exception:
        return None

def gallery_download(url, folder):
    try:
        subprocess.run([sys.executable, "-m", "gallery_dl", "-D", folder, url],
                       capture_output=True, text=True, timeout=180)
        files = [p for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True) if os.path.isfile(p)]
        return files[:10]
    except Exception:
        return []

def send_path(message, path, url):
    if os.path.getsize(path) > MAX_SEND_BYTES:
        fallback(message.chat.id, url, "ফাইলটি 49 MB-এর বেশি। Telegram Bot API-তে সাধারণ আপলোড সীমা 50 MB।")
        return
    try:
        with open(path, "rb") as f:
            bot.send_document(message.chat.id, f, visible_file_name=safe_name(path),
                              caption=f"✅ Downloaded Successfully!\nPower by: @{OWNER_USERNAME}", timeout=180)
    except Exception:
        fallback(message.chat.id, url, "ফাইলটি Telegram-এ পাঠানো যায়নি।")

@bot.message_handler(commands=["start", "help"])
def start(message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📢 Subscribe Channel", url="https://youtube.com/@bubiloworld_1m?feature=shared"))
    if TUTORIALS_URL: kb.add(InlineKeyboardButton("📚 All Tutorials", url=TUTORIALS_URL))
    if OWNER_USERNAME: kb.add(InlineKeyboardButton("👤 Contact Owner", url=f"https://t.me/{OWNER_USERNAME}"))
    bot.send_message(message.chat.id,
        "👋 <b>Anycontent Downloader Bot</b>\n\n"
        "একটি public link পাঠাও। বট যতটা পারবে ফাইল ডাউনলোড করে Telegram-এ পাঠাবে। "
        "না পারলে মূল লিংক দেওয়ার চেষ্টা করবে।\n\n"
        "সমর্থন সাইটভেদে আলাদা; login/private link নাও চলতে পারে। শুধু নিজের বা ডাউনলোডের অনুমতি থাকা ফাইল ব্যবহার করো।",
        reply_markup=kb)

@bot.message_handler(func=lambda m: bool(URL_RE.search(m.text or "")), content_types=["text"])
def handle_link(message):
    url = URL_RE.search(message.text).group(0).rstrip(".,);]")
    status = bot.reply_to(message, "🔎 লিংক পরীক্ষা করছি…")
    folder = tempfile.mkdtemp(prefix="downloader_")
    try:
        path = ytdlp_download(url, folder)
        if not path:
            files = gallery_download(url, folder)
            path = files[0] if files else None
        if not path:
            path = direct_download(url, folder)
        if path and os.path.isfile(path):
            send_path(message, path, url)
        else:
            fallback(message.chat.id, url, "এই লিংকটি বট দিয়ে সরাসরি ডাউনলোড করা যায়নি বা সাইটটি সমর্থিত নয়।")
    except Exception:
        fallback(message.chat.id, url, "ডাউনলোডের সময় সমস্যা হয়েছে।")
    finally:
        try: bot.delete_message(message.chat.id, status.message_id)
        except Exception: pass
        shutil.rmtree(folder, ignore_errors=True)

@bot.message_handler(func=lambda m: True, content_types=["text"])
def help_text(message):
    bot.reply_to(message, "একটি public URL পাঠাও।")

if __name__ == "__main__":
    Thread(target=keep_alive, daemon=True).start()
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
                              
