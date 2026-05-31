import aiohttp
import asyncio
import logging
import re
from database import get_all_apis, get_assignment_by_number, reward_user_for_otp, save_otp_to_number, get_setting
from datetime import datetime
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

_sent_otps = {}
_last_seen_dt = {}

def extract_otp(message: str) -> str:
    # Match dash-separated OTP like 580-080
    m = re.search(r'(\d{3,4})[-](\d{3,4})', message)
    if m:
        return m.group(1) + m.group(2)
    # Match plain 4-8 digit OTP
    m = re.search(r'(?<![\d])(\d{4,8})(?![\d])', message)
    if m:
        return m.group(1)
    return "N/A"

COUNTRY_CODES = {
    "880": ("Bangladesh", "🇧🇩"), "91": ("India", "🇮🇳"), "1": ("USA", "🇺🇸"),
    "44": ("UK", "🇬🇧"), "255": ("Tanzania", "🇹🇿"), "95": ("Myanmar", "🇲🇲"),
    "959": ("Myanmar", "🇲🇲"), "7": ("Russia/KZ", "🇷🇺"), "62": ("Indonesia", "🇮🇩"),
    "92": ("Pakistan", "🇵🇰"), "234": ("Nigeria", "🇳🇬"), "60": ("Malaysia", "🇲🇾"),
    "66": ("Thailand", "🇹🇭"), "84": ("Vietnam", "🇻🇳"), "63": ("Philippines", "🇵🇭"),
    "82": ("South Korea", "🇰🇷"), "81": ("Japan", "🇯🇵"), "55": ("Brazil", "🇧🇷"),
    "27": ("South Africa", "🇿🇦"), "254": ("Kenya", "🇰🇪"), "233": ("Ghana", "🇬🇭"),
    "213": ("Algeria", "🇩🇿"), "212": ("Morocco", "🇲🇦"), "216": ("Tunisia", "🇹🇳"),
    "20": ("Egypt", "🇪🇬"), "237": ("Cameroon", "🇨🇲"), "225": ("Ivory Coast", "🇨🇮"),
    "221": ("Senegal", "🇸🇳"), "243": ("Congo DR", "🇨🇩"), "33": ("France", "🇫🇷"),
    "49": ("Germany", "🇩🇪"), "34": ("Spain", "🇪🇸"), "39": ("Italy", "🇮🇹"),
    "351": ("Portugal", "🇵🇹"), "31": ("Netherlands", "🇳🇱"), "32": ("Belgium", "🇧🇪"),
    "48": ("Poland", "🇵🇱"), "380": ("Ukraine", "🇺🇦"), "40": ("Romania", "🇷🇴"),
    "90": ("Turkey", "🇹🇷"), "98": ("Iran", "🇮🇷"), "966": ("Saudi Arabia", "🇸🇦"),
    "971": ("UAE", "🇦🇪"), "965": ("Kuwait", "🇰🇼"), "974": ("Qatar", "🇶🇦"),
    "86": ("China", "🇨🇳"), "61": ("Australia", "🇦🇺"), "64": ("New Zealand", "🇳🇿"),
    "52": ("Mexico", "🇲🇽"), "54": ("Argentina", "🇦🇷"), "57": ("Colombia", "🇨🇴"),
    "56": ("Chile", "🇨🇱"), "58": ("Venezuela", "🇻🇪"),
}

def detect_country_from_number(number: str):
    clean = re.sub(r'[\s\-\+]', '', number)
    for code in sorted(COUNTRY_CODES.keys(), key=lambda x: -len(x)):
        if clean.startswith(code):
            name, flag = COUNTRY_CODES[code]
            return name, flag
    return "Unknown", "🌍"

async def check_api_for_otps(bot, otp_group_id: str):
    apis = await get_all_apis()
    if not apis:
        return
    async with aiohttp.ClientSession() as session:
        for api in apis:
            api = dict(api)
            try:
                url = f"{api['api_url']}?token={api['api_key']}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    await process_api_response(bot, api['name'], data, otp_group_id)
            except Exception as e:
                logger.error(f"API polling error [{api['name']}]: {e}")

async def process_api_response(bot, api_name: str, data, otp_group_id: str):
    if isinstance(data, dict):
        data = data.get("data", [data])
    if not isinstance(data, list):
        data = [data]

    data = sorted(data, key=lambda x: x.get("dt", ""))
    new_max_dt = _last_seen_dt.get(api_name, "")

    for item in data:
        dt = item.get("dt", "")
        number = item.get("number") or item.get("num", "")
        message = item.get("message", "")
        app = item.get("app") or item.get("cli", "Unknown")

        if not number or not message:
            continue

        # Skip old dt
        if dt and dt <= _last_seen_dt.get(api_name, ""):
            continue

        otp = extract_otp(message)
        clean_number = re.sub(r'[\s\-\+]', '', number)

        # Duplicate check
        key = f"{api_name}:{clean_number}"
        if _sent_otps.get(key) == otp and otp != "N/A":
            if dt > new_max_dt:
                new_max_dt = dt
            continue
        _sent_otps[key] = otp

        await save_otp_to_number(clean_number, otp)

        # Try multiple number formats to find assignment
        assignment = await get_assignment_by_number(clean_number)
        if not assignment:
            assignment = await get_assignment_by_number("+" + clean_number)
        if not assignment:
            assignment = await get_assignment_by_number(clean_number.lstrip("0"))

        if assignment and assignment.get("country_name") and assignment["country_name"] != "Unknown":
            country_flag = assignment["country_flag"]
            country_name = assignment["country_name"]
        else:
            country_name, country_flag = detect_country_from_number(clean_number)

        service = assignment["category_name"] if assignment else app
        masked = "+" + clean_number[:4] + "***" + clean_number[-4:] if len(clean_number) > 8 else clean_number
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            support_username = await get_setting("support_username") or "@support"
            channel_link = await get_setting("main_channel_link") or await get_setting("otp_group_link") or "https://t.me/otpgroup"
        except Exception:
            support_username = "@support"
            channel_link = "https://t.me/otpgroup"

        support_clean = support_username.replace("@", "")

        try:
            bot_info = await bot.get_me()
            bot_link = f"https://t.me/{bot_info.username}"
        except Exception:
            bot_link = "https://t.me/bot"

        group_msg = (
            f"{country_flag} <b>{country_name} {service} SUCCESSFULLY RECEIVED</b> 🔥\n\n"
            f"<blockquote>🕐 Time: {now}</blockquote>\n"
            f"<blockquote>🌍 Country: {country_flag} {country_name}</blockquote>\n"
            f"<blockquote>📱 Service: {service}</blockquote>\n"
            f"<blockquote>📞 Number: <code>{masked}</code></blockquote>\n"
            f"<blockquote>🔑 OTP: <code>{otp}</code> 👆 tap to copy</blockquote>\n"
            f"<blockquote>✉️ Message: {message}</blockquote>"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Channel", url=channel_link)],
            [InlineKeyboardButton("🤖 Bot", url=bot_link)],
            [InlineKeyboardButton("👨‍💻 Developer", url=f"https://t.me/{support_clean}")],
        ])

        try:
            if otp_group_id:
                gid = int(otp_group_id) if str(otp_group_id).lstrip('-').isdigit() else otp_group_id
                await bot.send_message(chat_id=gid, text=group_msg, parse_mode="HTML", reply_markup=keyboard)
                logger.info(f"✅ OTP sent: {otp} for {masked}")
        except Exception as e:
            logger.error(f"Failed to send to OTP group: {e}")

        if assignment:
            user_id = assignment["user_id"]
            rate = assignment["rate_per_otp"]
            user_msg = (
                f"🔐 <b>OTP Received!</b>\n\n"
                f"{country_flag} {country_name} | {service}\n"
                f"📞 <b>Number:</b> <code>{clean_number}</code>\n"
                f"🔑 <b>OTP: <code>{otp}</code></b>\n\n"
                f"💰 Earned: <b>${rate}</b>"
            )
            try:
                await bot.send_message(chat_id=user_id, text=user_msg, parse_mode="HTML")
                if rate > 0:
                    await reward_user_for_otp(user_id, rate)
            except Exception as e:
                logger.error(f"Failed to forward OTP to user: {e}")

        if dt > new_max_dt:
            new_max_dt = dt

    if new_max_dt:
        _last_seen_dt[api_name] = new_max_dt

async def start_api_polling(bot, otp_group_id: str, interval: int = 2):
    logger.info(f"🔄 API polling started (every {interval}s) for group: {otp_group_id}")
    while True:
        try:
            await check_api_for_otps(bot, otp_group_id)
        except Exception as e:
            logger.error(f"API polling loop error: {e}")
        await asyncio.sleep(interval)

async def check_api_health(api_url: str, api_key: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{api_url}?token={api_key}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                return resp.status == 200
    except Exception:
        return False
