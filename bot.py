"""
Telegram Support Bot с поддержкой групп с темами (Forum Topics).
Каждый пользователь получает отдельную тему в группе-форуме.
Поддерживает все типы медиафайлов.

pip install aiogram==3.13.1 aiosqlite==0.20.0
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    ForumTopic,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

import database as db
from config import BOT_TOKEN, ADMIN_IDS, FORUM_GROUP_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())


# ════════════════════════════════════════════════════════
#  FSM States
# ════════════════════════════════════════════════════════

class UserStates(StatesGroup):
    waiting_message = State()

class AdminStates(StatesGroup):
    replying = State()       # fallback — если нет форума
    broadcasting = State()
    banning_user = State()


# ════════════════════════════════════════════════════════
#  Keyboards
# ════════════════════════════════════════════════════════

def user_main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="✉️ Написать администратору")],
        [KeyboardButton(text="📋 Мои обращения"), KeyboardButton(text="ℹ️ О боте")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="❌ Отмена")]
    ], resize_keyboard=True)

def admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Активные обращения", callback_data="admin:new_tickets")],
        [InlineKeyboardButton(text="📂 Все обращения",      callback_data="admin:all_tickets")],
        [InlineKeyboardButton(text="👥 Пользователи",       callback_data="admin:users")],
        [InlineKeyboardButton(text="📢 Рассылка",           callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📊 Статистика",         callback_data="admin:stats")],
    ])

def tickets_list_kb(tickets: list, page: int = 0, prefix: str = "ticket"):
    per_page = 5
    start = page * per_page
    chunk = tickets[start:start + per_page]
    icons = {"open": "🔴", "answered": "🟡", "closed": "🟢"}
    buttons = []
    for t in chunk:
        icon = icons.get(t["status"], "⚪")
        name = t["username"] or str(t["user_id"])
        buttons.append([InlineKeyboardButton(
            text=f"{icon} #{t['id']} — {name} ({t['created_at'][:10]})",
            callback_data=f"{prefix}_view:{t['id']}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"{prefix}_page:{page-1}"))
    if start + per_page < len(tickets):
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"{prefix}_page:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="« Главная", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def ticket_action_kb(ticket_id: int, user_id: int, status: str, thread_id: int | None):
    buttons = []
    if not thread_id:  # только если форум НЕ настроен — показываем кнопку "Ответить"
        if status in ("open", "answered"):
            buttons.append([InlineKeyboardButton(
                text="💬 Ответить", callback_data=f"reply:{ticket_id}:{user_id}"
            )])
    if status != "closed":
        buttons.append([InlineKeyboardButton(
            text="✅ Закрыть тему", callback_data=f"close:{ticket_id}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="🔓 Переоткрыть", callback_data=f"reopen:{ticket_id}"
        )])
    buttons.append([
        InlineKeyboardButton(text="🚫 Бан/Разбан", callback_data=f"ban:{user_id}"),
        InlineKeyboardButton(text="« Назад", callback_data="admin:new_tickets"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def forum_enabled() -> bool:
    return bool(FORUM_GROUP_ID)

def get_media_info(message: Message) -> tuple[str | None, str | None, str | None]:
    """Возвращает (file_id, file_type, caption)"""
    caption = message.caption or ""
    if message.photo:
        return message.photo[-1].file_id, "photo", caption
    if message.video:
        return message.video.file_id, "video", caption
    if message.audio:
        return message.audio.file_id, "audio", caption
    if message.voice:
        return message.voice.file_id, "voice", caption
    if message.video_note:
        return message.video_note.file_id, "video_note", caption
    if message.document:
        return message.document.file_id, "document", caption
    if message.sticker:
        return message.sticker.file_id, "sticker", ""
    if message.animation:
        return message.animation.file_id, "animation", caption
    return None, None, None

async def send_media_to_user(user_id: int, file_id: str, file_type: str,
                              caption: str = "", thread_id: int | None = None):
    """Отправляет медиафайл пользователю или в тему форума."""
    target = user_id if not thread_id else FORUM_GROUP_ID
    kwargs = dict(chat_id=target)
    if thread_id:
        kwargs["message_thread_id"] = thread_id

    if file_type == "photo":
        await bot.send_photo(**kwargs, photo=file_id, caption=caption)
    elif file_type == "video":
        await bot.send_video(**kwargs, video=file_id, caption=caption)
    elif file_type == "audio":
        await bot.send_audio(**kwargs, audio=file_id, caption=caption)
    elif file_type == "voice":
        await bot.send_voice(**kwargs, voice=file_id, caption=caption)
    elif file_type == "video_note":
        await bot.send_video_note(**kwargs, video_note=file_id)
    elif file_type == "document":
        await bot.send_document(**kwargs, document=file_id, caption=caption)
    elif file_type == "sticker":
        await bot.send_sticker(**kwargs, sticker=file_id)
    elif file_type == "animation":
        await bot.send_animation(**kwargs, animation=file_id, caption=caption)

async def forward_to_topic(message: Message, thread_id: int, prefix: str = ""):
    """Пересылает любое сообщение в тему форума."""
    file_id, file_type, caption = get_media_info(message)
    text = message.text or ""
    full_caption = f"{prefix}{caption}" if caption else prefix

    if text:
        await bot.send_message(FORUM_GROUP_ID, f"{prefix}{text}",
                               message_thread_id=thread_id)
    elif file_id:
        await send_media_to_user(None, file_id, file_type,
                                  caption=full_caption, thread_id=thread_id)

async def forward_to_user(message: Message, user_id: int, prefix: str = ""):
    """Пересылает любое сообщение пользователю."""
    file_id, file_type, caption = get_media_info(message)
    text = message.text or ""
    full_caption = f"{prefix}{caption}" if caption else prefix

    if text:
        await bot.send_message(user_id, f"{prefix}{text}")
    elif file_id:
        await send_media_to_user(user_id, file_id, file_type, caption=full_caption)

async def get_or_create_topic(user_id: int, username: str | None,
                               full_name: str | None) -> int | None:
    """Возвращает thread_id темы пользователя, создаёт если нет."""
    if not forum_enabled():
        return None

    existing = await db.get_user_thread(user_id)
    if existing:
        return existing

    display = f"{full_name or ''} (@{username})" if username else (full_name or str(user_id))
    try:
        topic: ForumTopic = await bot.create_forum_topic(
            chat_id=FORUM_GROUP_ID,
            name=display[:128],
            icon_color=0x6FB9F0,
        )
        thread_id = topic.message_thread_id
        await db.set_user_thread(user_id, thread_id)

        # Шапка темы
        await bot.send_message(
            FORUM_GROUP_ID,
            f"👤 <b>Пользователь:</b> {display}\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"<i>Здесь будут сообщения этого пользователя.\n"
            f"Отвечайте прямо в эту тему — ответ придёт пользователю.</i>",
            message_thread_id=thread_id,
        )
        return thread_id
    except TelegramBadRequest as e:
        logger.error(f"Не удалось создать тему: {e}")
        return None

async def format_ticket(ticket: dict) -> str:
    status_map = {"open": "🔴 Открыто", "answered": "🟡 Отвечено", "closed": "🟢 Закрыто"}
    status = status_map.get(ticket["status"], ticket["status"])
    text = (
        f"📩 <b>Обращение #{ticket['id']}</b>\n"
        f"👤 {ticket['username'] or '—'} (ID: <code>{ticket['user_id']}</code>)\n"
        f"📅 {ticket['created_at'][:19].replace('T', ' ')}\n"
        f"📌 Статус: {status}\n\n"
        f"<b>Сообщение:</b>\n{ticket['message']}"
    )
    if ticket.get("reply"):
        text += f"\n\n<b>Ответ:</b>\n{ticket['reply']}"
    return text


# ════════════════════════════════════════════════════════
#  User handlers
# ════════════════════════════════════════════════════════

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.add_user(message.from_user.id, message.from_user.username,
                      message.from_user.full_name)

    if is_admin(message.from_user.id):
        await message.answer("👋 Добро пожаловать в панель администратора!",
                             reply_markup=admin_main_kb())
        return

    if await db.is_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы.")
        return

    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "Это бот для связи с администратором. Напишите ваше сообщение здесь и мы ответим вам в ближайшее время",
        reply_markup=user_main_kb()
    )


@dp.message(F.text == "✉️ Написать администратору")
async def user_write(message: Message, state: FSMContext):
    if await db.is_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы.")
        return
    await state.set_state(UserStates.waiting_message)
    await message.answer(
        "✏️ Отправьте сообщение администратору.\n",
        reply_markup=cancel_kb()
    )


@dp.message(F.text == "❌ Отмена")
async def user_cancel(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Панель администратора:", reply_markup=admin_main_kb())
    else:
        await message.answer("Отменено.", reply_markup=user_main_kb())


@dp.message(UserStates.waiting_message)
async def user_send_message(message: Message, state: FSMContext):
    await state.clear()

    file_id, file_type, caption = get_media_info(message)
    text = message.text or caption or ""

    if not text and not file_id:
        await message.answer("Пожалуйста, отправьте текст или медиафайл.")
        return

    ticket_id = await db.create_ticket(
        user_id=message.from_user.id,
        username=message.from_user.username,
        message=text or f"[{file_type}]"
    )
    if file_id:
        await db.update_ticket_media(ticket_id, file_id, file_type)

    await message.answer(
        f"✅ Обращение <b>#{ticket_id}</b> отправлено!\n",
        reply_markup=user_main_kb()
    )

    # ── Форум-режим ──
    if forum_enabled():
        thread_id = await get_or_create_topic(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name
        )
        if thread_id:
            prefix = f"📩 <b>Обращение #{ticket_id}</b>\n"
            await forward_to_topic(message, thread_id, prefix)
            return  # дальше не уведомляем личку админов

    # ── Fallback: уведомление в личку администраторов ──
    ticket = await db.get_ticket(ticket_id)
    ticket_text = await format_ticket(ticket)
    kb = ticket_action_kb(ticket_id, message.from_user.id, "open", None)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id,
                f"🔔 <b>Новое обращение!</b>\n\n{ticket_text}", reply_markup=kb)
            if file_id:
                await send_media_to_user(admin_id, file_id, file_type, caption)
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")


@dp.message(F.text == "📋 Мои обращения")
async def user_my_tickets(message: Message):
    tickets = await db.get_user_tickets(message.from_user.id)
    if not tickets:
        await message.answer("У вас ещё нет обращений.", reply_markup=user_main_kb())
        return
    icons = {"open": "🔴", "answered": "🟡", "closed": "🟢"}
    lines = ["<b>Ваши последние обращения:</b>\n"]
    for t in tickets[-10:]:
        icon = icons.get(t["status"], "⚪")
        preview = (t["message"] or "")[:50]
        lines.append(f"{icon} <b>#{t['id']}</b> ({t['created_at'][:10]}) — {preview}")
    await message.answer("\n".join(lines), reply_markup=user_main_kb())


# ════════════════════════════════════════════════════════
#  Forum: ответы из темы → пользователю
# ════════════════════════════════════════════════════════

@dp.message(F.chat.id == FORUM_GROUP_ID if FORUM_GROUP_ID else F.chat.id == 0)
async def forum_reply_handler(message: Message):
    """
    Обрабатывает сообщения из группы-форума.
    Если сообщение в теме (thread) — пересылает его пользователю.
    """
    # Игнорируем сообщения не в теме и системные
    if not message.message_thread_id:
        return
    # Игнорируем сообщения от самого бота
    if message.from_user and message.from_user.is_bot:
        return

    thread_id = message.message_thread_id
    user_id = await db.get_user_by_thread(thread_id)
    if not user_id:
        return  # тема не привязана к пользователю

    if await db.is_banned(user_id):
        await message.reply("🚫 Пользователь заблокирован.")
        return

    sender_name = message.from_user.full_name or "Администратор"
    prefix = f"📬 <b>Ответ от администратора</b> ({sender_name}):\n\n"

    try:
        await forward_to_user(message, user_id, prefix)
        # Обновляем статус последнего тикета пользователя
        await db.set_latest_ticket_answered(user_id)
    except Exception as e:
        logger.error(f"Ошибка пересылки пользователю {user_id}: {e}")
        await message.reply(f"⚠️ Не удалось доставить сообщение пользователю: {e}")


# ════════════════════════════════════════════════════════
#  Admin panel — команды и callbacks
# ════════════════════════════════════════════════════════

@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🛠 <b>Панель администратора</b>",
                         reply_markup=admin_main_kb())


@dp.callback_query(F.data == "admin:panel")
async def cb_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("🛠 <b>Панель администратора</b>",
                                     reply_markup=admin_main_kb())


@dp.callback_query(F.data == "admin:stats")
async def cb_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    s = await db.get_stats()
    mode = "🗂 Форум-группа" if forum_enabled() else "💬 Личные сообщения"
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"🔧 Режим: {mode}\n\n"
        f"👥 Всего пользователей: <b>{s['total_users']}</b>\n"
        f"🚫 Заблокировано: <b>{s['banned_users']}</b>\n"
        f"📨 Всего обращений: <b>{s['total_tickets']}</b>\n"
        f"🔴 Открытых: <b>{s['open_tickets']}</b>\n"
        f"🟡 Отвеченных: <b>{s['answered_tickets']}</b>\n"
        f"🟢 Закрытых: <b>{s['closed_tickets']}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="admin:panel")]
        ])
    )


@dp.callback_query(F.data == "admin:new_tickets")
async def cb_new_tickets(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    tickets = await db.get_tickets_by_status(["open", "answered"])
    if not tickets:
        await callback.message.edit_text(
            "✅ Нет активных обращений!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="admin:panel")]
            ])
        )
        return
    await callback.message.edit_text(
        f"📨 <b>Активные обращения</b> ({len(tickets)}):",
        reply_markup=tickets_list_kb(tickets, prefix="ticket")
    )


@dp.callback_query(F.data == "admin:all_tickets")
async def cb_all_tickets(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    tickets = await db.get_all_tickets()
    if not tickets:
        await callback.message.edit_text(
            "Обращений пока нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="admin:panel")]
            ])
        )
        return
    await callback.message.edit_text(
        f"📂 <b>Все обращения</b> ({len(tickets)}):",
        reply_markup=tickets_list_kb(tickets, prefix="all")
    )


@dp.callback_query(F.data.startswith("ticket_view:") | F.data.startswith("all_view:"))
async def cb_view_ticket(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    ticket_id = int(callback.data.split(":")[1])
    ticket = await db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("Обращение не найдено.")
        return
    thread_id = await db.get_user_thread(ticket["user_id"])
    text = await format_ticket(ticket)
    if thread_id and forum_enabled():
        text += f"\n\n🔗 <a href='https://t.me/c/{str(FORUM_GROUP_ID)[4:]}/{thread_id}'>Открыть тему в форуме</a>"
    kb = ticket_action_kb(ticket_id, ticket["user_id"], ticket["status"], thread_id)
    await callback.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("ticket_page:"))
async def cb_ticket_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    page = int(callback.data.split(":")[1])
    tickets = await db.get_tickets_by_status(["open", "answered"])
    await callback.message.edit_text(
        f"📨 <b>Активные обращения</b> ({len(tickets)}):",
        reply_markup=tickets_list_kb(tickets, page, prefix="ticket")
    )


@dp.callback_query(F.data.startswith("all_page:"))
async def cb_all_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    page = int(callback.data.split(":")[1])
    tickets = await db.get_all_tickets()
    await callback.message.edit_text(
        f"📂 <b>Все обращения</b> ({len(tickets)}):",
        reply_markup=tickets_list_kb(tickets, page, prefix="all")
    )


@dp.callback_query(F.data.startswith("close:"))
async def cb_close(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    ticket_id = int(callback.data.split(":")[1])
    ticket = await db.get_ticket(ticket_id)
    await db.set_ticket_status(ticket_id, "closed")

    thread_id = await db.get_user_thread(ticket["user_id"])
    if thread_id and forum_enabled():
        try:
            await bot.close_forum_topic(FORUM_GROUP_ID, thread_id)
        except Exception:
            pass

    await callback.answer("✅ Тема закрыта.")
    ticket = await db.get_ticket(ticket_id)
    kb = ticket_action_kb(ticket_id, ticket["user_id"], "closed", thread_id)
    await callback.message.edit_text(await format_ticket(ticket), reply_markup=kb)


@dp.callback_query(F.data.startswith("reopen:"))
async def cb_reopen(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    ticket_id = int(callback.data.split(":")[1])
    ticket = await db.get_ticket(ticket_id)
    await db.set_ticket_status(ticket_id, "open")

    thread_id = await db.get_user_thread(ticket["user_id"])
    if thread_id and forum_enabled():
        try:
            await bot.reopen_forum_topic(FORUM_GROUP_ID, thread_id)
        except Exception:
            pass

    await callback.answer("🔓 Тема переоткрыта.")
    ticket = await db.get_ticket(ticket_id)
    kb = ticket_action_kb(ticket_id, ticket["user_id"], "open", thread_id)
    await callback.message.edit_text(await format_ticket(ticket), reply_markup=kb)


# ── Ответ вручную (только без форума) ────────────────────

@dp.callback_query(F.data.startswith("reply:"))
async def cb_reply(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    _, ticket_id, user_id = callback.data.split(":")
    await state.set_state(AdminStates.replying)
    await state.update_data(ticket_id=int(ticket_id), user_id=int(user_id))
    await callback.message.answer(
        f"✏️ Введите ответ <b>#{ticket_id}</b>:",
        reply_markup=cancel_kb()
    )
    await callback.answer()


@dp.message(AdminStates.replying)
async def admin_do_reply(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    ticket_id, user_id = data["ticket_id"], data["user_id"]
    await state.clear()

    file_id, file_type, caption = get_media_info(message)
    reply_text = message.text or caption or f"[{file_type}]"

    await db.set_ticket_reply(ticket_id, reply_text)

    try:
        prefix = f"📬 <b>Ответ на ваше сообщение #{ticket_id}</b>\n<i>— Администратор</i>\n\n"
        await forward_to_user(message, user_id, prefix)
        confirm = "✅ Ответ отправлен!"
    except Exception as e:
        confirm = f"⚠️ Ошибка: {e}"

    await message.answer(confirm, reply_markup=ReplyKeyboardRemove())
    await message.answer("Панель администратора:", reply_markup=admin_main_kb())


# ── Пользователи / бан ───────────────────────────────────

@dp.callback_query(F.data == "admin:users")
async def cb_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    users = await db.get_all_users()
    lines = [f"👥 <b>Пользователи ({len(users)}):</b>\n"]
    for u in users[-20:]:
        icon = "🚫" if u["is_banned"] else "✅"
        name = u["username"] or u["full_name"] or "—"
        lines.append(f"{icon} <code>{u['user_id']}</code> — {name}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Бан/Разбан по ID", callback_data="admin:ban_input")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:panel")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)


@dp.callback_query(F.data.startswith("ban:"))
async def cb_ban(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[1])
    user = await db.get_user(user_id)
    if not user:
        await callback.answer("Пользователь не найден.")
        return
    new_status = not user["is_banned"]
    await db.set_ban(user_id, new_status)
    action = "заблокирован" if new_status else "разблокирован"
    await callback.answer(f"Пользователь {action}.")
    try:
        msg = "🚫 Вы заблокированы." if new_status else "✅ Вы разблокированы."
        await bot.send_message(user_id, msg)
    except Exception:
        pass

    # Закрыть/переоткрыть тему в форуме
    thread_id = await db.get_user_thread(user_id)
    if thread_id and forum_enabled():
        try:
            if new_status:
                await bot.close_forum_topic(FORUM_GROUP_ID, thread_id)
            else:
                await bot.reopen_forum_topic(FORUM_GROUP_ID, thread_id)
        except Exception:
            pass


@dp.callback_query(F.data == "admin:ban_input")
async def cb_ban_input(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.banning_user)
    await callback.message.answer("Введите ID пользователя:", reply_markup=cancel_kb())
    await callback.answer()


@dp.message(AdminStates.banning_user)
async def admin_ban_by_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Неверный ID.")
        return
    await state.clear()
    user = await db.get_user(user_id)
    if not user:
        await message.answer("Пользователь не найден.", reply_markup=ReplyKeyboardRemove())
    else:
        new_status = not user["is_banned"]
        await db.set_ban(user_id, new_status)
        action = "заблокирован" if new_status else "разблокирован"
        await message.answer(f"✅ Пользователь {user_id} {action}.",
                             reply_markup=ReplyKeyboardRemove())
    await message.answer("Панель администратора:", reply_markup=admin_main_kb())


# ── Рассылка ─────────────────────────────────────────────

@dp.callback_query(F.data == "admin:broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.broadcasting)
    await callback.message.answer(
        "📢 Отправьте сообщение для рассылки.\n"
        "Поддерживается текст (HTML), фото, видео, документы и т.д.\n"
        "Будет отправлено всем незаблокированным пользователям.",
        reply_markup=cancel_kb()
    )
    await callback.answer()


@dp.message(AdminStates.broadcasting)
async def admin_do_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    users = await db.get_all_users(only_active=True)
    sent, failed = 0, 0
    for user in users:
        if user["user_id"] in ADMIN_IDS:
            continue
        try:
            await forward_to_user(message, user["user_id"])
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"📢 Рассылка завершена!\n✅ Отправлено: {sent}\n❌ Ошибок: {failed}",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer("Панель администратора:", reply_markup=admin_main_kb())


# ════════════════════════════════════════════════════════
#  Entry point
# ════════════════════════════════════════════════════════

async def main():
    await db.init_db()
    if forum_enabled():
        logger.info(f"Режим: Форум-группа (ID: {FORUM_GROUP_ID})")
    else:
        logger.info("Режим: Личные сообщения администраторам")
    logger.info("Бот запущен!")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
