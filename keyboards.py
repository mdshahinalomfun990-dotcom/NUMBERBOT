from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
from config import SERVICE_EMOJIS

# ============================================================
# MAIN MENU KEYBOARD (Reply Keyboard)
# ============================================================

def main_menu_keyboard(is_admin_user: bool = False):
    keyboard = [
        ["📞 GET NUMBER", "💰 BALANCE"],
        ["👥 REFER AND EARN", "💬 SUPPORT"],
        ["📊 STATUS"],
    ]
    if is_admin_user:
        keyboard.append(["🛠 ADMIN PANEL"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ============================================================
# USER FLOW KEYBOARDS
# ============================================================

def services_keyboard(categories: list):
    """Inline keyboard showing available services."""
    buttons = []
    for cat in categories:
        emoji = cat["emoji"] if isinstance(cat, dict) else cat[2]
        name = cat["name"] if isinstance(cat, dict) else cat[1]
        buttons.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"service_{name}")])
    return InlineKeyboardMarkup(buttons)

def countries_keyboard(countries: list, category_name: str):
    """Inline keyboard showing available countries for a service."""
    buttons = []
    for c in countries:
        flag = c["country_flag"]
        country = c["country_name"]
        count = c["available_numbers"]
        batch_id = c["batch_id"]
        buttons.append([InlineKeyboardButton(
            f"{flag} {country} ({count})",
            callback_data=f"country_{batch_id}_{category_name}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Back To Services", callback_data="back_to_services")])
    return InlineKeyboardMarkup(buttons)

def number_assigned_keyboard(number: str, flag: str):
    """Keyboard shown after a number is assigned."""
    buttons = [
        [InlineKeyboardButton(f"📋 {flag} {number}", callback_data=f"copy_number_{number}")],
        [InlineKeyboardButton("🔄 Change Number", callback_data="change_number")],
        [InlineKeyboardButton("🌍 Change Country", callback_data="change_country")],
        [InlineKeyboardButton("🔔 Otp Group", url="placeholder_otp_link")],
    ]
    return InlineKeyboardMarkup(buttons)

async def number_assigned_keyboard_with_link(number: str, flag: str, otp_link: str):
    # Normalize number: strip spaces/dashes, ensure single +
    clean = number.strip().replace(" ", "").replace("-", "")
    if not clean.startswith("+"):
        clean = "+" + clean.lstrip("+")
    buttons = [
        [InlineKeyboardButton(f"📋 {clean}", copy_text=CopyTextButton(clean))],
        [InlineKeyboardButton("🔄 Change Number", callback_data="change_number")],
        [InlineKeyboardButton("🌍 Change Country", callback_data="change_country")],
        [InlineKeyboardButton("🔔 Otp Group", url=otp_link)],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# BALANCE KEYBOARDS
# ============================================================

def balance_keyboard():
    buttons = [
        [
            InlineKeyboardButton("Bkash", callback_data="withdraw_bkash"),
            InlineKeyboardButton("Nagad", callback_data="withdraw_nagad"),
        ],
        [InlineKeyboardButton("Binance", callback_data="withdraw_binance")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# SUPPORT KEYBOARD
# ============================================================

def support_keyboard(support_username: str):
    buttons = [
        [InlineKeyboardButton("💬 Contact Support", url=f"https://t.me/{support_username.lstrip('@')}")],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# ADMIN PANEL KEYBOARD
# ============================================================

def admin_panel_keyboard():
    buttons = [
        [
            InlineKeyboardButton("➕ Add Numbers", callback_data="admin_add_numbers"),
            InlineKeyboardButton("📁 Manage Numbers", callback_data="admin_manage_numbers"),
        ],
        [
            InlineKeyboardButton("📂 Manage Categories", callback_data="admin_manage_categories"),
            InlineKeyboardButton("💸 Withdraw Requests", callback_data="admin_withdraw_requests"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("👤 Admin List", callback_data="admin_list"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings"),
            InlineKeyboardButton("📣 Req. Channels", callback_data="admin_req_channels"),
        ],
        [
            InlineKeyboardButton("👮 Add Admin", callback_data="admin_add_admin"),
            InlineKeyboardButton("🚫 Remove Admin", callback_data="admin_remove_admin"),
        ],
        [
            InlineKeyboardButton("🔗 Manage API", callback_data="admin_manage_api"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# SETTINGS KEYBOARD
# ============================================================

def settings_keyboard():
    buttons = [
        [InlineKeyboardButton("✏️ Edit Support Username", callback_data="setting_support_username")],
        [InlineKeyboardButton("✏️ Edit Referral Amount ($)", callback_data="setting_referral_amount")],
        [InlineKeyboardButton("✏️ Edit Min Withdraw ($)", callback_data="setting_min_withdraw")],
        [InlineKeyboardButton("✏️ Edit OTP Group Link 🔗", callback_data="setting_otp_group_link")],
        [InlineKeyboardButton("✏️ Edit Main Channel Link 📢", callback_data="setting_main_channel_link")],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# CATEGORY MANAGEMENT KEYBOARD
# ============================================================

def category_management_keyboard(categories: list):
    buttons = [[InlineKeyboardButton("➕ Add Category", callback_data="add_category")]]
    for cat in categories:
        name = cat["name"] if isinstance(cat, dict) else cat[1]
        buttons.append([InlineKeyboardButton(f"🗑 Delete {name}", callback_data=f"del_category_{name}")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# MANAGE NUMBERS KEYBOARD
# ============================================================

def manage_numbers_keyboard(batches: list):
    buttons = []
    for batch in batches:
        bid = batch["id"]
        buttons.append([InlineKeyboardButton(f"🗑 Delete ID {bid}", callback_data=f"del_batch_{bid}")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# WITHDRAW REQUEST KEYBOARD (Admin)
# ============================================================

def withdraw_action_keyboard(request_id: int):
    buttons = [
        [
            InlineKeyboardButton("✅ Complete", callback_data=f"withdraw_complete_{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"withdraw_reject_{request_id}"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# REQUIRED CHANNELS KEYBOARD
# ============================================================

def req_channels_keyboard(channels: list):
    buttons = [[InlineKeyboardButton("➕ Add New Channel", callback_data="add_channel")]]
    for ch in channels:
        cid = ch["id"]
        link = ch["channel_link"]
        buttons.append([InlineKeyboardButton(f"🗑 Delete: {link[:30]}", callback_data=f"del_channel_{cid}")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# API MANAGEMENT KEYBOARD
# ============================================================

def api_management_keyboard(apis: list):
    buttons = [
        [
            InlineKeyboardButton("🔗 API System", callback_data="api_system_menu"),
            InlineKeyboardButton("🕷 Scraping System", callback_data="scraping_system_menu"),
        ],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="back_to_admin_panel")],
    ]
    return InlineKeyboardMarkup(buttons)

def api_system_keyboard(apis: list):
    buttons = []
    for api in apis:
        name = api["name"]
        buttons.append([InlineKeyboardButton(f"✅ {name}", callback_data=f"del_api_{name}")])
    buttons.append([InlineKeyboardButton("➕ Add API", callback_data="add_api")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_manage_api")])
    return InlineKeyboardMarkup(buttons)

def scraping_system_keyboard(scrapers: list):
    buttons = []
    for s in scrapers:
        name = s["name"] if isinstance(s, dict) else s
        buttons.append([InlineKeyboardButton(f"🕷 {name}", callback_data=f"scraper_info_{name}")])
    buttons.append([InlineKeyboardButton("➕ Add Scraper", callback_data="add_scraper")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_manage_api")])
    return InlineKeyboardMarkup(buttons)

def scraper_action_keyboard(name: str, is_running: bool):
    status_btn = InlineKeyboardButton(
        "⏹ Stop" if is_running else "▶️ Start",
        callback_data=f"scraper_{'stop' if is_running else 'start'}_{name}"
    )
    buttons = [
        [status_btn],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"scraper_del_{name}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="scraping_system_menu")],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# ADD NUMBERS STEP KEYBOARDS
# ============================================================

def add_numbers_service_keyboard(categories: list):
    buttons = []
    for cat in categories:
        emoji = cat["emoji"] if isinstance(cat, dict) else cat[2]
        name = cat["name"] if isinstance(cat, dict) else cat[1]
        buttons.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"addnum_cat_{name}")])
    return InlineKeyboardMarkup(buttons)

def broadcast_confirm_keyboard():
    buttons = [
        [
            InlineKeyboardButton("📢 Broadcast to Users", callback_data="confirm_broadcast"),
            InlineKeyboardButton("❌ Skip", callback_data="skip_broadcast"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)

# ============================================================
# CHANNEL JOIN VERIFICATION KEYBOARD
# ============================================================

def channel_join_keyboard(channels: list):
    buttons = []
    for ch in channels:
        link = ch["channel_link"]
        buttons.append([InlineKeyboardButton("📢 Join Channel", url=link)])
    buttons.append([InlineKeyboardButton("✅ I've Joined — Verify", callback_data="verify_join")])
    return InlineKeyboardMarkup(buttons)
