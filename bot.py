import os
import sqlite3
import aiohttp
import time
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# Create Data folder if it doesn't exist
if not os.path.exists("Data"):
    os.makedirs("Data")

# Read configuration from files
try:
    with open("tg.txt", "r") as f:
        lines = f.readlines()
        BOT_TOKEN = lines[0].strip()
        ADMIN_ID = int(lines[1].strip())
except (FileNotFoundError, IndexError, ValueError):
    print("❌ Error reading tg.txt file")
    exit(1)

try:
    with open("ch.txt", "r", encoding="utf-8") as f:
        lines = f.readlines()
        CHANNEL_USERNAME = lines[0].strip().lstrip('@')
        CHANNEL_NICKNAME = lines[1].strip() if len(lines) > 1 else "کانال ما"
except (FileNotFoundError, IndexError):
    print("❌ Error reading ch.txt file")
    exit(1)

FASTCREATE_API = "https://api.fast-creat.ir/instagram"
API_KEY = "7531225248:EG39gM6sh5dFIVC@Api_ManagerRoBot"

# SQLite database setup
DB_PATH = "Data/bot_data.db"

def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        join_date TEXT,
        download_count INTEGER DEFAULT 0,
        last_download TEXT,
        request_times TEXT
    )
    ''')
    
    # Blocked users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS blocked_users (
        user_id INTEGER PRIMARY KEY
    )
    ''')
    
    # Temp blocked users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS temp_blocked (
        user_id INTEGER PRIMARY KEY,
        unblock_time REAL
    )
    ''')
    
    # Bot status table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bot_status (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        status TEXT DEFAULT 'on'
    )
    ''')
    
    # Insert default bot status if not exists
    cursor.execute('INSERT OR IGNORE INTO bot_status (id, status) VALUES (1, "on")')
    
    conn.commit()
    conn.close()

# Initialize database
init_database()

# Rate limiting cooldown in seconds
DOWNLOAD_COOLDOWN = 3
admin_states = {}

def get_db_connection():
    """Get a database connection"""
    return sqlite3.connect(DB_PATH)

def add_user(chat_id):
    """Add user to database if not exists"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (chat_id,))
    user = cursor.fetchone()
    
    if not user:
        join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            'INSERT INTO users (user_id, join_date, download_count, last_download, request_times) VALUES (?, ?, 0, NULL, "[]")',
            (chat_id, join_date)
        )
        conn.commit()
    
    conn.close()

def update_user_download(chat_id):
    """Update user download count and timestamp"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get current values
    cursor.execute('SELECT download_count, request_times FROM users WHERE user_id = ?', (chat_id,))
    user = cursor.fetchone()
    
    if user:
        download_count = user[0] + 1
        request_times = user[1] or "[]"
        
        # Parse request times
        try:
            times = eval(request_times)
        except:
            times = []
        
        # Add current time
        current_time = time.time()
        times.append(current_time)
        
        # Keep only requests from last hour
        times = [t for t in times if current_time - t < 3600]
        
        # Update user
        last_download = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            'UPDATE users SET download_count = ?, last_download = ?, request_times = ? WHERE user_id = ?',
            (download_count, last_download, str(times), chat_id)
        )
        conn.commit()
    
    conn.close()

def is_user_blocked(chat_id):
    """Check if user is blocked"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM blocked_users WHERE user_id = ?', (chat_id,))
    blocked = cursor.fetchone() is not None
    
    conn.close()
    return blocked

def is_user_temp_blocked(chat_id):
    """Check if user is temporarily blocked and update if expired"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT unblock_time FROM temp_blocked WHERE user_id = ?', (chat_id,))
    result = cursor.fetchone()
    
    if result:
        unblock_time = result[0]
        if time.time() >= unblock_time:
            # Remove expired block
            cursor.execute('DELETE FROM temp_blocked WHERE user_id = ?', (chat_id,))
            conn.commit()
            conn.close()
            return False
        conn.close()
        return True
    
    conn.close()
    return False

def is_user_rate_limited(chat_id):
    """Check if user has exceeded rate limits"""
    # Check if user is temporarily blocked
    if is_user_temp_blocked(chat_id):
        return True
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get user request times
    cursor.execute('SELECT request_times FROM users WHERE user_id = ?', (chat_id,))
    result = cursor.fetchone()
    
    if result:
        request_times_str = result[0] or "[]"
        try:
            request_times = eval(request_times_str)
        except:
            request_times = []
        
        current_time = time.time()
        
        # Count requests in last hour
        recent_requests = [t for t in request_times if current_time - t < 3600]
        
        # Block user for 1 hour if they made 50+ requests
        if len(recent_requests) >= 500:
            # Add to temp blocked
            unblock_time = current_time + 3600
            cursor.execute(
                'INSERT OR REPLACE INTO temp_blocked (user_id, unblock_time) VALUES (?, ?)',
                (chat_id, unblock_time)
            )
            conn.commit()
            conn.close()
            return True
        
        # Check if user is making requests too quickly (1 per 30 seconds)
        if request_times and (current_time - request_times[-1]) < DOWNLOAD_COOLDOWN:
            conn.close()
            return True
    
    conn.close()
    return False

def get_bot_status():
    """Get bot status from database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT status FROM bot_status WHERE id = 1')
    result = cursor.fetchone()
    
    conn.close()
    return result[0] if result else 'on'

def set_bot_status(status):
    """Set bot status in database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE bot_status SET status = ? WHERE id = 1', (status,))
    conn.commit()
    conn.close()

async def is_user_joined(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        # Ensure channel username doesn't start with @
        channel_username = CHANNEL_USERNAME.lstrip('@')
        member = await context.bot.get_chat_member(f"@{channel_username}", chat_id)
        return member.status in ["member", "creator", "administrator"]
    except Exception as e:
        print(f"Error checking channel membership for {chat_id}: {e}")
        return False

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آمار ربات", callback_data="stats")],
        [
            InlineKeyboardButton("📢 ارسال همگانی", callback_data="broadcast"),
            InlineKeyboardButton("🔁 فوروارد همگانی", callback_data="forward")
        ],
        [
            InlineKeyboardButton("🚫 مسدود کردن کاربر", callback_data="block"),
            InlineKeyboardButton("✅ آزاد کردن کاربر", callback_data="unblock")
        ],
        [
            InlineKeyboardButton("🟢 روشن کردن ربات", callback_data="bot_on"),
            InlineKeyboardButton("🔴 خاموش کردن ربات", callback_data="bot_off")
        ],
        [InlineKeyboardButton("👤 پشتیبانی", url="https://t.me/PlushPepeDesigner")]
    ])

def get_user_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 دانلود پست اینستاگرام", callback_data="download_help")],
        [InlineKeyboardButton("👤 مشخصات من", callback_data="my_profile")],
        [InlineKeyboardButton("👥 کانال ما", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("📞 پشتیبانی", url="https://t.me/PlushPepeDesigner")]
    ])

async def cleanup_messages(context, chat_id, exclude_message_id=None):
    """Clean up previous bot messages except the specified one"""
    try:
        if 'message_history' in context.chat_data:
            for msg_id in context.chat_data['message_history']:
                if exclude_message_id and msg_id == exclude_message_id:
                    continue
                try:
                    await context.bot.delete_message(chat_id, msg_id)
                except Exception:
                    pass
            
            # Reset message history, keeping only the excluded message if specified
            context.chat_data['message_history'] = [exclude_message_id] if exclude_message_id else []
    except Exception as e:
        print(f"Error cleaning up messages for {chat_id}: {e}")

async def send_clean_message(context, chat_id, text, reply_markup=None, parse_mode="HTML"):
    """Send a message and clean up previous ones"""
    try:
        await cleanup_messages(context, chat_id)
        
        # Add small delay to prevent Telegram rate limits
        await asyncio.sleep(0.3)
        
        message = await context.bot.send_message(
            chat_id, 
            text, 
            parse_mode=parse_mode, 
            reply_markup=reply_markup
        )
        
        # Store message ID for future cleanup
        if 'message_history' not in context.chat_data:
            context.chat_data['message_history'] = []
        context.chat_data['message_history'].append(message.message_id)
        
        return message
    except Exception as e:
        print(f"Error sending clean message to {chat_id}: {e}")
        return None

async def edit_to_clean_message(context, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """Edit a message and clean up previous ones"""
    try:
        await cleanup_messages(context, chat_id, message_id)
        
        # Add small delay to prevent Telegram rate limits
        await asyncio.sleep(0.2)
        
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
        
        # Ensure this message is in history
        if 'message_history' not in context.chat_data:
            context.chat_data['message_history'] = []
        if message_id not in context.chat_data['message_history']:
            context.chat_data['message_history'].append(message_id)
    except Exception as e:
        print(f"Error editing clean message for {chat_id}: {e}")

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False, message_id=None):
    text = "<blockquote>🛠️ <b>پنل مدیریت ربات</b>\n\nاز طریق دکمه های زیر می توانید ربات را مدیریت کنید.</blockquote>"
    if edit:
        await edit_to_clean_message(context, ADMIN_ID, message_id, text, get_admin_keyboard())
    else:
        await send_clean_message(context, ADMIN_ID, text, get_admin_keyboard())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Skip anonymous admins (user IDs starting with -100)
    if str(chat_id).startswith('-100'):
        return
    
    # Don't delete admin messages
    if chat_id != ADMIN_ID:
        try:
            await update.message.delete()
        except:
            pass

    # Check if user is temporarily blocked
    if is_user_rate_limited(chat_id) and chat_id != ADMIN_ID:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT unblock_time FROM temp_blocked WHERE user_id = ?', (chat_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            unblock_time = result[0]
            time_left = unblock_time - time.time()
            hours_left = int(time_left // 3600)
            minutes_left = int((time_left % 3600) // 60)
            
            blocked_text = (
                "<blockquote>⏰ <b>شما به دلیل ارسال درخواست های زیاد موقتاً مسدود شده‌اید.</b>\n\n</blockquote>"
                f"<blockquote>⏳ زمان باقیمانده تا آزاد شدن: {hours_left} ساعت و {minutes_left} دقیقه\n\n</blockquote>"
                "<blockquote>لطفاً پس از این مدت مجدداً تلاش کنید.</blockquote>"
            )
            await send_clean_message(context, chat_id, blocked_text)
            return

    # Check if user needs to join channel
    if chat_id != ADMIN_ID and not await is_user_joined(chat_id, context):
        keyboard = [
            [InlineKeyboardButton(f"📢 {CHANNEL_NICKNAME}", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")]
        ]
        welcome_text = (
            f"<blockquote>👋 <b>سلام به ربات دانلود محتوای اینستاگرام خوش آمدید!</b>\n\n</blockquote>"
            f"<blockquote>🔒 برای استفاده از ربات، ابتدا باید در {CHANNEL_NICKNAME} عضو شوید.\n\n"
            f"پس از عضویت، روی دکمه «عضو شدم» کلیک کنید.</blockquote>"
        )
        await send_clean_message(
            context, chat_id, welcome_text, InlineKeyboardMarkup(keyboard)
        )
        return

    add_user(chat_id)

    if chat_id == ADMIN_ID:
        await send_admin_panel(update, context)
    else:
        welcome_text = (
            "<blockquote>👋 <b>سلام به ربات دانلود محتوای اینستاگرام خوش آمدید!</b>\n\n</blockquote>"
            "<blockquote>📥 این ربات به شما امکان دانلود پست‌ها، ویدیوها و ریلس‌های اینستاگرام را می‌دهد.\n\n</blockquote>"
            "<blockquote>✨ <b>نحوه استفاده:</b>\n</blockquote>"
            "<blockquote>1. لینک پست اینستاگرام را کپی کنید\n</blockquote>"
            "<blockquote>2. لینک را برای ربات ارسال کنید\n</blockquote>"
            "<blockquote>3. ربات محتوا را برای شما دانلود می‌کند\n\n</blockquote>"
            "<blockquote>📎 <b>مثال لینک معتبر:</b>\n"
            "https://www.instagram.com/p/Cxxxxxxxxxx/\n\n</blockquote>"
            "<blockquote>⚠️ توجه: لینک باید از پست‌های عمومی اینستاگرام باشد.</blockquote>"
        )
        await send_clean_message(context, chat_id, welcome_text, get_user_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Skip anonymous admins (user IDs starting with -100)
    if str(chat_id).startswith('-100'):
        return
    
    # Don't delete admin messages
    if chat_id != ADMIN_ID:
        try:
            await update.message.delete()
        except:
            pass
    
    help_text = (
        "<blockquote>📖 <b>راهنمای استفاده از ربات</b>\n\n</blockquote>"
        "<blockquote>🔹 این ربات برای دانلود محتوای اینستاگرام طراحی شده است.\n\n</blockquote>"
        "<blockquote>🔹 <b>نحوه استفاده:</b>\n"
        "1. لینک پست اینستاگرام را کپی کنید\n</blockquote>"
        "<blockquote>2. لینک را برای ربات ارسال کنید\n</blockquote>"
        "<blockquote>3. ربات محتوا را برای شما دانلود می‌کند\n\n</blockquote>"
        "<blockquote>🔹 <b>انواع محتوای قابل دانلود:</b>\n</blockquote>"
        "<blockquote>• 📷 عکس ها\n</blockquote>"
        "<blockquote>• 🎬 ویدیوها\n</blockquote>"
        "<blockquote>• 📹 ریلس ها\n</blockquote>"
        "<blockquote>• 🎞️ IGTV ها\n\n</blockquote>"
        "<blockquote>🔹 <b>محدودیت ها:</b>\n</blockquote>"
        "<blockquote>• پست باید عمومی باشد\n</blockquote>"
        "<blockquote>• پست های خصوصی قابل دانلود نیستند\n</blockquote>"
        "<blockquote>• اکانت های خصوصی قابل دسترسی نیستند\n\n</blockquote>"
        "<blockquote>🔹 <b>مثال لینک معتبر:</b>\n"
        "<code>https://www.instagram.com/p/Cxxxxxxxxxx/</code>\n\n</blockquote>"
        "<blockquote>برای شروع، یک لینک اینستاگرام ارسال کنید.</blockquote>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_main")],
        [InlineKeyboardButton("📥 شروع دانلود", callback_data="start_download")]
    ]
    await send_clean_message(
        context, update.effective_chat.id, help_text, InlineKeyboardMarkup(keyboard)
    )

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Skip anonymous admins (user IDs starting with -100)
    if str(chat_id).startswith('-100'):
        return
    
    # Don't delete admin messages
    if chat_id != ADMIN_ID:
        try:
            await update.message.delete()
        except:
            pass
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT join_date, download_count, last_download FROM users WHERE user_id = ?', (chat_id,))
    user = cursor.fetchone()
    conn.close()
    
    join_date = user[0] if user else "نامشخص"
    download_count = user[1] if user else 0
    last_download = user[2] if user and user[2] else "هیچ دانلودی ثبت نشده"
    
    profile_text = (
        "<blockquote>👤 <b>مشخصات کاربری</b>\n\n</blockquote>"
        f"🆔 شناسه کاربری: <code>{chat_id}</code>\n"
        f"📅 تاریخ عضویت: <code>{join_date}</code>\n"
        f"📥 تعداد دانلودها: <code>{download_count}</code>\n"
        f"🕒 آخرین دانلود: <code>{last_download}</code>\n\n"
        "✨ برای دانلود محتوا، لینک اینستاگرام را ارسال کنید."
    )
    
    keyboard = [
        [InlineKeyboardButton("📥 دانلود محتوا", callback_data="start_download")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_main")]
    ]
    
    await send_clean_message(
        context, chat_id, profile_text, InlineKeyboardMarkup(keyboard)
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    
    # Skip anonymous admins (user IDs starting with -100)
    if str(chat_id).startswith('-100'):
        return

    if data == "check_join":
        if await is_user_joined(chat_id, context):
            welcome_text = (
                "<blockquote>✅ <b>تبریک! شما با موفقیت عضو شدید.</b>\n\n</blockquote>"
                "👋 <b>سلام به ربات دانلود محتوای اینستاگرام خوش آمدید!</b>\n\n"
                "📥 این ربات به شما امکان دانلود پست‌ها، ویدیوها و ریلس‌های اینستاگرام را می‌دهد.\n\n"
                "✨ برای شروع، یک لینک اینستاگرام ارسال کنید."
            )
            await edit_to_clean_message(
                context, chat_id, message_id, welcome_text, get_user_keyboard()
            )
        else:
            await query.answer("❌ هنوز در کانال عضو نشدید! لطفا ابتدا عضو شوید سپس روی دکمه کلیک کنید.", show_alert=True)
        return

    if data == "my_profile":
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT join_date, download_count, last_download FROM users WHERE user_id = ?', (chat_id,))
        user = cursor.fetchone()
        conn.close()
        
        join_date = user[0] if user else "نامشخص"
        download_count = user[1] if user else 0
        last_download = user[2] if user and user[2] else "هیچ دانلودی ثبت نشده"
        
        profile_text = (
            "<blockquote>👤 <b>مشخصات کاربری</b>\n\n</blockquote>"
            f"🆔 شناسه کاربری: <code>{chat_id}</code>\n"
            f"📅 تاریخ عضویت: <code>{join_date}</code>\n"
            f"📥 تعداد دانلودها: <code>{download_count}</code>\n"
            f"🕒 آخرین دانلود: <code>{last_download}</code>\n\n"
            "✨ برای دانلود محتوا، لینک اینستاگرام را ارسال کنید."
        )
        
        keyboard = [
            [InlineKeyboardButton("📥 دانلود محتوا", callback_data="start_download")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_main")]
        ]
        
        await edit_to_clean_message(
            context, chat_id, message_id, profile_text, InlineKeyboardMarkup(keyboard)
        )
        return
        
    if data == "download_help":
        help_text = (
            "<blockquote>📖 <b>راهنمای استفاده از ربات</b>\n\n</blockquote>"
            "🔹 این ربات برای دانلود محتوای اینستاگرام طراحی شده است.\n\n"
            "🔹 <b>نحوه استفاده:</b>\n"
            "1. لینک پست اینستاگرام را کپی کنید\n"
            "2. لینک را برای ربات ارسال کنید\n"
            "3. ربات محتوا را برای شما دانلود می‌کند\n\n"
            "🔹 <b>انواع محتوای قابل دانلود:</b>\n"
            "• 📷 عکس ها\n"
            "• 🎬 ویدیوها\n"
            "• 📹 ریلس ها\n"
            "• 🎞️ IGTV ها\n\n"
            "🔹 <b>محدودیت ها:</b>\n"
            "• پست باید عمومی باشد\n"
            "• پست های خصوصی قابل دانلود نیستند\n"
            "• اکانت های خصوصی قابل دسترسی نیستند\n\n"
            "🔹 <b>مثال لینک معتبر:</b>\n"
            "<code>https://www.instagram.com/p/Cxxxxxxxxxx/</code>\n\n"
            "برای شروع، یک لینک اینستاگرام ارسال کنید."
        )
        keyboard = [
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_main")],
            [InlineKeyboardButton("📥 شروع دانلود", callback_data="start_download")]
        ]
        await edit_to_clean_message(
            context, chat_id, message_id, help_text, InlineKeyboardMarkup(keyboard)
        )
        return
        
    if data == "start_download":
        await edit_to_clean_message(
            context, chat_id, message_id,
            "<blockquote>📥 <b>برای دانلود، لینک پست اینستاگرام را ارسال کنید.</b>\n\n</blockquote>"
            "<blockquote>مثال لینک معتبر:\n"
            "<code>https://www.instagram.com/p/Cxxxxxxxxxx/</code></blockquote>",
            parse_mode="HTML"
        )
        return
        
    if data == "back_to_main":
        welcome_text = (
            "<blockquote>👋 <b>به ربات دانلود محتوای اینستاگرام خوش آمدید!</b>\n\n</blockquote>"
            "<blockquote>از طریق منوی زیر می‌توانید از امکانات ربات استفاده کنید.</blockquote>"
        )
        await edit_to_clean_message(
            context, chat_id, message_id, welcome_text, get_user_keyboard()
        )
        return

    if chat_id != ADMIN_ID:
        await query.answer("<blockquote>⛔️ فقط مدیران ربات به این بخش دسترسی دارند</blockquote>", show_alert=True)
        return

    if data == "stats":
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get user count
        cursor.execute('SELECT COUNT(*) FROM users')
        user_count = cursor.fetchone()[0]
        
        # Get blocked user count
        cursor.execute('SELECT COUNT(*) FROM blocked_users')
        blocked_count = cursor.fetchone()[0]
        
        # Get total downloads
        cursor.execute('SELECT SUM(download_count) FROM users')
        total_downloads = cursor.fetchone()[0] or 0
        
        # Get bot status
        bot_status = "🟢 روشن" if get_bot_status() == "on" else "🔴 خاموش"
        
        conn.close()
        
        stats_text = (
            f"<blockquote>📊 <b>آمار ربات</b>\n\n</blockquote>"
            f"👥 کاربران کل: <code>{user_count}</code>\n"
            f"🚫 کاربران مسدود: <code>{blocked_count}</code>\n"
            f"📥 کل دانلودها: <code>{total_downloads}</code>\n"
            f"🔧 وضعیت ربات: {bot_status}\n\n"
            f"🆔 شناسه شما: <code>{chat_id}</code>"
        )
        await edit_to_clean_message(
            context, chat_id, message_id, stats_text,
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back")]])
        )

    elif data == "broadcast":
        admin_states[chat_id] = "broadcast"
        await edit_to_clean_message(
            context, chat_id, message_id,
            "<blockquote>📢 <b>ارسال پیام همگانی</b>\n\n"
            "لطفا پیامی که می‌خواهید برای همه کاربران ارسال شود را وارد کنید:</blockquote>",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back")]])
        )

    elif data == "forward":
        admin_states[chat_id] = "forward"
        await edit_to_clean_message(
            context, chat_id, message_id,
            "🔁 <b>فوروارد همگانی</b>\n\n"
            "لطفا پیامی که می‌خواهید برای همه کاربران فوروارد شود را ارسال کنید:",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back")]])
        )

    elif data == "block":
        admin_states[chat_id] = "block"
        await edit_to_clean_message(
            context, chat_id, message_id,
            "🚫 <b>مسدود کردن کاربر</b>\n\n"
            "لطفا شناسه عددی کاربری که می‌خواهید مسدود کنید را ارسال کنید:",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back")]])
        )

    elif data == "unblock":
        admin_states[chat_id] = "unblock"
        await edit_to_clean_message(
            context, chat_id, message_id,
            "✅ <b>آزاد کردن کاربر</b>\n\n"
            "لطفا شناسه عددی کاربری که می‌خواهید آزاد کنید را ارسال کنید:",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back")]])
        )

    elif data == "bot_on":
        set_bot_status("on")
        await query.answer("✅ ربات روشن شد")
        await send_admin_panel(update, context, edit=True, message_id=message_id)

    elif data == "bot_off":
        set_bot_status("off")
        await query.answer("⛔️ ربات خاموش شد")
        await send_admin_panel(update, context, edit=True, message_id=message_id)

    elif data == "back":
        await send_admin_panel(update, context, edit=True, message_id=message_id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Skip anonymous admins (user IDs starting with -100)
    if str(chat_id).startswith('-100'):
        return
    
    text = update.message.text.strip() if update.message.text else None

    # Don't delete admin messages
    if chat_id != ADMIN_ID:
        try:
            await update.message.delete()
        except:
            pass

    # Check if user is blocked
    if is_user_blocked(chat_id):
        blocked_text = "<blockquote>⛔️ <b>شما توسط مدیریت مسدود شده‌اید.</b>\n\nدر صورت نیاز به پیگیری با پشتیبانی تماس بگیرید.</blockquote>"
        await send_clean_message(context, chat_id, blocked_text)
        return

    # Check if user is temporarily blocked
    if is_user_rate_limited(chat_id) and chat_id != ADMIN_ID:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT unblock_time FROM temp_blocked WHERE user_id = ?', (chat_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            unblock_time = result[0]
            time_left = unblock_time - time.time()
            hours_left = int(time_left // 3600)
            minutes_left = int((time_left % 3600) // 60)
            
            blocked_text = (
                "<blockquote>⏰ <b>شما به دلیل ارسال درخواست های زیاد موقتاً مسدود شده‌اید.</b>\n\n</blockquote>"
                f"⏳ زمان باقیمانده تا آزاد شدن: {hours_left} ساعت و {minutes_left} دقیقه\n\n"
                "لطفاً پس از این مدت مجدداً تلاش کنید."
            )
            await send_clean_message(context, chat_id, blocked_text)
            return

    if get_bot_status() != "on" and chat_id != ADMIN_ID:
        bot_off_text = "<blockquote>🔴 <b>ربات موقتاً غیرفعال است.</b>\n\nلطفاً بعداً تلاش کنید.</blockquote>"
        await send_clean_message(context, chat_id, bot_off_text)
        return

    # Check if user needs to join channel
    if chat_id != ADMIN_ID and not await is_user_joined(chat_id, context):
        keyboard = [
            [InlineKeyboardButton(f"📢 {CHANNEL_NICKNAME}", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")]
        ]
        welcome_text = (
            f"<blockquote>👋 <b>سلام به ربات دانلود محتوای اینستاگرام خوش آمدید!</b>\n\n</blockquote>"
            f"🔒 برای استفاده از ربات، ابتدا باید در {CHANNEL_NICKNAME} عضو شوید.\n\n"
            f"پس از عضویت، روی دکمه «عضو شدم» کلیк کنید."
        )
        await send_clean_message(
            context, chat_id, welcome_text, InlineKeyboardMarkup(keyboard)
        )
        return

    if chat_id == ADMIN_ID and chat_id in admin_states:
        state = admin_states.pop(chat_id)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id FROM users')
        users = [row[0] for row in cursor.fetchall()]
        conn.close()

        if state == "broadcast":
            success = 0
            fail = 0
            processing_msg = await send_clean_message(context, chat_id, "⏳ <b>در حال ارسال همگانی...</b>")
            
            # Add delay between messages to prevent rate limiting
            for i, uid in enumerate(users):
                try:
                    await context.bot.send_message(uid, text, parse_mode="HTML")
                    success += 1
                    
                    # Add delay every 20 messages to prevent flooding
                    if i % 20 == 0:
                        await asyncio.sleep(1)
                except Exception as e:
                    print(f"Failed to send to {uid}: {e}")
                    fail += 1
            
            # Clean up processing message
            await cleanup_messages(context, chat_id)
            
            await send_clean_message(
                context, chat_id,
                f"📊 <b>نتایج ارسال همگانی:</b>\n\n✅ موفق: {success}\n❌ ناموفق: {fail}"
            )
            return

        elif state == "forward":
            success = 0
            fail = 0
            processing_msg = await send_clean_message(context, chat_id, "⏳ <b>در حال فوروارد همگانی...</b>")
            
            # Add delay between messages to prevent rate limiting
            for i, uid in enumerate(users):
                try:
                    await context.bot.forward_message(uid, chat_id, update.message.message_id)
                    success += 1
                    
                    # Add delay every 20 messages to prevent flooding
                    if i % 20 == 0:
                        await asyncio.sleep(1)
                except Exception as e:
                    print(f"Failed to forward to {uid}: {e}")
                    fail += 1
            
            # Clean up processing message
            await cleanup_messages(context, chat_id)
            
            await send_clean_message(
                context, chat_id,
                f"📊 <b>نتایج فوروارد همگانی:</b>\n\n✅ موفق: {success}\n❌ ناموفق: {fail}"
            )
            return

        elif state == "block":
            try:
                user_id = int(text)
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('INSERT OR IGNORE INTO blocked_users (user_id) VALUES (?)', (user_id,))
                conn.commit()
                conn.close()
                await send_clean_message(context, chat_id, f"✅ <b>کاربر {user_id} مسدود شد.</b>")
            except (ValueError, TypeError):
                await send_clean_message(context, chat_id, "❌ <b>شناسه کاربری نامعتبر است.</b>")
            return

        elif state == "unblock":
            try:
                user_id = int(text)
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('DELETE FROM blocked_users WHERE user_id = ?', (user_id,))
                conn.commit()
                conn.close()
                await send_clean_message(context, chat_id, f"✅ <b>کاربر {user_id} آزاد شد.</b>")
            except (ValueError, TypeError):
                await send_clean_message(context, chat_id, "❌ <b>شناسه کاربری نامعتبر است.</b>")
            return

    if text and text.startswith("http") and "instagram.com" in text:
        # Check if user is rate limited (30-second cooldown)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT last_download FROM users WHERE user_id = ?', (chat_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            try:
                last_time = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
                time_diff = (datetime.now() - last_time).total_seconds()
                
                if time_diff < DOWNLOAD_COOLDOWN:
                    time_left = DOWNLOAD_COOLDOWN - time_diff
                    cooldown_text = (
                        f"<blockquote>⏰ <b>شما باید {int(time_left)} ثانیه دیگر صبر کنید قبل از ارسال درخواست جدید.</b>\n\n</blockquote>"
                        "<blockquote>لطفاً پس از این مدت مجدداً تلاش کنید.</blockquote>"
                    )
                    await send_clean_message(context, chat_id, cooldown_text)
                    conn.close()
                    return
            except ValueError:
                pass
        
        conn.close()

        # Check if user is rate limited (10 requests per hour)
        if is_user_rate_limited(chat_id) and chat_id != ADMIN_ID:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT unblock_time FROM temp_blocked WHERE user_id = ?', (chat_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                unblock_time = result[0]
                time_left = unblock_time - time.time()
                hours_left = int(time_left // 3600)
                minutes_left = int((time_left % 3600) // 60)
                
                blocked_text = (
                    "<blockquote>⏰ <b>شما به دلیل ارسال درخواست های زیاد موقتاً مسدود شده‌اید.</b>\n\n</blockquote>"
                    f"<blockquote>⏳ زمان باقیمانده تا آزاد شدن: {hours_left} ساعت و {minutes_left} دقیقه\n\n</blockquote>"
                    "<blockquote>لطفاً پس از این مدت مجدداً تلاش کنید.</blockquote>"
                )
                await send_clean_message(context, chat_id, blocked_text)
                return

        processing_msg = await send_clean_message(context, chat_id, "<blockquote>⏳ <b>در حال دریافت محتوا...</b>\n\nلطفاً کمی صبر کنید.</blockquote>")

        params = {"apikey": API_KEY, "type": "post", "url": text}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(FASTCREATE_API, params=params, timeout=30) as resp:
                    data = await resp.json()

            if data.get("ok") and data["result"].get("result"):
                first_item = data["result"]["result"][0]
                
                # Format caption as requested
                caption = (
                    "<blockquote>📥 <b>دانلود شده توسط ربات Instagram دانلودر</b>\n\n</blockquote>"
                    f"🔗 <b>لینک پست دانلود شده اینستاگرام:</b>\n"
                    f"<blockquote>{text}</blockquote>\n\n"
                    f"📝 <b>توضیحات:</b>\n"
                    f"<blockquote>{first_item.get('caption', '')}\n\n</blockquote>"
                    f"🤖 @{(await context.bot.get_me()).username}"
                )
                
                # Update user download count
                update_user_download(chat_id)

                # Clean up processing message before sending content
                await cleanup_messages(context, chat_id)
                
                if first_item.get("is_video") and first_item.get("video_url"):
                    await context.bot.send_video(
                        chat_id, 
                        video=first_item["video_url"], 
                        caption=caption,
                        parse_mode="HTML"
                    )
                elif first_item.get("image_url"):
                    await context.bot.send_photo(
                        chat_id,
                        photo=first_item["image_url"],
                        caption=caption,
                        parse_mode="HTML"
                    )
                else:
                    await send_clean_message(context, chat_id, "❌ <b>محتوایی برای دانلود یافت نشد.</b>")
                    
                # Send user panel again after download with a small delay
                await asyncio.sleep(1)
                welcome_text = (
                    "✅ <b>دانلود با موفقیت انجام شد!</b>\n\n"
                    "می‌توانید لینک دیگری ارسال کنید یا از منوی زیر استفاده کنید."
                )
                await send_clean_message(context, chat_id, welcome_text, get_user_keyboard())
            else:
                await send_clean_message(
                    context, chat_id,
                    "❌ <b>خطا در دریافت اطلاعات از اینستاگرام.</b>\n\n"
                    "ممکن است لینک نامعتبر باشد یا پست حذف شده است."
                )
        except Exception as e:
            print(f"Error: {e}")
            await send_clean_message(
                context, chat_id,
                "❌ <b>خطا در پردازش درخواست.</b>\n\n"
                "لطفاً بعداً مجدداً تلاش کنید یا لینک دیگری ارسال کنید."
            )
        finally:
            # Don't delete the processing message here as we already cleaned up
            pass

    else:
        if chat_id != ADMIN_ID:
            help_text = (
                "<blockquote>📝 <b>لطفاً یک لینک معتبر اینستاگرام ارسال کنید.</b>\n\n</blockquote>"
                "<blockquote>🔹 مثال لینک معتبر:\n</blockquote>"
                "<code>https://www.instagram.com/p/Cxxxxxxxxxx/</code>\n\n</blockquote>"
                "<blockquote>برای راهنمایی بیشتر از دکمه راهنما استفاده کنید.</blockquote>"
            )
            keyboard = [
                [InlineKeyboardButton("📖 راهنمای استفاده", callback_data="download_help")],
                [InlineKeyboardButton("👤 پشتیبانی", url="https://t.me/PlushPepeDesigner")]
            ]
            await send_clean_message(
                context, chat_id, help_text, InlineKeyboardMarkup(keyboard)
            )

def main():
    print("🤖 Starting bot...")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("me", profile_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    print("✅ Bot started successfully")
    app.run_polling()

if __name__ == "__main__":
    main()