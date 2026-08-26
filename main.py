import asyncio
import logging
import sqlite3
import json
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest, DeletePhotosRequest, GetUserPhotosRequest
from telethon.tl.types import InputPhoto
from telethon.errors import SessionPasswordNeededError

# ==================== КОНФИГ ====================
BOT_TOKEN = "8642683935:AAHFlaXgroXtlxNyEtZUhmJgSJ2Vq_0vyRk"
ADMIN_ID = 7545129896
DEFAULT_PASSWORD = "tbl_kto66666677"

DB_NAME = "bot.db"
PHOTOS_DIR = "user_photos"
os.makedirs(PHOTOS_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)

# ==================== БАЗА ДАННЫХ ====================
def get_conn():
    return sqlite3.connect(DB_NAME)

async def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT 0,
        api_id TEXT,
        api_hash TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS passwords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        password TEXT UNIQUE,
        created_by INTEGER,
        used BOOLEAN DEFAULT 0,
        used_by INTEGER DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("INSERT OR IGNORE INTO passwords (password, created_by, used) VALUES (?, ?, 0)",
              (DEFAULT_PASSWORD, ADMIN_ID))
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY,
        session_string TEXT,
        phone TEXT,
        connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'open'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS autoreply_settings (
        user_id INTEGER PRIMARY KEY,
        enabled BOOLEAN DEFAULT 0,
        reply_text TEXT DEFAULT '',
        delay_seconds INTEGER DEFAULT 0,
        except_users TEXT DEFAULT '[]'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS fake_accounts (
        user_id INTEGER PRIMARY KEY,
        enabled BOOLEAN DEFAULT 0,
        original_name TEXT,
        original_last_name TEXT,
        original_about TEXT,
        original_username TEXT,
        original_photo_id TEXT,
        photo_files TEXT
    )''')
    conn.commit()
    conn.close()

# ==================== МИДЛВАРЬ ====================
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, types.CallbackQuery):
            return await handler(event, data)
        if isinstance(event, types.Message):
            if event.text and (event.text.startswith('/start') or event.text.startswith('/panel')):
                return await handler(event, data)
            if data.get('state'):
                current_state = await data['state'].get_state()
                allowed_states = [
                    PasswordStates.waiting_password.state,
                    ConnectStates.waiting_api_id.state,
                    ConnectStates.waiting_api_hash.state,
                    ConnectStates.waiting_phone.state,
                    ConnectStates.waiting_code.state,
                    ConnectStates.waiting_2fa.state,
                    TicketState.waiting_message.state,
                    AdminCreatePass.waiting_new_pass.state,
                    AdminDeletePass.waiting_pass_id.state,
                    ReplySettings.waiting_text.state,
                    ReplySettings.waiting_delay.state,
                    ReplySettings.waiting_except.state
                ]
                if current_state in allowed_states:
                    return await handler(event, data)
            user_id = event.from_user.id
            conn = get_conn()
            c = conn.cursor()
            c.execute("SELECT is_active FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0] == 1:
                return await handler(event, data)
            else:
                await event.answer("❌ Нет доступа. Введите пароль через /start.")
                return
        return await handler(event, data)

# ==================== СОСТОЯНИЯ FSM ====================
class PasswordStates(StatesGroup):
    waiting_password = State()

class ConnectStates(StatesGroup):
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

class TicketState(StatesGroup):
    waiting_message = State()

class AdminCreatePass(StatesGroup):
    waiting_new_pass = State()

class AdminDeletePass(StatesGroup):
    waiting_pass_id = State()

class ReplySettings(StatesGroup):
    waiting_text = State()
    waiting_delay = State()
    waiting_except = State()

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
user_clients = {}

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def show_main_menu(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Подключить аккаунт", callback_data="connect_account")],
        [InlineKeyboardButton(text="⚙️ Настройки автоответа", callback_data="autoreply_settings")],
        [InlineKeyboardButton(text="❄️ Фейк удаленный аккаунт", callback_data="fake_account")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="ticket")],
        [InlineKeyboardButton(text="🚪 Выйти", callback_data="logout")]
    ])
    await message.answer("🏠 Главное меню:", reply_markup=keyboard)

async def is_account_connected(user_id: int) -> bool:
    return user_id in user_clients and user_clients[user_id].is_connected()

# ==================== /start ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
              (user.id, user.username, user.first_name))
    c.execute("SELECT is_active FROM users WHERE user_id = ?", (user.id,))
    is_active = c.fetchone()[0]
    conn.commit()
    conn.close()

    if is_active == 1:
        await show_main_menu(message)
        return

    text = ("Приветствую, AutoReply - это многофункциональный автоответчик созданный представителем @justmench1k.\n\n"
            "Заплатив небольшую сумму ты уберёшь из своей жизни статус нищеты и получишь много возможностей связанные с авто ответом.\n\n"
            "Каждая покупка даёт мне мотивацию расти и продвигаться вперёд :)")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Купить", callback_data="buy")],
        [InlineKeyboardButton(text="🔑 Вход", callback_data="enter")]
    ])
    await message.answer(text, reply_markup=keyboard)

@dp.callback_query(F.data == "buy")
async def buy_callback(callback: types.CallbackQuery):
    text = ("Способы оплаты: звёзды.\n"
            "Шаг 1: кликайте на кнопку ниже\n"
            "Шаг 2: нажимайте start > профиль > пополнить баланс > выбираете способ оплаты > пополняете на сумму 100 рублей\n"
            "Шаг 3: заходите в главное меню > звёзды и премиум > звёзды > вводите @justmench1k (не ошибитесь) > вводите \"70\"\n"
            "Шаг 4: готово, после оплаты ожидайте в течении 24 часов вам скинут в чат с ботом пароль для входа :)")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перейти в @Novave_bot", url="https://t.me/Novave_bot")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "enter")
async def enter_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(PasswordStates.waiting_password)
    await callback.message.edit_text("🔑 Введите пароль для входа:")
    await callback.answer()

# ==================== ВВОД ПАРОЛЯ ====================
@dp.message(PasswordStates.waiting_password)
async def process_password(message: types.Message, state: FSMContext):
    entered = message.text.strip()
    user_id = message.from_user.id
    conn = get_conn()
    c = conn.cursor()
    
    c.execute("SELECT is_active FROM users WHERE user_id = ?", (user_id,))
    active_row = c.fetchone()
    if active_row and active_row[0] == 1:
        conn.close()
        await state.clear()
        await message.answer("✅ Вы уже авторизованы.")
        await show_main_menu(message)
        return
    
    c.execute("SELECT id, password, used_by FROM passwords WHERE password = ? AND used = 0", (entered,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE passwords SET used = 1, used_by = ? WHERE id = ?", (user_id, row[0]))
        c.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer("✅ Доступ получен! Теперь вы можете пользоваться автоответчиком.")
        await show_main_menu(message)
    else:
        c.execute("SELECT used_by FROM passwords WHERE password = ?", (entered,))
        used_row = c.fetchone()
        conn.close()
        if used_row and used_row[0] == user_id:
            conn2 = get_conn()
            c2 = conn2.cursor()
            c2.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (user_id,))
            conn2.commit()
            conn2.close()
            await state.clear()
            await message.answer("✅ Вы уже использовали этот пароль. Доступ восстановлен.")
            await show_main_menu(message)
        elif used_row:
            await message.answer("❌ Этот пароль уже был использован другим пользователем.")
        else:
            await message.answer("❌ Неверный пароль. Попробуйте снова или купите доступ.")
        await state.set_state(PasswordStates.waiting_password)

# ==================== ВЫХОД ====================
@dp.callback_query(F.data == "logout")
async def logout(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    if user_id in user_clients:
        await user_clients[user_id].disconnect()
        del user_clients[user_id]
    await callback.message.edit_text("✅ Вы вышли из системы. Для входа используйте /start.")
    await callback.answer()
    # ==================== ПОДКЛЮЧЕНИЕ АККАУНТА (SMS) ====================
@dp.callback_query(F.data == "connect_account")
async def start_connect(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT api_id, api_hash FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0] and row[1]:
        await state.update_data(api_id=int(row[0]), api_hash=row[1])
        await state.set_state(ConnectStates.waiting_phone)
        await callback.message.edit_text("📱 Введите номер телефона в международном формате (например +79001234567):")
        await callback.answer()
        return

    await state.set_state(ConnectStates.waiting_api_id)
    await callback.message.edit_text(
        "Для подключения аккаунта нужны API_ID и API_HASH.\n"
        "Получить их можно на my.telegram.org/apps\n\n"
        "Введите ваш API_ID (число):"
    )
    await callback.answer()

@dp.message(ConnectStates.waiting_api_id)
async def process_api_id(message: types.Message, state: FSMContext):
    try:
        api_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число (API_ID).")
        return
    await state.update_data(api_id=api_id)
    await state.set_state(ConnectStates.waiting_api_hash)
    await message.answer("Введите ваш API_HASH (строка):")

@dp.message(ConnectStates.waiting_api_hash)
async def process_api_hash(message: types.Message, state: FSMContext):
    api_hash = message.text.strip()
    if not api_hash:
        await message.answer("❌ API_HASH не может быть пустым.")
        return
    await state.update_data(api_hash=api_hash)
    user_id = message.from_user.id
    data = await state.get_data()
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET api_id = ?, api_hash = ? WHERE user_id = ?",
              (str(data['api_id']), data['api_hash'], user_id))
    conn.commit()
    conn.close()
    await state.set_state(ConnectStates.waiting_phone)
    await message.answer("📱 Введите номер телефона в международном формате (например +79001234567):")

@dp.message(ConnectStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith('+'):
        await message.answer("❌ Номер должен начинаться с + и кода страны (например +79001234567).")
        return
    await state.update_data(phone=phone)
    data = await state.get_data()
    api_id = data['api_id']
    api_hash = data['api_hash']
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            await finalize_connection(message, client, state)
            return
        await client.send_code_request(phone)
        await state.update_data(client=client)
        await state.set_state(ConnectStates.waiting_code)
        await message.answer("📨 Код подтверждения отправлен в Telegram. Введите его:")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nПроверьте номер и попробуйте снова.")
        await state.set_state(ConnectStates.waiting_phone)

@dp.message(ConnectStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    client = data.get('client')
    phone = data.get('phone')
    if not client:
        await message.answer("❌ Ошибка сессии. Начните заново /start")
        return
    try:
        await client.sign_in(phone, code)
        await finalize_connection(message, client, state)
    except SessionPasswordNeededError:
        await state.set_state(ConnectStates.waiting_2fa)
        await message.answer("🔐 Требуется двухфакторная аутентификация. Введите пароль 2FA:")
    except Exception as e:
        await message.answer(f"❌ Неверный код или ошибка: {e}\nПопробуйте снова.")
        await state.set_state(ConnectStates.waiting_code)

@dp.message(ConnectStates.waiting_2fa)
async def process_2fa(message: types.Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    client = data.get('client')
    if not client:
        await message.answer("❌ Ошибка сессии. Начните заново /start")
        return
    try:
        await client.sign_in(password=password)
        await finalize_connection(message, client, state)
    except Exception as e:
        await message.answer(f"❌ Неверный пароль 2FA: {e}\nПопробуйте снова.")
        await state.set_state(ConnectStates.waiting_2fa)

async def finalize_connection(message, client, state):
    session_str = client.session.save()
    user_id = message.from_user.id
    conn = get_conn()
    c = conn.cursor()
    c.execute("REPLACE INTO sessions (user_id, session_string, phone) VALUES (?, ?, ?)",
              (user_id, session_str, "sms"))
    conn.commit()
    conn.close()
    user_clients[user_id] = client
    await state.clear()
    await message.answer("✅ Аккаунт успешно подключён! Теперь автоответчик активен.")
    asyncio.create_task(start_listener(user_id))
    await show_main_menu(message)

# ==================== АВТООТВЕТЧИК ====================
async def start_listener(user_id):
    client = user_clients.get(user_id)
    if not client:
        return
    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if event.is_private and not event.message.out:
            sender = await event.get_sender()
            if sender.id == user_id:
                return
            conn = get_conn()
            c = conn.cursor()
            c.execute("SELECT enabled, reply_text, delay_seconds, except_users FROM autoreply_settings WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0] == 1:
                except_list = json.loads(row[3]) if row[3] else []
                if sender.id in except_list:
                    return
                if row[2] > 0:
                    await asyncio.sleep(row[2])
                try:
                    await event.reply(row[1] if row[1] else "Автоответ: я сейчас занят, отвечу позже.")
                except Exception as e:
                    logging.error(f"Auto reply error: {e}")
    if not client.is_connected():
        await client.connect()

async def load_all_clients():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, session_string FROM sessions")
    rows = c.fetchall()
    conn.close()
    for user_id, session_str in rows:
        conn2 = get_conn()
        c2 = conn2.cursor()
        c2.execute("SELECT api_id, api_hash FROM users WHERE user_id = ?", (user_id,))
        row2 = c2.fetchone()
        conn2.close()
        if row2 and row2[0] and row2[1]:
            api_id = int(row2[0])
            api_hash = row2[1]
            client = TelegramClient(StringSession(session_str), api_id, api_hash)
            try:
                await client.connect()
                if await client.is_user_authorized():
                    user_clients[user_id] = client
                    asyncio.create_task(start_listener(user_id))
            except Exception as e:
                logging.error(f"Failed to load session: {e}")

# ==================== НАСТРОЙКИ АВТООТВЕТА ====================
@dp.callback_query(F.data == "autoreply_settings")
async def settings_menu(callback: types.CallbackQuery):
    if not await is_account_connected(callback.from_user.id):
        await callback.message.edit_text("❌ Сначала подключите аккаунт в главном меню.")
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Включить/выключить", callback_data="toggle_autoreply")],
        [InlineKeyboardButton(text="Изменить текст ответа", callback_data="change_reply_text")],
        [InlineKeyboardButton(text="Задержка (сек)", callback_data="change_delay")],
        [InlineKeyboardButton(text="Исключения (ID)", callback_data="change_except")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])
    await callback.message.edit_text("⚙️ Настройки автоответа:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "toggle_autoreply")
async def toggle_autoreply(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_account_connected(user_id):
        await callback.message.edit_text("❌ Сначала подключите аккаунт.")
        await callback.answer()
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT enabled FROM autoreply_settings WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        c.execute("UPDATE autoreply_settings SET enabled = ? WHERE user_id = ?", (new_val, user_id))
    else:
        new_val = 1
        c.execute("INSERT INTO autoreply_settings (user_id, enabled) VALUES (?, ?)", (user_id, new_val))
    conn.commit()
    conn.close()
    status = "включён" if new_val else "выключен"
    await callback.message.edit_text(f"✅ Автоответ {status}.")
    await callback.answer()

@dp.callback_query(F.data == "change_reply_text")
async def change_text(callback: types.CallbackQuery, state: FSMContext):
    if not await is_account_connected(callback.from_user.id):
        await callback.message.edit_text("❌ Сначала подключите аккаунт.")
        await callback.answer()
        return
    await state.set_state(ReplySettings.waiting_text)
    await callback.message.edit_text("Введите новый текст автоответа:")

@dp.message(ReplySettings.waiting_text)
async def set_reply_text(message: types.Message, state: FSMContext):
    text = message.text
    user_id = message.from_user.id
    conn = get_conn()
    c = conn.cursor()
    c.execute("REPLACE INTO autoreply_settings (user_id, reply_text) VALUES (?, ?)", (user_id, text))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Текст автоответа обновлён.")
    await show_main_menu(message)

@dp.callback_query(F.data == "change_delay")
async def change_delay(callback: types.CallbackQuery, state: FSMContext):
    if not await is_account_connected(callback.from_user.id):
        await callback.message.edit_text("❌ Сначала подключите аккаунт.")
        await callback.answer()
        return
    await state.set_state(ReplySettings.waiting_delay)
    await callback.message.edit_text("Введите задержку в секундах (число):")

@dp.message(ReplySettings.waiting_delay)
async def set_delay(message: types.Message, state: FSMContext):
    try:
        delay = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    user_id = message.from_user.id
    conn = get_conn()
    c = conn.cursor()
    c.execute("REPLACE INTO autoreply_settings (user_id, delay_seconds) VALUES (?, ?)", (user_id, delay))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"✅ Задержка установлена: {delay} сек.")
    await show_main_menu(message)

@dp.callback_query(F.data == "change_except")
async def change_except(callback: types.CallbackQuery, state: FSMContext):
    if not await is_account_connected(callback.from_user.id):
        await callback.message.edit_text("❌ Сначала подключите аккаунт.")
        await callback.answer()
        return
    await state.set_state(ReplySettings.waiting_except)
    await callback.message.edit_text("Введите ID пользователей, которым не отвечать, через запятую (например: 123456,789012):")

@dp.message(ReplySettings.waiting_except)
async def set_except(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    ids = [int(x.strip()) for x in raw.split(',') if x.strip().isdigit()]
    user_id = message.from_user.id
    conn = get_conn()
    c = conn.cursor()
    c.execute("REPLACE INTO autoreply_settings (user_id, except_users) VALUES (?, ?)", (user_id, json.dumps(ids)))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"✅ Исключения обновлены: {ids}")
    await show_main_menu(message)

# ==================== ФЕЙК УДАЛЕННЫЙ АККАУНТ ====================
@dp.callback_query(F.data == "fake_account")
async def fake_account_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_account_connected(user_id):
        await callback.message.edit_text("❌ Сначала подключите аккаунт в главном меню.")
        await callback.answer()
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT enabled FROM fake_accounts WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    current_status = "включена" if row and row[0] == 1 else "выключена"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Включить" if not row or row[0] == 0 else "Выключить", callback_data="fake_toggle")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])
    await callback.message.edit_text(
        f"❄️ Фейк удаленный аккаунт: {current_status}\n\n"
        "При включении бот изменит ваш профиль:\n"
        "— Имя → ❄️ Удалённый аккаунт\n"
        "— Фамилия → удалится\n"
        "— Описание → удалится\n"
        "— Все фото профиля → удалятся\n\n"
        "При выключении все данные будут восстановлены.",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "fake_toggle")
async def fake_toggle(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    client = user_clients.get(user_id)
    if not client:
        await callback.message.edit_text("❌ Аккаунт не подключён.")
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT enabled FROM fake_accounts WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    enabled = row[0] if row else 0

    if enabled == 0:
        try:
            me = await client.get_me()
            original_name = me.first_name or ""
            original_last = me.last_name or ""
            original_about = me.about or ""
            original_username = me.username or ""

            photos = await client(GetUserPhotosRequest(user_id, offset=0, max_id=0, limit=100))
            photo_files = []
            for i, photo in enumerate(photos.photos):
                file_path = os.path.join(PHOTOS_DIR, f"{user_id}_{i}.jpg")
                await client.download_media(photo, file=file_path)
                photo_files.append(file_path)

            main_photo_id = photos.photos[0].id if photos.photos else None

            c.execute("INSERT OR REPLACE INTO fake_accounts (user_id, enabled, original_name, original_last_name, original_about, original_username, original_photo_id, photo_files) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (user_id, 1, original_name, original_last, original_about, original_username, str(main_photo_id), json.dumps(photo_files)))
            conn.commit()

            await client(UpdateProfileRequest(first_name="❄️ Удалённый аккаунт", last_name=""))
            await client(UpdateProfileRequest(about=""))
            if photos.photos:
                await client(DeletePhotosRequest([InputPhoto(pid=p.id, access_hash=p.access_hash, file_reference=p.file_reference) for p in photos.photos]))

            await callback.message.edit_text("✅ Фейк удаленный аккаунт включён! Профиль изменён.")
        except Exception as e:
            await callback.message.edit_text(f"❌ Ошибка: {e}")
    else:
        c.execute("SELECT original_name, original_last_name, original_about, original_username, photo_files FROM fake_accounts WHERE user_id = ?", (user_id,))
        data = c.fetchone()
        if data:
            orig_name, orig_last, orig_about, orig_username, photo_files_json = data
            photo_files = json.loads(photo_files_json) if photo_files_json else []

            await client(UpdateProfileRequest(first_name=orig_name, last_name=orig_last))
            await client(UpdateProfileRequest(about=orig_about))

            for file_path in photo_files:
                if os.path.exists(file_path):
                    try:
                        await client(UploadProfilePhotoRequest(file=file_path))
                    except:
                        pass

            c.execute("DELETE FROM fake_accounts WHERE user_id = ?", (user_id,))
            conn.commit()
            await callback.message.edit_text("✅ Фейк удаленный аккаунт выключен. Профиль восстановлен.")
        else:
            await callback.message.edit_text("❌ Нет сохранённых данных для восстановления.")
    conn.close()
    await callback.answer()

# ==================== ТИКЕТЫ ====================
@dp.callback_query(F.data == "ticket")
async def create_ticket(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TicketState.waiting_message)
    await callback.message.edit_text("🆘 Опишите вашу проблему. Мы ответим в ближайшее время.")
    await callback.answer()

@dp.message(TicketState.waiting_message)
async def process_ticket(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO tickets (user_id, username, message) VALUES (?, ?, ?)",
              (user.id, user.username, text))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Ваше обращение отправлено. Ожидайте ответа.")
    await bot.send_message(ADMIN_ID, f"🆘 Новый тикет от @{user.username or user.id}:\n{text[:200]}")

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    await show_main_menu(callback.message)
    await callback.answer()

# ==================== АДМИН-ПАНЕЛЬ ====================
@dp.message(Command("panel"))
async def panel_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="panel_users")],
        [InlineKeyboardButton(text="🔑 Пароли", callback_data="panel_passwords")],
        [InlineKeyboardButton(text="🎫 Тикеты", callback_data="panel_tickets")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="panel_stats")],
        [InlineKeyboardButton(text="➕ Создать пароль", callback_data="panel_create_pass")],
        [InlineKeyboardButton(text="➖ Удалить пароль", callback_data="panel_delete_pass")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="panel_back")]
    ])
    await message.answer("⚙️ Панель управления:", reply_markup=keyboard)

@dp.callback_query(F.data == "panel_back")
async def panel_back(callback: types.CallbackQuery):
    await show_main_menu(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("panel_"))
async def panel_actions(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = callback.data
    conn = get_conn()
    c = conn.cursor()
    if data == "panel_stats":
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_active=1")
        active_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM passwords")
        total_passes = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tickets WHERE status='open'")
        open_tickets = c.fetchone()[0]
        text = f"📊 Статистика:\n👥 Всего пользователей: {total_users}\n✅ Активных: {active_users}\n🔑 Паролей: {total_passes}\n🎫 Открытых тикетов: {open_tickets}"
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="pa
