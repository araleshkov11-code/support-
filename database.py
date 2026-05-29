"""
database.py — SQLite через aiosqlite.
Добавлена поддержка forum thread_id для каждого пользователя.
"""

import aiosqlite

DB_PATH = "bot.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                thread_id   INTEGER,           -- ID темы в форуме
                is_banned   INTEGER DEFAULT 0,
                joined_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                message     TEXT NOT NULL,
                reply       TEXT,
                file_id     TEXT,
                file_type   TEXT,
                status      TEXT DEFAULT 'open',
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            -- Миграция: добавить thread_id если таблица уже существует без него
            CREATE TABLE IF NOT EXISTS _migrations (key TEXT PRIMARY KEY);
        """)

        # Безопасная миграция: добавить колонку thread_id если её нет
        try:
            await db.execute("ALTER TABLE users ADD COLUMN thread_id INTEGER")
        except Exception:
            pass

        await db.commit()


# ════════════════════════ Users ════════════════════════

async def add_user(user_id: int, username: str | None, full_name: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name
        """, (user_id, username, full_name))
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_users(only_active: bool = False) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM users"
        if only_active:
            q += " WHERE is_banned = 0"
        q += " ORDER BY joined_at DESC"
        async with db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def is_banned(user_id: int) -> bool:
    user = await get_user(user_id)
    return bool(user and user["is_banned"])


async def set_ban(user_id: int, banned: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (1 if banned else 0, user_id)
        )
        await db.commit()


# ════════════════════════ Forum threads ════════════════════════

async def get_user_thread(user_id: int) -> int | None:
    """Возвращает thread_id темы пользователя в форуме."""
    user = await get_user(user_id)
    if user:
        return user.get("thread_id")
    return None


async def set_user_thread(user_id: int, thread_id: int):
    """Сохраняет thread_id темы для пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET thread_id = ? WHERE user_id = ?",
            (thread_id, user_id)
        )
        await db.commit()


async def get_user_by_thread(thread_id: int) -> int | None:
    """Возвращает user_id по thread_id форума."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id FROM users WHERE thread_id = ?", (thread_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["user_id"] if row else None


# ════════════════════════ Tickets ════════════════════════

async def create_ticket(user_id: int, username: str | None, message: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tickets (user_id, username, message) VALUES (?, ?, ?)",
            (user_id, username, message)
        )
        await db.commit()
        return cur.lastrowid


async def get_ticket(ticket_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_tickets(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
            (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_tickets_by_status(statuses: list[str]) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        ph = ",".join("?" * len(statuses))
        async with db.execute(
            f"SELECT * FROM tickets WHERE status IN ({ph}) ORDER BY created_at DESC",
            statuses
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_tickets() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def set_ticket_reply(ticket_id: int, reply: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tickets SET reply=?, status='answered', updated_at=datetime('now') WHERE id=?",
            (reply, ticket_id)
        )
        await db.commit()


async def set_ticket_status(ticket_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tickets SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, ticket_id)
        )
        await db.commit()


async def set_latest_ticket_answered(user_id: int):
    """Помечает последнее открытое обращение пользователя как отвеченное."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE tickets SET status='answered', updated_at=datetime('now')
            WHERE id = (
                SELECT id FROM tickets
                WHERE user_id = ? AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1
            )
        """, (user_id,))
        await db.commit()


async def update_ticket_media(ticket_id: int, file_id: str, file_type: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tickets SET file_id=?, file_type=? WHERE id=?",
            (file_id, file_type, ticket_id)
        )
        await db.commit()


# ════════════════════════ Stats ════════════════════════

async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async def count(q, *args):
            async with db.execute(q, args) as c:
                return (await c.fetchone())[0]

        return {
            "total_users":     await count("SELECT COUNT(*) FROM users"),
            "banned_users":    await count("SELECT COUNT(*) FROM users WHERE is_banned=1"),
            "total_tickets":   await count("SELECT COUNT(*) FROM tickets"),
            "open_tickets":    await count("SELECT COUNT(*) FROM tickets WHERE status='open'"),
            "answered_tickets":await count("SELECT COUNT(*) FROM tickets WHERE status='answered'"),
            "closed_tickets":  await count("SELECT COUNT(*) FROM tickets WHERE status='closed'"),
        }
