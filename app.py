"""
====================================================
  SMS OTP PREMIUM TELEGRAM BOT — OPTIMIZED VERSION
  Features: Get Number, Balance, Refer & Earn,
  Support, Status, Full Admin Panel, API Integration
  
  OPTIMIZATIONS:
  - Admin cache (TTL 5 min) — DB hit কমানো
  - Channel membership cache (TTL 3 min)
  - Main menu cache per user
  - Faster broadcast (asyncio.gather + batching)
  - Concurrent DB queries where possible
====================================================
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from functools import lru_cache

import aiohttp
from bs4 import BeautifulSoup

from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

import database as db
from config import BOT_TOKEN, OWNER_ID, COUNTRY_CODES, OTP_GROUP_ID
from keyboards import (
    main_menu_keyboard,
    services_keyboard,
    countries_keyboard,
    balance_keyboard,
    support_keyboard,
    admin_panel_keyboard,
    settings_keyboard,
    category_management_keyboard,
    manage_numbers_keyboard,
    withdraw_action_keyboard,
    req_channels_keyboard,
    api_management_keyboard,
    api_system_keyboard,
    scraping_system_keyboard,
    scraper_action_keyboard,
    add_numbers_service_keyboard,
    broadcast_confirm_keyboard,
    channel_join_keyboard,
    number_assigned_keyboard_with_link,
)
from api_handler import start_api_polling, check_api_health

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================
# CONVERSATION STATES
# ============================================================
(
    STATE_ADD_NUM_WAITING_NUMBERS,
    STATE_ADD_NUM_WAITING_CATEGORY,
    STATE_ADD_NUM_WAITING_PER_USER,
    STATE_ADD_NUM_WAITING_RATE,
    STATE_WITHDRAW_WAITING_ADDRESS,
    STATE_WITHDRAW_WAITING_AMOUNT,
    STATE_SETTING_WAITING_VALUE,
    STATE_ADD_ADMIN_WAITING_ID,
    STATE_REMOVE_ADMIN_WAITING_ID,
    STATE_BROADCAST_WAITING_MSG,
    STATE_ADD_CATEGORY_WAITING_NAME,
    STATE_ADD_CHANNEL_WAITING_LINK,
    STATE_API_WAITING_NAME,
    STATE_API_WAITING_URL,
    STATE_API_WAITING_KEY,
    STATE_SCRAPER_WAITING_NAME,
    STATE_SCRAPER_WAITING_URL,
    STATE_SCRAPER_WAITING_USER,
    STATE_SCRAPER_WAITING_PASS,
) = range(19)

# ============================================================
# ⚡ CACHE LAYER — DB call কমাতে
# ============================================================

# Admin cache: {user_id: (is_admin: bool, timestamp)}
_admin_cache: dict[int, tuple[bool, float]] = {}
ADMIN_CACHE_TTL = 300  # 5 মিনিট

# Channel membership cache: {user_id: (is_member: bool, timestamp)}
_channel_cache: dict[int, tuple[bool, float]] = {}
CHANNEL_CACHE_TTL = 180  # 3 মিনিট

# ⚡ Bot username — startup এ একবার cache করা হবে
_bot_username: str = ""

# ============================================================
# SCRAPER STORAGE  {name: {url, username, password, task, session}}
# ============================================================
_scrapers: dict = {}  # name → {url, user, password, task, running}

def _invalidate_admin_cache(user_id: int = None):
    """Admin add/remove হলে cache clear করো।"""
    if user_id:
        _admin_cache.pop(user_id, None)
    else:
        _admin_cache.clear()

def _invalidate_channel_cache():
    """Channel change হলে সব cache clear।"""
    _channel_cache.clear()

# ============================================================
# HELPERS
# ============================================================

def InlineKeyBoardBack():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")]])

def InlineKeyboardMarkupBack():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")]])

def detect_country(number: str):
    clean = re.sub(r'[\s\-\+]', '', number)
    for code in sorted(COUNTRY_CODES.keys(), key=lambda x: -len(x)):
        if clean.startswith(code):
            info = COUNTRY_CODES[code]
            return code, info["name"], info["flag"]
    return None, "Unknown", "🌍"

def parse_numbers_from_text(text: str) -> list:
    lines = text.strip().splitlines()
    numbers = []
    for line in lines:
        line = line.strip()
        if line and re.search(r'\d{6,}', line):
            numbers.append(line)
    return numbers

async def check_channel_membership(bot: Bot, user_id: int, channels: list) -> bool:
    """⚡ Cache সহ channel membership check।"""
    now = time.monotonic()
    cached = _channel_cache.get(user_id)
    if cached:
        result, ts = cached
        if now - ts < CHANNEL_CACHE_TTL:
            return result

    # Cache miss — actual check করো
    for ch in channels:
        ch = dict(ch) if not isinstance(ch, dict) else ch
        channel_id   = ch.get("channel_id") or ""
        channel_link = ch.get("channel_link") or ""
        ch_db_id     = ch.get("id")

        # channel_id না থাকলে username দিয়ে resolve করো
        if not channel_id:
            try:
                username = channel_link.strip().rstrip('/').split('/')[-1]
                if not username.startswith("@"):
                    username = "@" + username
                chat = await bot.get_chat(username)
                channel_id = str(chat.id)
                if ch_db_id:
                    await db.update_channel_id(ch_db_id, channel_id)
            except Exception as e:
                logger.warning(f"Could not resolve channel {channel_link}: {e}")
                _channel_cache[user_id] = (False, now)
                return False

        # membership check
        try:
            member = await bot.get_chat_member(chat_id=int(channel_id), user_id=user_id)
            if member.status in ["left", "kicked", "banned"]:
                _channel_cache[user_id] = (False, now)
                return False
        except Exception as e:
            logger.warning(f"Could not check membership for channel {channel_id}: {e}")
            _channel_cache[user_id] = (False, now)
            return False

    _channel_cache[user_id] = (True, now)
    return True

async def is_user_admin_or_owner(user_id: int) -> bool:
    """⚡ Cache সহ admin check — বারবার DB hit নয়।"""
    if user_id == OWNER_ID:
        return True

    now = time.monotonic()
    cached = _admin_cache.get(user_id)
    if cached:
        result, ts = cached
        if now - ts < ADMIN_CACHE_TTL:
            return result

    # Cache miss
    result = await db.is_admin(user_id)
    _admin_cache[user_id] = (result, now)
    return result

async def get_main_menu(user_id: int):
    is_adm = await is_user_admin_or_owner(user_id)
    return main_menu_keyboard(is_adm)

# ============================================================
# /start
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    full_name = user.full_name or ""

    referred_by = None
    if context.args:
        try:
            ref_id = int(context.args[0])
            if ref_id != user_id:
                referred_by = ref_id
        except ValueError:
            pass

    # ⚡ existing user check + channel check কে concurrent করো
    existing_task = asyncio.create_task(db.get_user(user_id))
    channels_task = asyncio.create_task(db.get_required_channels())
    existing, channels = await asyncio.gather(existing_task, channels_task)

    if not existing:
        await db.add_user(user_id, username, full_name, referred_by)
        if referred_by:
            ref_amount = float(await db.get_setting("referral_amount") or "0.005")
            await db.update_user_balance(referred_by, ref_amount)
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 <b>New Referral!</b>\n\n👤 <b>{full_name}</b> joined using your link.\n💰 <b>+${ref_amount}</b> added to your balance!",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    if channels:
        if not await check_channel_membership(context.bot, user_id, channels):
            await update.message.reply_text(
                "📢 <b>Please join the required channels to use this bot!</b>\n\nAfter joining, tap <b>Verify</b> below.",
                parse_mode=ParseMode.HTML,
                reply_markup=channel_join_keyboard(
                    [dict(ch) if not isinstance(ch, dict) else ch for ch in channels]
                )
            )
            return

    await update.message.reply_text(
        "⚡ <b>Fast delivery</b>\n🔒 <b>Secure numbers</b>\n♻️ <b>Change anytime</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=await get_main_menu(user_id)
    )

# ============================================================
# VERIFY CHANNEL JOIN
# ============================================================

async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    # Cache invalidate করো — নতুন করে check করতে হবে
    _channel_cache.pop(user_id, None)
    channels = await db.get_required_channels()
    if await check_channel_membership(context.bot, user_id, channels):
        await query.message.reply_text(
            "✅ <b>Verified! Welcome!</b>\n\n⚡ Fast delivery\n🔒 Secure numbers\n♻️ Change anytime",
            parse_mode=ParseMode.HTML,
            reply_markup=await get_main_menu(user_id)
        )
        await query.message.delete()
    else:
        await query.answer("❌ You haven't joined all channels yet!", show_alert=True)

# ============================================================
# GET NUMBER
# ============================================================

async def handle_get_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = await db.get_categories_with_numbers()
    if not cats:
        await update.message.reply_text(
            "⚠️ <b>No service available right now.</b>\n\nPlease check back later.",
            parse_mode=ParseMode.HTML
        )
        return
    cat_list = [{"name": c["name"], "emoji": c["emoji"]} for c in cats]
    await update.message.reply_text(
        "⚙️ <b>Select a Service:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=services_keyboard(cat_list)
    )

# ============================================================
# BALANCE
# ============================================================

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # ⚡ Concurrent fetch
    balance, min_withdraw = await asyncio.gather(
        db.get_user_balance(user_id),
        db.get_setting("min_withdraw")
    )
    min_withdraw = float(min_withdraw or "0.5")
    await update.message.reply_text(
        f"💰 <b>Balance</b>\n\n💳 <b>Current balance:</b> {balance}$\n📉 <b>Minimum withdraw:</b> {min_withdraw}$\n\nChoose a withdrawal method below:",
        parse_mode=ParseMode.HTML,
        reply_markup=balance_keyboard()
    )

# ============================================================
# REFER AND EARN
# ============================================================

async def handle_refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    try:
        # ⚡ Concurrent DB calls
        user_row, ref_amount_str, balance = await asyncio.gather(
            db.get_user(user_id),
            db.get_setting("referral_amount"),
            db.get_user_balance(user_id),
        )

        # user না থাকলে add করো
        if not user_row:
            await db.add_user(user_id, user.username or "", user.full_name or "")
            user_row = await db.get_user(user_id)

        ref_amount_str = ref_amount_str or "0.005"

        # ⚡ Cached bot username — get_me() আর call হবে না
        username = _bot_username or context.bot.username or "bot"
        ref_link = f"https://t.me/{username}?start={user_id}"

        ref_count = int(user_row.get("referral_count", 0) or 0) if user_row else 0
        ref_earnings = round(ref_count * float(ref_amount_str), 6)

        await update.message.reply_text(
            f"👥 <b>Refer &amp; Earn</b>\n\n"
            f"🔗 <b>Your referral link:</b>\n{ref_link}\n\n"
            f"📈 <b>Total referrals:</b> {ref_count}\n"
            f"💵 <b>Referral earnings:</b> {ref_earnings}$\n"
            f"➕ <b>Per referral:</b> {ref_amount_str}$\n\n"
            f"💳 <b>Your current balance:</b> {balance}$",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"handle_refer error user={user_id}: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ <b>কিছু একটা সমস্যা হয়েছে।</b> আবার চেষ্টা করুন।",
            parse_mode=ParseMode.HTML
        )


async def handle_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_username = await db.get_setting("support_username") or "@support"
    await update.message.reply_text(
        "💬 <b>Support</b>\n\nClick the button below to contact support.",
        parse_mode=ParseMode.HTML,
        reply_markup=support_keyboard(support_username)
    )

# ============================================================
# STATUS
# ============================================================

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = await db.get_status_summary()
    if not summary:
        await update.message.reply_text("📊 <b>Status</b>\n\n❌ No numbers available currently.", parse_mode=ParseMode.HTML)
        return
    lines = []
    for row in summary:
        lines.append(f"{row['category_name']} | {row['country_flag']} {row['country_name']} | {row['available_numbers']}")
    await update.message.reply_text("📊 <b>Status</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)

# ============================================================
# ADMIN PANEL
# ============================================================

async def handle_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_admin_or_owner(user_id):
        await update.message.reply_text("❌ Access denied.")
        return
    live_users = await db.count_live_users()
    await update.message.reply_text(
        f"🛠 <b>Admin Panel</b> [Live Users: {live_users}]",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )

# ============================================================
# WITHDRAW FLOW
# ============================================================

async def withdraw_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.replace("withdraw_", "")
    context.user_data["withdraw_method"] = method.capitalize()
    context.user_data["withdraw_state"] = STATE_WITHDRAW_WAITING_ADDRESS
    await query.edit_message_text(
        f"💳 <b>Withdraw via {method.capitalize()}</b>\n\n📨 Please send your <b>{method.capitalize()} account number / address:</b>",
        parse_mode=ParseMode.HTML
    )

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await query.message.reply_text("🏠 <b>Main Menu</b>", parse_mode=ParseMode.HTML, reply_markup=await get_main_menu(user_id))
    await query.message.delete()

async def handle_withdraw_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("withdraw_state")

    if state == STATE_WITHDRAW_WAITING_ADDRESS:
        context.user_data["withdraw_address"] = update.message.text.strip()
        context.user_data["withdraw_state"] = STATE_WITHDRAW_WAITING_AMOUNT
        min_w = await db.get_setting("min_withdraw") or "0.5"
        await update.message.reply_text(
            f"💵 <b>Enter the amount you want to withdraw:</b>\n\n📉 Minimum: <b>${min_w}</b>",
            parse_mode=ParseMode.HTML
        )

    elif state == STATE_WITHDRAW_WAITING_AMOUNT:
        try:
            amount = float(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ <b>Invalid amount. Please enter a number.</b>", parse_mode=ParseMode.HTML)
            return

        # ⚡ Concurrent fetch
        min_w_str, balance = await asyncio.gather(
            db.get_setting("min_withdraw"),
            db.get_user_balance(user_id)
        )
        min_w = float(min_w_str or "0.5")

        if amount < min_w:
            await update.message.reply_text(f"❌ <b>Amount too low!</b> Minimum is <b>${min_w}</b>", parse_mode=ParseMode.HTML)
            return
        if amount > balance:
            await update.message.reply_text(f"❌ <b>Insufficient balance!</b> Your balance: <b>${balance}</b>", parse_mode=ParseMode.HTML)
            return

        method = context.user_data.get("withdraw_method", "Unknown")
        address = context.user_data.get("withdraw_address", "")
        user = update.effective_user

        await db.create_withdraw_request(user_id, user.username or "", user.full_name or "", method, address, amount)
        context.user_data["withdraw_state"] = None

        await update.message.reply_text(
            f"✅ <b>Withdraw Request Submitted!</b>\n\n💳 <b>Method:</b> {method}\n📨 <b>Address:</b> {address}\n💵 <b>Amount:</b> ${amount}\n\n⏳ Please wait for admin approval.",
            parse_mode=ParseMode.HTML,
            reply_markup=await get_main_menu(user_id)
        )

# ============================================================
# SERVICE / COUNTRY / NUMBER CALLBACKS
# ============================================================

async def service_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service_name = query.data.replace("service_", "")
    context.user_data["selected_service"] = service_name
    countries = await db.get_countries_for_category(service_name)
    if not countries:
        await query.edit_message_text("⚠️ <b>No numbers available for this service right now.</b>", parse_mode=ParseMode.HTML)
        return
    country_list = [dict(c) for c in countries]
    await query.edit_message_text(
        f"💥 <b>Select country for {service_name}:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=countries_keyboard(country_list, service_name)
    )

async def back_to_services_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cats = await db.get_categories_with_numbers()
    if not cats:
        await query.edit_message_text("⚠️ No service available right now.")
        return
    cat_list = [{"name": c["name"], "emoji": c["emoji"]} for c in cats]
    await query.edit_message_text("⚙️ <b>Select a Service:</b>", parse_mode=ParseMode.HTML, reply_markup=services_keyboard(cat_list))

async def country_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    parts = query.data.replace("country_", "").split("_", 1)
    batch_id = int(parts[0])
    category_name = parts[1] if len(parts) > 1 else ""
    context.user_data["selected_service"] = category_name
    context.user_data["selected_batch"] = batch_id

    # ⚡ Concurrent fetch
    number_row, batches = await asyncio.gather(
        db.get_next_number(batch_id),
        db.get_all_batches()
    )

    if not number_row:
        await query.edit_message_text("⚠️ <b>No numbers available for this country.</b>", parse_mode=ParseMode.HTML)
        return

    batch = next((b for b in batches if b["id"] == batch_id), None)
    if not batch:
        await query.edit_message_text("❌ Error fetching batch info.")
        return

    country_name = batch["country_name"]
    country_flag = batch["country_flag"]

    await db.assign_number_to_user(user_id, number_row["id"], batch_id, category_name, country_name, country_flag)

    otp_link = await db.get_setting("otp_group_link") or "https://t.me/otp_group"
    kb = await number_assigned_keyboard_with_link(number_row["number"], country_flag, otp_link)

    await query.edit_message_text(
        f"{country_flag} <b>{country_name} Number Assigned:</b>\n\n⏳ <b>Waiting for OTP...</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

async def change_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    assignment = await db.get_user_assignment(user_id)
    if not assignment:
        await query.answer("❌ No active assignment found.", show_alert=True)
        return

    batch_id = assignment["batch_id"]
    country_name = assignment["country_name"]
    country_flag = assignment["country_flag"]
    category_name = assignment["category_name"]

    await db.release_user_assignment(user_id)

    number_row = await db.get_next_number(batch_id)
    if not number_row:
        await query.edit_message_text("⚠️ <b>No more numbers available for this country.</b>", parse_mode=ParseMode.HTML)
        return

    await db.assign_number_to_user(user_id, number_row["id"], batch_id, category_name, country_name, country_flag)

    otp_link = await db.get_setting("otp_group_link") or "https://t.me/otp_group"
    kb = await number_assigned_keyboard_with_link(number_row["number"], country_flag, otp_link)

    await query.edit_message_text(
        f"{country_flag} <b>{country_name} Number Assigned:</b>\n\n⏳ <b>Waiting for OTP...</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

async def change_country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    assignment = await db.get_user_assignment(user_id)
    service = assignment["category_name"] if assignment else context.user_data.get("selected_service")
    await db.release_user_assignment(user_id)

    if service:
        countries = await db.get_countries_for_category(service)
        country_list = [dict(c) for c in countries]
        await query.edit_message_text(
            f"💥 <b>Select country for {service}:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=countries_keyboard(country_list, service)
        )
    else:
        await back_to_services_callback(update, context)

async def copy_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback — only fires when CopyTextButton is NOT available (PTB < 21.1)."""
    query = update.callback_query
    safe = query.data[len("copy_number_"):]
    number = safe.replace("PLUS", "+")
    await query.answer(f"📋 {number}", show_alert=False)
    try:
        await query.message.reply_text(f"<code>{number}</code>", parse_mode=ParseMode.HTML)
    except Exception:
        pass

# ============================================================
# ⚡ OPTIMIZED BROADCAST — Batch + concurrent
# ============================================================

async def do_broadcast(bot: Bot, message, query=None):
    users = await db.get_all_users()
    total = len(users)
    success = 0
    failed = 0
    BATCH_SIZE = 25  # একসাথে 25 জনকে পাঠাও

    if query:
        try:
            await query.edit_message_text(
                f"📢 <b>Broadcast Started...</b>\n\n👥 Target Users: {total}\n✅ Success: 0\n❌ Failed: 0\n\n<i>Please wait...</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    async def send_one(user):
        try:
            if hasattr(message, 'text') and message.text:
                await bot.send_message(chat_id=user["user_id"], text=message.text, parse_mode=ParseMode.HTML)
            elif hasattr(message, 'photo') and message.photo:
                await bot.send_photo(chat_id=user["user_id"], photo=message.photo[-1].file_id, caption=message.caption or "")
            elif hasattr(message, 'video') and message.video:
                await bot.send_video(chat_id=user["user_id"], video=message.video.file_id, caption=message.caption or "")
            else:
                await bot.copy_message(chat_id=user["user_id"], from_chat_id=message.chat_id, message_id=message.message_id)
            return True
        except Exception:
            return False

    # Batch করে পাঠাও — Telegram rate limit মানতে
    for i in range(0, total, BATCH_SIZE):
        batch = users[i:i + BATCH_SIZE]
        results = await asyncio.gather(*[send_one(u) for u in batch], return_exceptions=True)
        for r in results:
            if r is True:
                success += 1
            else:
                failed += 1
        await asyncio.sleep(0.5)  # batch এর মধ্যে ছোট delay

    if query:
        try:
            await query.edit_message_text(
                f"📢 <b>Broadcast Complete!</b>\n\n👥 Target Users: {total}\n✅ Success: {success}\n❌ Failed: {failed}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkupBack()
            )
        except Exception:
            pass

# ============================================================
# ADD NUMBERS — STEP HANDLERS
# ============================================================

async def _add_numbers_step1(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    numbers = parse_numbers_from_text(text)
    if not numbers:
        await update.message.reply_text("❌ No valid numbers found. Please send again.")
        return

    context.user_data["add_num_numbers"] = numbers
    country_code, country_name, country_flag = detect_country(numbers[0])
    context.user_data["add_num_country_code"] = country_code
    context.user_data["add_num_country_name"] = country_name
    context.user_data["add_num_country_flag"] = country_flag
    context.user_data["add_num_step"] = "waiting_category"

    categories = await db.get_all_categories()
    cat_list = [{"name": c["name"], "emoji": c["emoji"]} for c in categories]

    await update.message.reply_text(
        f"✅ Detected <b>{len(numbers)}</b> numbers.\n"
        f"Detected country: {country_flag} <b>{country_name}</b> (+{country_code})\n\n"
        f"<b>Step 2:</b> Select a category for these numbers:",
        parse_mode=ParseMode.HTML,
        reply_markup=add_numbers_service_keyboard(cat_list)
    )

async def _add_numbers_step3(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        per_user = int(text)
    except ValueError:
        await update.message.reply_text("❌ Please send a valid integer. Example: 1")
        return
    context.user_data["add_num_per_user"] = per_user
    context.user_data["add_num_step"] = "waiting_rate"
    await update.message.reply_text(
        "📊 <b>Step 4:</b> Send rate per successful OTP (USD).\n"
        "Example: 0.0001\n\n"
        "<i>(If you set 0, OTPs will not give any reward.)</i>",
        parse_mode=ParseMode.HTML
    )

async def _add_numbers_step4(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        rate = float(re.sub(r'[^\d.]', '', text))
    except ValueError:
        await update.message.reply_text("❌ Please send a valid number (e.g. 0.0007).")
        return

    numbers       = context.user_data.get("add_num_numbers", [])
    category_name = context.user_data.get("add_num_category", "")
    country_code  = context.user_data.get("add_num_country_code", "")
    country_name  = context.user_data.get("add_num_country_name", "Unknown")
    country_flag  = context.user_data.get("add_num_country_flag", "🌍")
    per_user      = context.user_data.get("add_num_per_user", 1)

    cat = await db.get_category_by_name(category_name)
    if not cat:
        await update.message.reply_text("❌ Category not found. Please start again.")
        context.user_data.clear()
        return

    await db.add_number_batch(
        country_code, country_name, country_flag,
        cat["id"], numbers, per_user, rate
    )

    for key in ["admin_flow", "add_num_step", "add_num_numbers", "add_num_category",
                "add_num_country_code", "add_num_country_name", "add_num_country_flag", "add_num_per_user"]:
        context.user_data.pop(key, None)

    summary = (
        f"✅ <b>Numbers Added Successfully!</b>\n\n"
        f"{country_flag} <b>{country_name}</b> | {category_name}\n"
        f"📦 Total: <b>{len(numbers)}</b>\n"
        f"👤 Per user: <b>{per_user}</b>\n"
        f"💵 Rate: <b>{rate}$</b>\n\n"
        f"Do you want to broadcast this new stock to all users?"
    )
    context.user_data["broadcast_message_text"] = summary
    await update.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=broadcast_confirm_keyboard())
# ============================================================
# GENERAL TEXT INPUT HANDLER
# ============================================================

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    admin_flow = context.user_data.get("admin_flow")

    # ── WITHDRAW FLOW (যেকোনো user)
    withdraw_state = context.user_data.get("withdraw_state")
    if withdraw_state in (STATE_WITHDRAW_WAITING_ADDRESS, STATE_WITHDRAW_WAITING_AMOUNT):
        await handle_withdraw_input(update, context)
        return

    # ── Admin না হলে admin flow handle করবে না — simply ignore
    if not await is_user_admin_or_owner(user_id):
        # Unknown text — main menu দেখাও
        await update.message.reply_text(
            "⚡ <b>Please use the menu buttons below.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=await get_main_menu(user_id)
        )
        return

    # ── ADD NUMBERS STEPS
    if admin_flow == "add_numbers":
        step = context.user_data.get("add_num_step")
        if step == "waiting_numbers":
            await _add_numbers_step1(update, context, text)
            return
        elif step == "waiting_per_user":
            await _add_numbers_step3(update, context, text)
            return
        elif step == "waiting_rate":
            await _add_numbers_step4(update, context, text)
            return

    # ── ADD CATEGORY
    if admin_flow == "add_category":
        name = text.upper()
        result = await db.add_category(name)
        context.user_data.pop("admin_flow", None)
        if result:
            await update.message.reply_text(f"✅ <b>Category '{name}' added!</b>", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
        else:
            await update.message.reply_text("❌ Category already exists or error occurred.")
        return

    # ── SETTINGS
    if admin_flow == "setting":
        setting_key = context.user_data.get("setting_key")
        await db.set_setting(setting_key, text)
        context.user_data.pop("admin_flow", None)
        context.user_data.pop("setting_key", None)
        await update.message.reply_text("✅ <b>Setting updated!</b>", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
        return

    # ── ADD ADMIN
    if admin_flow == "add_admin":
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Send a numeric ID.")
            return
        await db.add_admin(new_admin_id)
        _invalidate_admin_cache(new_admin_id)  # ⚡ Cache update
        context.user_data.pop("admin_flow", None)
        await update.message.reply_text(f"✅ <b>User <code>{new_admin_id}</code> added as admin!</b>", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
        return

    # ── REMOVE ADMIN
    if admin_flow == "remove_admin":
        try:
            rm_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
        await db.remove_admin(rm_admin_id)
        _invalidate_admin_cache(rm_admin_id)  # ⚡ Cache update
        context.user_data.pop("admin_flow", None)
        await update.message.reply_text(f"✅ <b>User <code>{rm_admin_id}</code> removed from admin.</b>", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
        return

    # ── ADD CHANNEL
    if admin_flow == "add_channel":
        channel_id = None
        try:
            username = text.strip().rstrip('/').split('/')[-1]
            chat = await context.bot.get_chat(f"@{username}")
            channel_id = str(chat.id)
        except Exception:
            pass
        await db.add_required_channel(text, channel_id)
        _invalidate_channel_cache()  # ⚡ Cache clear
        context.user_data.pop("admin_flow", None)
        await update.message.reply_text(
            f"✅ <b>Channel added:</b> {text}\n{'✅ ID: ' + channel_id if channel_id else '⚠️ Bot must be admin in channel!'}",
            parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard()
        )
        return

    # ── BROADCAST
    if admin_flow == "broadcast":
        context.user_data["broadcast_message"] = update.message
        context.user_data.pop("admin_flow", None)
        await update.message.reply_text("📢 <b>Ready to broadcast!</b>\n\nSend to all users?", parse_mode=ParseMode.HTML, reply_markup=broadcast_confirm_keyboard())
        return

    # ── ADD API
    if admin_flow == "add_api":
        step = context.user_data.get("api_step")
        if step == "name":
            context.user_data["api_name"] = text
            context.user_data["api_step"] = "url"
            await update.message.reply_text("Step 2: Send the <b>API URL</b>:", parse_mode=ParseMode.HTML)
        elif step == "url":
            context.user_data["api_url"] = text
            context.user_data["api_step"] = "key"
            await update.message.reply_text("Step 3: Send the <b>API Key</b>:", parse_mode=ParseMode.HTML)
        elif step == "key":
            api_name = context.user_data.get("api_name")
            api_url  = context.user_data.get("api_url")
            is_healthy = await check_api_health(api_url, text)
            result = await db.add_api(api_name, api_url, text)
            for k in ["admin_flow", "api_step", "api_name", "api_url"]:
                context.user_data.pop(k, None)
            status = "✅ API is working!" if is_healthy else "⚠️ API added but health check failed."
            if result:
                await update.message.reply_text(f"✅ <b>API '{api_name}' added!</b>\n{status}", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
            else:
                await update.message.reply_text("❌ API name already exists.", reply_markup=admin_panel_keyboard())
        return

    # ── ADD SCRAPER
    if admin_flow == "add_scraper":
        step = context.user_data.get("scraper_step")
        if step == "name":
            context.user_data["scraper_name"] = text.strip()
            context.user_data["scraper_step"] = "url"
            await update.message.reply_text(
                "🕷 <b>Step 2:</b> Website এর <b>Login URL</b> দিন:\n(যেমন: http://2.59.169.96/ints/login)",
                parse_mode=ParseMode.HTML
            )
        elif step == "url":
            context.user_data["scraper_url"] = text.strip()
            context.user_data["scraper_step"] = "user"
            await update.message.reply_text("🕷 <b>Step 3:</b> <b>Username</b> দিন:", parse_mode=ParseMode.HTML)
        elif step == "user":
            context.user_data["scraper_user"] = text.strip()
            context.user_data["scraper_step"] = "pass"
            await update.message.reply_text("🕷 <b>Step 4:</b> <b>Password</b> দিন:", parse_mode=ParseMode.HTML)
        elif step == "pass":
            s_name = context.user_data.get("scraper_name")
            s_url  = context.user_data.get("scraper_url")
            s_user = context.user_data.get("scraper_user")
            s_pass = text.strip()
            _scrapers[s_name] = {
                "url": s_url, "user": s_user, "password": s_pass,
                "running": False, "task": None
            }
            # Auto-start
            otp_group_id = os.getenv("OTP_GROUP_ID", "") or OTP_GROUP_ID
            _start_scraper_task(s_name, context.bot, otp_group_id)
            for k in ["admin_flow", "scraper_step", "scraper_name", "scraper_url", "scraper_user"]:
                context.user_data.pop(k, None)
            await update.message.reply_text(
                f"✅ <b>Scraper '{s_name}' added and started!</b>\n\n"
                f"🌐 URL: {s_url}\n"
                f"👤 User: {s_user}\n"
                f"🔄 Refreshing every 2 seconds...",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_keyboard()
            )
        return

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    admin_flow     = context.user_data.get("admin_flow")
    withdraw_state = context.user_data.get("withdraw_state")

    # Withdraw flow — যেকোনো user
    if withdraw_state in (STATE_WITHDRAW_WAITING_ADDRESS, STATE_WITHDRAW_WAITING_AMOUNT):
        await handle_withdraw_input(update, context)
        return

    # Admin flow — শুধু admin
    if admin_flow:
        await handle_text_input(update, context)
        return

    # ── Normal menu buttons — সব user
    if "GET NUMBER" in text:
        await handle_get_number(update, context)
    elif "BALANCE" in text:
        await handle_balance(update, context)
    elif "REFER AND EARN" in text:
        await handle_refer(update, context)
    elif "SUPPORT" in text:
        await handle_support(update, context)
    elif "STATUS" in text:
        await handle_status(update, context)
    elif "ADMIN PANEL" in text:
        await handle_admin_panel(update, context)

# ============================================================
# DOCUMENT UPLOAD
# ============================================================

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_admin_or_owner(user_id):
        return

    admin_flow = context.user_data.get("admin_flow")
    add_num_step = context.user_data.get("add_num_step")
    if admin_flow != "add_numbers" or add_num_step != "waiting_numbers":
        return

    doc = update.message.document
    if not doc.file_name.endswith((".txt", ".csv")):
        await update.message.reply_text("❌ Please send a .txt or .csv file.")
        return

    file = await context.bot.get_file(doc.file_id)
    content = await file.download_as_bytearray()
    text = content.decode("utf-8", errors="ignore")
    await _add_numbers_step1(update, context, text)

# ============================================================
# PHOTO/VIDEO FOR BROADCAST
# ============================================================

async def handle_media_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_admin_or_owner(user_id):
        return
    if context.user_data.get("admin_flow") != "broadcast":
        return
    context.user_data["broadcast_message"] = update.message
    context.user_data.pop("admin_flow", None)
    await update.message.reply_text("📢 <b>Media ready to broadcast!</b>\n\nSend to all users?", parse_mode=ParseMode.HTML, reply_markup=broadcast_confirm_keyboard())

# ============================================================
# ADMIN CALLBACK HANDLER
# ============================================================

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not await is_user_admin_or_owner(user_id):
        await query.answer("❌ Access denied.", show_alert=True)
        return

    data = query.data

    if data == "back_to_admin_panel":
        await query.answer()
        live_users = await db.count_live_users()
        await query.edit_message_text(
            f"🛠 <b>Admin Panel</b> [Live Users: {live_users}]",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard()
        )

    elif data == "admin_add_numbers":
        await query.answer()
        categories = await db.get_all_categories()
        if not categories:
            await query.edit_message_text("❌ No categories found. Please add a category first.", reply_markup=InlineKeyBoardBack())
            return
        context.user_data["admin_flow"] = "add_numbers"
        context.user_data["add_num_step"] = "waiting_numbers"
        for key in ["add_num_numbers", "add_num_category", "add_num_country_code",
                    "add_num_country_name", "add_num_country_flag", "add_num_per_user"]:
            context.user_data.pop(key, None)
        await query.edit_message_text(
            "➕ <b>Add Numbers</b>\n\n"
            "<b>Step 1:</b> Send all phone numbers.\n"
            "• Type them (one per line), OR\n"
            "• Upload a <b>.txt</b> or <b>.csv</b> file (one per line)\n\n"
            "Example:\n"
            "+8801XXXXXXXXX\n"
            "8801YYYYYYYYY\n"
            "+8801ZZZZZZZZZ",
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("addnum_cat_"):
        await query.answer()
        category_name = data.replace("addnum_cat_", "")
        context.user_data["add_num_category"] = category_name
        context.user_data["admin_flow"] = "add_numbers"
        context.user_data["add_num_step"] = "waiting_per_user"
        numbers_count = len(context.user_data.get("add_num_numbers", []))
        country_flag  = context.user_data.get("add_num_country_flag", "🌍")
        country_name  = context.user_data.get("add_num_country_name", "Unknown")
        await query.edit_message_text(
            f"✅ <b>{numbers_count} numbers</b> | {country_flag} {country_name}\n"
            f"📂 Category: <b>{category_name}</b>\n\n"
            f"<b>Step 3:</b> How many numbers per user?\n"
            f"Send an integer. Example: <code>1</code>",
            parse_mode=ParseMode.HTML
        )

    elif data == "admin_manage_numbers":
        await query.answer()
        batches = await db.get_all_batches()
        if not batches:
            await query.edit_message_text("📁 <b>Manage Numbers</b>\n\nNo number batches found.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkupBack())
            return
        lines = []
        for b in batches:
            lines.append(
                f"<b>ID {b['id']}</b> | {b['country_flag']} {b['country_name']}\n"
                f"📂 {b['category_emoji']} {b['category_name']}\n"
                f"📅 {b['added_at'][:10]} | Total: {b['total_numbers']} | Available: {b['available_numbers']}\n"
                f"💵 Rate: {b['rate_per_otp']}$"
            )
        await query.edit_message_text(
            "📁 <b>Manage Numbers</b>\n\n" + "\n\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=manage_numbers_keyboard([dict(b) for b in batches])
        )

    elif data.startswith("del_batch_"):
        batch_id = int(data.replace("del_batch_", ""))
        await db.delete_batch(batch_id)
        await query.answer(f"✅ Batch ID {batch_id} deleted.", show_alert=True)
        batches = await db.get_all_batches()
        if not batches:
            await query.edit_message_text("📁 <b>Manage Numbers</b>\n\nNo number batches found.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkupBack())
            return
        lines = [
            f"<b>ID {b['id']}</b> | {b['country_flag']} {b['country_name']}\n"
            f"📂 {b['category_emoji']} {b['category_name']}\n"
            f"Total: {b['total_numbers']} | Available: {b['available_numbers']} | Rate: {b['rate_per_otp']}$"
            for b in batches
        ]
        await query.edit_message_text("📁 <b>Manage Numbers</b>\n\n" + "\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=manage_numbers_keyboard([dict(b) for b in batches]))

    elif data == "admin_manage_categories":
        await query.answer()
        categories = await db.get_all_categories()
        cat_list = [{"name": c["name"], "emoji": c["emoji"]} for c in categories]
        lines = [f"• {c['emoji']} <b>{c['name']}</b> (ID: {c['id']})" for c in categories]
        await query.edit_message_text(
            "📂 <b>Category Management</b>\n\n" + ("\n".join(lines) if lines else "No categories yet."),
            parse_mode=ParseMode.HTML,
            reply_markup=category_management_keyboard(cat_list)
        )

    elif data == "add_category":
        await query.answer()
        context.user_data["admin_flow"] = "add_category"
        await query.edit_message_text("➕ <b>Add Category</b>\n\nSend the name of the new category (e.g. TWITTER):", parse_mode=ParseMode.HTML)

    elif data.startswith("del_category_"):
        name = data.replace("del_category_", "")
        await db.delete_category(name)
        await query.answer(f"✅ Category {name} deleted.", show_alert=True)
        categories = await db.get_all_categories()
        cat_list = [{"name": c["name"], "emoji": c["emoji"]} for c in categories]
        lines = [f"• {c['emoji']} <b>{c['name']}</b> (ID: {c['id']})" for c in categories]
        await query.edit_message_text(
            "📂 <b>Category Management</b>\n\n" + ("\n".join(lines) if lines else "No categories yet."),
            parse_mode=ParseMode.HTML,
            reply_markup=category_management_keyboard(cat_list)
        )

    elif data == "admin_withdraw_requests":
        await query.answer()
        requests = await db.get_pending_withdraw_requests()
        if not requests:
            await query.edit_message_text("💸 <b>Withdraw Requests</b>\n\n✅ No pending requests.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkupBack())
            return
        for req in requests:
            await query.message.reply_text(
                f"💸 <b>Withdraw Request #{req['id']}</b>\n\n"
                f"👤 <b>Name:</b> {req['full_name']}\n"
                f"🔗 <b>Username:</b> @{req['username']}\n"
                f"💳 <b>Method:</b> {req['method']}\n"
                f"📨 <b>Address:</b> {req['address']}\n"
                f"💵 <b>Amount:</b> ${req['amount']}\n"
                f"📅 <b>Date:</b> {req['created_at'][:16]}",
                parse_mode=ParseMode.HTML,
                reply_markup=withdraw_action_keyboard(req["id"])
            )

    elif data.startswith("withdraw_complete_"):
        req_id = int(data.replace("withdraw_complete_", ""))
        req = await db.get_withdraw_request(req_id)
        await db.update_withdraw_status(req_id, "completed")
        await query.edit_message_text(query.message.text + "\n\n✅ <b>COMPLETED</b>", parse_mode=ParseMode.HTML)
        if req:
            try:
                await context.bot.send_message(chat_id=req["user_id"], text="✅ <b>আপনার উইথড্রো সফল হয়েছে!</b>\n\nআপনার টাকা পাঠানো হয়েছে। ধন্যবাদ! 🎉", parse_mode=ParseMode.HTML)
            except Exception:
                pass

    elif data.startswith("withdraw_reject_"):
        req_id = int(data.replace("withdraw_reject_", ""))
        req = await db.get_withdraw_request(req_id)
        await db.update_withdraw_status(req_id, "rejected")
        await query.edit_message_text(query.message.text + "\n\n❌ <b>REJECTED</b>", parse_mode=ParseMode.HTML)
        if req:
            try:
                await context.bot.send_message(chat_id=req["user_id"], text="❌ <b>আপনার উইথড্রো বাতিল করা হয়েছে।</b>\n\nদয়া করে সাপোর্টে যোগাযোগ করুন।", parse_mode=ParseMode.HTML)
            except Exception:
                pass

    elif data == "admin_broadcast":
        await query.answer()
        context.user_data["admin_flow"] = "broadcast"
        await query.edit_message_text("📢 <b>Broadcast</b>\n\nSend the message (text, photo, video) to broadcast to all users:", parse_mode=ParseMode.HTML)

    elif data in ("confirm_broadcast", "skip_broadcast"):
        if data == "confirm_broadcast":
            msg = context.user_data.get("broadcast_message")
            if not msg:
                txt = context.user_data.get("broadcast_message_text")
                if txt:
                    users = await db.get_all_users()
                    # ⚡ Batch broadcast for plain text too
                    async def send_txt(user):
                        try:
                            await context.bot.send_message(chat_id=user["user_id"], text=txt, parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
                    for i in range(0, len(users), 25):
                        await asyncio.gather(*[send_txt(u) for u in users[i:i+25]])
                        await asyncio.sleep(0.5)
            else:
                await do_broadcast(context.bot, msg, query)
        context.user_data.pop("broadcast_message", None)
        context.user_data.pop("broadcast_message_text", None)
        live_users = await db.count_live_users()
        try:
            await query.edit_message_text(f"🛠 <b>Admin Panel</b> [Live Users: {live_users}]", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
        except Exception:
            pass

    elif data == "admin_list":
        await query.answer()
        admins = await db.get_all_admins()
        if not admins:
            text = "👤 <b>Admin List</b>\n\nThere are no admins [only owner]"
        else:
            lines = []
            # ⚡ Concurrent get_chat calls
            async def get_admin_line(adm):
                try:
                    chat = await context.bot.get_chat(adm["user_id"])
                    name = chat.full_name or str(adm["user_id"])
                    uname = f"@{chat.username}" if chat.username else ""
                    return f"• {name} {uname} (<code>{adm['user_id']}</code>)"
                except Exception:
                    return f"• <code>{adm['user_id']}</code>"
            lines = await asyncio.gather(*[get_admin_line(a) for a in admins])
            text = "👤 <b>Admin List</b>\n\n" + "\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkupBack())

    elif data == "admin_settings":
        await query.answer()
        # ⚡ Concurrent settings fetch
        support, ref_amount, min_w, otp_link, main_ch = await asyncio.gather(
            db.get_setting("support_username"),
            db.get_setting("referral_amount"),
            db.get_setting("min_withdraw"),
            db.get_setting("otp_group_link"),
            db.get_setting("main_channel_link"),
        )
        await query.edit_message_text(
            f"⚙️ <b>Bot Settings</b>\n\n"
            f"• Support Username: {support or 'N/A'}\n"
            f"• Referral Amount ($): {ref_amount or 'N/A'}\n"
            f"• Min Withdraw ($): {min_w or 'N/A'}\n"
            f"• OTP Group Link: {otp_link or 'N/A'}\n"
            f"• Main Channel Link: {main_ch or 'N/A'}\n\n"
            f"Tap a button to modify:",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard()
        )

    elif data.startswith("setting_"):
        setting_key = data.replace("setting_", "")
        key_labels = {
            "support_username": "Support Username",
            "referral_amount": "Referral Amount ($)",
            "min_withdraw": "Min Withdraw ($)",
            "otp_group_link": "OTP Group Link",
            "main_channel_link": "Main Channel Link",
        }
        label = key_labels.get(setting_key, setting_key)
        context.user_data["admin_flow"] = "setting"
        context.user_data["setting_key"] = setting_key
        await query.edit_message_text(f"✏️ <b>Edit {label}</b>\n\nSend the new value:", parse_mode=ParseMode.HTML)

    elif data == "admin_req_channels":
        await query.answer()
        channels = await db.get_required_channels()
        ch_list = [dict(c) for c in channels]
        text = "📣 <b>Required Channels</b>\n\n" + ("\n".join([f"• {c['channel_link']}" for c in channels]) if channels else "No required channels configured.")
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=req_channels_keyboard(ch_list))

    elif data == "add_channel":
        await query.answer()
        context.user_data["admin_flow"] = "add_channel"
        await query.edit_message_text("📣 <b>Add Required Channel</b>\n\nSend the channel invite link (e.g. https://t.me/yourchannel):", parse_mode=ParseMode.HTML)

    elif data.startswith("del_channel_"):
        ch_id = int(data.replace("del_channel_", ""))
        await db.delete_required_channel(ch_id)
        _invalidate_channel_cache()  # ⚡ Cache clear
        await query.answer("✅ Channel removed.", show_alert=True)
        channels = await db.get_required_channels()
        ch_list = [dict(c) for c in channels]
        text = "📣 <b>Required Channels</b>\n\n" + ("\n".join([f"• {c['channel_link']}" for c in channels]) if channels else "No channels configured.")
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=req_channels_keyboard(ch_list))

    elif data == "admin_add_admin":
        await query.answer()
        context.user_data["admin_flow"] = "add_admin"
        await query.edit_message_text("👮 <b>Add Admin</b>\n\nSend the Telegram <b>User ID</b> of the new admin:", parse_mode=ParseMode.HTML)

    elif data == "admin_remove_admin":
        await query.answer()
        context.user_data["admin_flow"] = "remove_admin"
        await query.edit_message_text("🚫 <b>Remove Admin</b>\n\nSend the Telegram <b>User ID</b> to remove from admin:", parse_mode=ParseMode.HTML)

    elif data == "admin_manage_api":
        await query.answer()
        apis = await db.get_all_apis()
        api_list = [dict(a) for a in apis]
        text = "🔗 <b>Manage API</b>\n\nSelect a system:"
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                      reply_markup=api_management_keyboard(api_list))

    elif data == "api_system_menu":
        await query.answer()
        apis = await db.get_all_apis()
        api_list = [dict(a) for a in apis]
        text = "🔗 <b>API System</b>\n\n" + ("\n".join([f"✅ <b>{a['name']}</b>" for a in apis]) if apis else "No APIs configured.")
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                      reply_markup=api_system_keyboard(api_list))

    elif data == "scraping_system_menu":
        await query.answer()
        scraper_list = [{"name": n} for n in _scrapers]
        text = "🕷 <b>Scraping System</b>\n\n"
        if _scrapers:
            for n, cfg in _scrapers.items():
                status = "🟢 Running" if cfg.get("running") else "🔴 Stopped"
                text += f"• <b>{n}</b> — {status}\n"
        else:
            text += "No scrapers added yet."
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                      reply_markup=scraping_system_keyboard(scraper_list))

    elif data == "add_scraper":
        await query.answer()
        context.user_data["admin_flow"] = "add_scraper"
        context.user_data["scraper_step"] = "name"
        await query.edit_message_text(
            "🕷 <b>Add Scraper</b>\n\n<b>Step 1:</b> এই scraper এর একটা <b>নাম</b> দিন:\n(যেমন: SMSHadi)",
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("scraper_info_"):
        name = data.replace("scraper_info_", "")
        await query.answer()
        cfg = _scrapers.get(name, {})
        status = "🟢 Running" if cfg.get("running") else "🔴 Stopped"
        text = (
            f"🕷 <b>Scraper: {name}</b>\n\n"
            f"🌐 URL: {cfg.get('url','?')}\n"
            f"👤 User: {cfg.get('user','?')}\n"
            f"📊 Status: {status}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                      reply_markup=scraper_action_keyboard(name, cfg.get("running", False)))

    elif data.startswith("scraper_start_"):
        name = data.replace("scraper_start_", "")
        await query.answer()
        if name in _scrapers and not _scrapers[name].get("running"):
            otp_group_id = os.getenv("OTP_GROUP_ID", "") or OTP_GROUP_ID
            _start_scraper_task(name, context.bot, otp_group_id)
        cfg = _scrapers.get(name, {})
        await query.edit_message_text(
            f"🕷 <b>Scraper: {name}</b>\n\n🟢 Started!",
            parse_mode=ParseMode.HTML,
            reply_markup=scraper_action_keyboard(name, True)
        )

    elif data.startswith("scraper_stop_"):
        name = data.replace("scraper_stop_", "")
        await query.answer()
        _stop_scraper(name)
        await query.edit_message_text(
            f"🕷 <b>Scraper: {name}</b>\n\n🔴 Stopped.",
            parse_mode=ParseMode.HTML,
            reply_markup=scraper_action_keyboard(name, False)
        )

    elif data.startswith("scraper_del_"):
        name = data.replace("scraper_del_", "")
        _stop_scraper(name)
        _scrapers.pop(name, None)
        await query.answer(f"🗑 '{name}' deleted.", show_alert=True)
        scraper_list = [{"name": n} for n in _scrapers]
        await query.edit_message_text(
            "🕷 <b>Scraping System</b>\n\nScraper deleted.",
            parse_mode=ParseMode.HTML,
            reply_markup=scraping_system_keyboard(scraper_list)
        )

    elif data == "add_api":
        await query.answer()
        context.user_data["admin_flow"] = "add_api"
        context.user_data["api_step"] = "name"
        await query.edit_message_text("🔗 <b>Add API</b>\n\nStep 1: Type a <b>name</b> for this API:", parse_mode=ParseMode.HTML)

    elif data.startswith("del_api_"):
        name = data.replace("del_api_", "")
        await db.delete_api(name)
        await query.answer(f"✅ API '{name}' deleted.", show_alert=True)
        apis = await db.get_all_apis()
        api_list = [dict(a) for a in apis]
        text = "🔗 <b>Manage API</b>\n\n" + ("\n".join([f"✅ <b>{a['name']}</b>" for a in apis]) if apis else "No APIs configured.")
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=api_management_keyboard(api_list))

# ============================================================
# SCRAPER ENGINE — SMS Hadi style (math CAPTCHA + polling)
# ============================================================

async def _solve_math_captcha(question: str) -> str:
    """'What is 3 + 3 = ?' → '6'"""
    m = re.search(r'(\d+)\s*([+\-\*x×])\s*(\d+)', question)
    if not m:
        return "0"
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    if op == '+':   return str(a + b)
    if op == '-':   return str(a - b)
    if op in ('*', 'x', '×'): return str(a * b)
    return "0"

async def _scraper_loop(name: str, bot: Bot, group_id: str):
    """Login → poll every 2s → forward new OTPs to group."""
    cfg = _scrapers.get(name)
    if not cfg:
        return
    url      = cfg["url"].rstrip("/")
    username = cfg["user"]
    password = cfg["password"]
    seen_ids: set = set()

    login_url = url
    poll_url  = url.replace("/login", "/agent")
    logger.info(f"[Scraper:{name}] Starting — {login_url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    while _scrapers.get(name, {}).get("running"):
      try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            try:
                # ── Step 1: GET login page for CAPTCHA ──
                async with session.get(login_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    html = await r.text()

                soup = BeautifulSoup(html, "html.parser")

                # Find math CAPTCHA label
                captcha_label = ""
                for label in soup.find_all(["label", "p", "span", "div"]):
                    txt = label.get_text()
                    if re.search(r'\d+\s*[+\-\*x×]\s*\d+', txt):
                        captcha_label = txt
                        break

                captcha_ans = await _solve_math_captcha(captcha_label) if captcha_label else "0"

                # ── Step 2: POST login ──
                # captcha field name auto-detect, default "answer"
                captcha_field = "answer"
                for inp in soup.find_all("input"):
                    n = inp.get("name", "").lower()
                    if any(x in n for x in ["captcha", "answer", "math", "verify", "code"]):
                        captcha_field = inp.get("name")
                        break

                payload = {
                    "username": username,
                    "password": password,
                    captcha_field: captcha_ans,
                }
                # include hidden fields (csrf etc)
                for inp in soup.find_all("input", {"type": "hidden"}):
                    if inp.get("name") and inp.get("value"):
                        payload[inp["name"]] = inp["value"]

                async with session.post(login_url, data=payload,
                                        timeout=aiohttp.ClientTimeout(total=10),
                                        allow_redirects=True,
                                        headers={
                                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                                            "Referer": login_url,
                                        }) as r2:
                    body = await r2.text()
                    final_url = str(r2.url)

                logger.info(f"[Scraper:{name}] After login URL: {final_url}")

                if "login" in final_url.lower():
                    logger.warning(f"[Scraper:{name}] Login failed, retrying in 5s")
                    await asyncio.sleep(5)
                    continue

                logger.info(f"[Scraper:{name}] Logged in ✅")

                # ── Step 3: Poll for new SMS rows ──
                while _scrapers.get(name, {}).get("running"):
                    try:
                        async with session.get(poll_url,
                                               timeout=aiohttp.ClientTimeout(total=10)) as r3:
                            page = await r3.text()

                        s2 = BeautifulSoup(page, "html.parser")
                        rows = s2.select("table tbody tr")
                        for row in rows:
                            cells = [td.get_text(strip=True) for td in row.find_all("td")]
                            if len(cells) < 3:
                                continue
                            row_id = "|".join(cells[:4])
                            if row_id in seen_ids:
                                continue
                            seen_ids.add(row_id)
                            # Parse number, cli, sms
                            number  = cells[1] if len(cells) > 1 else "?"
                            cli     = cells[2] if len(cells) > 2 else "?"
                            sms_txt = cells[3] if len(cells) > 3 else "?"
                            otp_m   = re.search(r'\b(\d{4,8})\b', sms_txt)
                            otp     = otp_m.group(1) if otp_m else "—"
                            msg = (
                                f"🔐 <b>OTP Received! [{name}]</b>\n\n"
                                f"📞 <b>Number:</b> {number}\n"
                                f"📱 <b>CLI:</b> {cli}\n"
                                f"🔑 <b>OTP:</b> {otp}\n"
                                f"📩 <b>SMS:</b> {sms_txt}"
                            )
                            try:
                                await bot.send_message(chat_id=group_id, text=msg,
                                                       parse_mode=ParseMode.HTML)
                            except Exception as e:
                                logger.error(f"[Scraper:{name}] Send error: {e}")

                    except Exception as e:
                        logger.warning(f"[Scraper:{name}] Poll error: {e}")
                        break   # re-login

                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"[Scraper:{name}] Loop error: {type(e).__name__}: {e}", exc_info=True)
                await asyncio.sleep(10)

      except Exception as e:
        logger.error(f"[Scraper:{name}] Session error: {e}", exc_info=True)
        await asyncio.sleep(10)

    logger.info(f"[Scraper:{name}] Stopped.")

def _start_scraper_task(name: str, bot: Bot, group_id: str):
    task = asyncio.create_task(_scraper_loop(name, bot, group_id))
    _scrapers[name]["task"]    = task
    _scrapers[name]["running"] = True

def _stop_scraper(name: str):
    if name in _scrapers:
        _scrapers[name]["running"] = False
        t = _scrapers[name].get("task")
        if t:
            t.cancel()
            
# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling update: {context.error}", exc_info=True)

# ============================================================
# POST INIT
# ============================================================

async def post_init(application: Application):
    global _bot_username
    await db.init_db()
    # ⚡ Bot username একবার cache করো — বারবার get_me() call লাগবে না
    try:
        bot_info = await application.bot.get_me()
        _bot_username = bot_info.username
        logger.info(f"✅ Bot @{_bot_username} started successfully!")
    except Exception as e:
        logger.warning(f"Could not cache bot username: {e}")
        logger.info("✅ Bot started successfully!")
    otp_group_id = os.getenv("OTP_GROUP_ID", "") or OTP_GROUP_ID
    if otp_group_id:
        asyncio.create_task(start_api_polling(application.bot, otp_group_id))
        logger.info(f"🔄 API polling started for group: {otp_group_id}")

# ============================================================
# MAIN
# ============================================================

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))

    application.add_handler(MessageHandler(
        filters.Regex(r"(GET NUMBER|BALANCE|REFER AND EARN|SUPPORT|STATUS|ADMIN PANEL)"),
        handle_menu_buttons
    ))

    application.add_handler(CallbackQueryHandler(verify_join_callback,      pattern="^verify_join$"))
    application.add_handler(CallbackQueryHandler(service_selected_callback, pattern="^service_"))
    application.add_handler(CallbackQueryHandler(back_to_services_callback, pattern="^back_to_services$"))
    application.add_handler(CallbackQueryHandler(country_selected_callback, pattern="^country_"))
    application.add_handler(CallbackQueryHandler(change_number_callback,    pattern="^change_number$"))
    application.add_handler(CallbackQueryHandler(change_country_callback,   pattern="^change_country$"))
    application.add_handler(CallbackQueryHandler(copy_number_callback,      pattern="^copy_number_"))
    application.add_handler(CallbackQueryHandler(withdraw_method_callback,  pattern="^withdraw_(bkash|nagad|binance)$"))
    application.add_handler(CallbackQueryHandler(back_to_menu_callback,     pattern="^back_to_menu$"))
    application.add_handler(CallbackQueryHandler(
        admin_callback_handler,
        pattern="^(admin_|back_to_admin|add_|del_|setting_|withdraw_complete|withdraw_reject|confirm_broadcast|skip_broadcast|addnum_cat_|api_system_menu|scraping_system_menu|scraper_)"
    ))

    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_media_broadcast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    application.add_error_handler(error_handler)

    logger.info("🚀 Bot is running (Optimized)...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        timeout=30,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30,
        pool_timeout=30,
    )

if __name__ == "__main__":
    main()            