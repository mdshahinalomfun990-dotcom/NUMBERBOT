import aiosqlite
import asyncio
import logging
from config import DB_PATH, DEFAULT_SUPPORT_USERNAME, DEFAULT_REFERRAL_AMOUNT, DEFAULT_MIN_WITHDRAW, DEFAULT_OTP_GROUP_LINK

logger = logging.getLogger(__name__)

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance REAL DEFAULT 0.0,
                referral_count INTEGER DEFAULT 0,
                referred_by INTEGER DEFAULT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Admins table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Categories (services) table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                emoji TEXT DEFAULT '💥',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Number batches table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS number_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_code TEXT NOT NULL,
                country_name TEXT NOT NULL,
                country_flag TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                numbers_per_user INTEGER DEFAULT 1,
                rate_per_otp REAL DEFAULT 0.0,
                total_numbers INTEGER DEFAULT 0,
                available_numbers INTEGER DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            )
        """)

        # Numbers table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                number TEXT NOT NULL,
                is_used INTEGER DEFAULT 0,
                assigned_to INTEGER DEFAULT NULL,
                assigned_at TIMESTAMP DEFAULT NULL,
                otp_received TEXT DEFAULT NULL,
                FOREIGN KEY (batch_id) REFERENCES number_batches(id)
            )
        """)

        # Withdraw requests table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdraw_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                method TEXT NOT NULL,
                address TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP DEFAULT NULL
            )
        """)

        # Bot settings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Required channels table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_link TEXT NOT NULL,
                channel_id TEXT DEFAULT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # APIs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS apis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                api_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # User assignments (active number assignments)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_assignments (
                user_id INTEGER PRIMARY KEY,
                number_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                category_name TEXT NOT NULL,
                country_name TEXT NOT NULL,
                country_flag TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (number_id) REFERENCES numbers(id)
            )
        """)

        # Initialize default settings
        defaults = {
            "support_username": DEFAULT_SUPPORT_USERNAME,
            "referral_amount": str(DEFAULT_REFERRAL_AMOUNT),
            "min_withdraw": str(DEFAULT_MIN_WITHDRAW),
            "otp_group_link": DEFAULT_OTP_GROUP_LINK,
        }
        for key, value in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                (key, value)
            )

        await db.commit()
        logger.info("✅ Database initialized successfully.")

# ============================================================
# USER FUNCTIONS
# ============================================================

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def add_user(user_id: int, username: str, full_name: str, referred_by: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, referred_by) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, referred_by)
        )
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            return await cursor.fetchall()

async def update_user_balance(user_id: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

async def get_user_balance(user_id: int) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0.0

async def count_live_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

# ============================================================
# ADMIN FUNCTIONS
# ============================================================

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def add_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_all_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM admins") as cursor:
            return await cursor.fetchall()

# ============================================================
# CATEGORY FUNCTIONS
# ============================================================

async def get_all_categories():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM categories ORDER BY name") as cursor:
            return await cursor.fetchall()

async def add_category(name: str, emoji: str = "💥"):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO categories (name, emoji) VALUES (?, ?)", (name.upper(), emoji))
            await db.commit()
            return True
        except Exception:
            return False

async def delete_category(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM categories WHERE name = ?", (name.upper(),))
        await db.commit()

async def get_category_by_name(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM categories WHERE name = ?", (name.upper(),)) as cursor:
            return await cursor.fetchone()

# ============================================================
# NUMBER BATCH FUNCTIONS
# ============================================================

async def add_number_batch(country_code, country_name, country_flag, category_id, numbers_list, numbers_per_user, rate_per_otp):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO number_batches 
               (country_code, country_name, country_flag, category_id, numbers_per_user, rate_per_otp, total_numbers, available_numbers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (country_code, country_name, country_flag, category_id, numbers_per_user, rate_per_otp, len(numbers_list), len(numbers_list))
        )
        batch_id = cursor.lastrowid
        for number in numbers_list:
            await db.execute(
                "INSERT INTO numbers (batch_id, number) VALUES (?, ?)",
                (batch_id, number.strip())
            )
        await db.commit()
        return batch_id

async def get_all_batches():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT nb.*, c.name as category_name, c.emoji as category_emoji
            FROM number_batches nb
            JOIN categories c ON nb.category_id = c.id
            ORDER BY nb.id DESC
        """) as cursor:
            return await cursor.fetchall()

async def delete_batch(batch_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM numbers WHERE batch_id = ?", (batch_id,))
        await db.execute("DELETE FROM number_batches WHERE id = ?", (batch_id,))
        # Remove assignments for this batch
        await db.execute("DELETE FROM user_assignments WHERE batch_id = ?", (batch_id,))
        await db.commit()

async def get_categories_with_numbers():
    """Returns categories that have available numbers."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT DISTINCT c.id, c.name, c.emoji
            FROM categories c
            JOIN number_batches nb ON c.id = nb.category_id
            WHERE nb.available_numbers > 0
        """) as cursor:
            return await cursor.fetchall()

async def get_countries_for_category(category_name: str):
    """Returns countries with available numbers for a given category."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT nb.id as batch_id, nb.country_name, nb.country_flag, nb.available_numbers
            FROM number_batches nb
            JOIN categories c ON nb.category_id = c.id
            WHERE c.name = ? AND nb.available_numbers > 0
            ORDER BY nb.country_name
        """, (category_name.upper(),)) as cursor:
            return await cursor.fetchall()

async def get_next_number(batch_id: int):
    """Get next available number from a batch."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM numbers WHERE batch_id = ? AND is_used = 0 LIMIT 1",
            (batch_id,)
        ) as cursor:
            return await cursor.fetchone()

async def assign_number_to_user(user_id: int, number_id: int, batch_id: int, category_name: str, country_name: str, country_flag: str):
    async with aiosqlite.connect(DB_PATH) as db:
        # Mark number as used
        await db.execute(
            "UPDATE numbers SET is_used = 1, assigned_to = ?, assigned_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id, number_id)
        )
        # Decrease available count
        await db.execute(
            "UPDATE number_batches SET available_numbers = available_numbers - 1 WHERE id = ?",
            (batch_id,)
        )
        # Save user assignment
        await db.execute("""
            INSERT OR REPLACE INTO user_assignments 
            (user_id, number_id, batch_id, category_name, country_name, country_flag)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, number_id, batch_id, category_name, country_name, country_flag))
        await db.commit()

async def get_user_assignment(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ua.*, n.number
            FROM user_assignments ua
            JOIN numbers n ON ua.number_id = n.id
            WHERE ua.user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def release_user_assignment(user_id: int):
    """Release a user's current number assignment (without marking as available again — number is used once)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_assignments WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_status_summary():
    """Returns service | country | available count summary."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT c.name as category_name, c.emoji, nb.country_flag, nb.country_name, nb.available_numbers
            FROM number_batches nb
            JOIN categories c ON nb.category_id = c.id
            WHERE nb.available_numbers > 0
            ORDER BY c.name, nb.country_name
        """) as cursor:
            return await cursor.fetchall()

# ============================================================
# WITHDRAW FUNCTIONS
# ============================================================

async def create_withdraw_request(user_id: int, username: str, full_name: str, method: str, address: str, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO withdraw_requests (user_id, username, full_name, method, address, amount) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, full_name, method, address, amount)
        )
        # Deduct balance
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def get_pending_withdraw_requests():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM withdraw_requests WHERE status = 'pending' ORDER BY created_at") as cursor:
            return await cursor.fetchall()

async def update_withdraw_status(request_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE withdraw_requests SET status = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, request_id)
        )
        if status == "rejected":
            # Refund balance
            async with db.execute("SELECT user_id, amount FROM withdraw_requests WHERE id = ?", (request_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (row[1], row[0]))
        await db.commit()

async def get_withdraw_request(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM withdraw_requests WHERE id = ?", (request_id,)) as cursor:
            return await cursor.fetchone()

# ============================================================
# SETTINGS FUNCTIONS
# ============================================================

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

# ============================================================
# REQUIRED CHANNELS FUNCTIONS
# ============================================================

async def get_required_channels():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM required_channels") as cursor:
            return await cursor.fetchall()

async def add_required_channel(channel_link: str, channel_id: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO required_channels (channel_link, channel_id) VALUES (?, ?)",
            (channel_link, channel_id)
        )
        await db.commit()

async def delete_required_channel(channel_id_db: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM required_channels WHERE id = ?", (channel_id_db,))
        await db.commit()

# ============================================================
# API MANAGEMENT FUNCTIONS
# ============================================================

async def get_all_apis():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM apis WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def add_api(name: str, api_url: str, api_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO apis (name, api_url, api_key) VALUES (?, ?, ?)", (name, api_url, api_key))
            await db.commit()
            return True
        except Exception:
            return False

async def delete_api(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM apis WHERE name = ?", (name,))
        await db.commit()

async def get_api_by_name(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM apis WHERE name = ?", (name,)) as cursor:
            return await cursor.fetchone()

# ============================================================
# OTP FUNCTIONS
# ============================================================

async def save_otp_to_number(number: str, otp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE numbers SET otp_received = ? WHERE number = ?", (otp, number))
        await db.commit()

async def get_assignment_by_number(number: str):
    """Find who has this number assigned."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ua.*, n.number, nb.rate_per_otp
            FROM user_assignments ua
            JOIN numbers n ON ua.number_id = n.id
            JOIN number_batches nb ON ua.batch_id = nb.id
            WHERE n.number = ? OR n.number = ?
        """, (number, "+" + number.lstrip("+"))) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def reward_user_for_otp(user_id: int, amount: float):
    await update_user_balance(user_id, amount)

async def update_channel_id(channel_db_id: int, channel_id: str):
    """Update the resolved channel_id for a required channel."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE required_channels SET channel_id = ? WHERE id = ?",
            (channel_id, channel_db_id)
        )
        await db.commit()
