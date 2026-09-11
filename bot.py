import os
import json
import time
import hashlib
import secrets
import threading
import urllib.request

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не указан")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY не указан")

DATA_DIR = "data"
CLONES_FILE = os.path.join(DATA_DIR, "clones.json")
KEYS_FILE = os.path.join(DATA_DIR, "keys.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending.json")

# Кто может создавать системные ключи
KEY_ADMINS = {
    "hedgehoblock",
    "FSBIJ"
}

CLONES = {}
KEYS = {}
PENDING = {}

LOCK = threading.RLock()


# -------------------------
# JSON
# -------------------------

def save_json(path, data):
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp, path)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def init_files():
    os.makedirs(DATA_DIR, exist_ok=True)

    for path, default in (
        (CLONES_FILE, {}),
        (KEYS_FILE, {}),
        (PENDING_FILE, {})
    ):
        if not os.path.exists(path):
            save_json(path, default)


def load_state():
    global CLONES
    global KEYS
    global PENDING

    CLONES = load_json(CLONES_FILE, {})
    KEYS = load_json(KEYS_FILE, {})
    PENDING = load_json(PENDING_FILE, {})


# -------------------------
# HTTP
# -------------------------

def http_json(url, payload=None, headers=None, timeout=60):
    data = None

    request_headers = {
        "Content-Type": "application/json"
    }

    if headers:
        request_headers.update(headers)

    if payload is not None:
        data = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def telegram(token, method, payload=None, timeout=60):
    url = (
        f"https://api.telegram.org/"
        f"bot{token}/{method}"
    )

    return http_json(
        url,
        payload or {},
        timeout=timeout
    )


def send_message(token, chat_id, text):
    if len(text) > 4090:
        text = text[:4087] + "..."

    try:
        telegram(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text
            },
            timeout=20
        )
    except Exception:
        pass


# -------------------------
# Telegram bot validation
# -------------------------

def get_me(token):
    result = telegram(
        token,
        "getMe",
        timeout=20
    )

    if not result.get("ok"):
        raise ValueError("Неверный токен")

    return result["result"]


# -------------------------
# Keys
# -------------------------

def hash_key(key):
    return hashlib.sha256(
        key.encode("utf-8")
    ).hexdigest()


def generate_key():
    return (
        "LD-" +
        secrets.token_urlsafe(24)
        .replace("-", "")
        .replace("_", "")[:28]
    )


def create_key(admin_username, target_user_id):
    admin_username = (
        admin_username or ""
    ).lstrip("@")

    if admin_username not in KEY_ADMINS:
        return None

    target_user_id = str(target_user_id)

    key = generate_key()

    KEYS[hash_key(key)] = {
        "target_user_id": target_user_id,
        "created_by": admin_username,
        "created_at": int(time.time()),
        "used": False
    }

    save_json(
        KEYS_FILE,
        KEYS
    )

    return key


def use_key(key, user_id):
    key_hash = hash_key(
        key.strip()
    )

    item = KEYS.get(key_hash)

    if not item:
        return False, "Ключ не найден."

    if item.get("used"):
        return False, "Ключ уже использован."

    target_user_id = str(
        item.get("target_user_id")
    )

    if target_user_id != str(user_id):
        return False, "Этот ключ предназначен для другого пользователя."

    item["used"] = True
    item["used_at"] = int(time.time())

    save_json(
        KEYS_FILE,
        KEYS
    )

    return True, "OK"


# -------------------------
# AI
# -------------------------

def ask_ai(text):
    payload = {
        "model": "groq/compound-mini",

        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты универсальный AI-помощник Telegram-бота. "
                    "Отвечай понятно и точно. "
                    "Если пользователь пишет на русском, отвечай на русском. "
                    "Для актуальной информации используй веб-поиск."
                )
            },
            {
                "role": "user",
                "content": text
            }
        ],

        "compound_custom": {
            "tools": {
                "enabled_tools": [
                    "web_search",
                    "visit_website"
                ]
            }
        }
    }

    result = http_json(
        "https://api.groq.com/openai/v1/chat/completions",
        payload,
        headers={
            "Authorization": (
                f"Bearer {GROQ_API_KEY}"
            ),
            "Groq-Model-Version": "latest"
        },
        timeout=100
    )

    try:
        return (
            result["choices"][0]
            ["message"]
            .get("content")
            or "Не удалось получить ответ."
        )

    except Exception:
        return "Ошибка при получении ответа AI."


# -------------------------
# Master bot
# -------------------------

def master_help():
    return (
        "Команды:\n\n"
        "/start — запуск\n"
        "/help — помощь\n"
        "/clone — подключить бота\n\n"
        "Для администраторов:\n"
        "/create key USER_ID\n\n"
        "Пример:\n"
        "/create key 123456789"
    )


def handle_master(update):
    message = update.get("message") or {}

    chat_id = message.get(
        "chat",
        {}
    ).get("id")

    user = message.get(
        "from",
        {}
    )

    user_id = user.get("id")

    username = (
        user.get("username") or ""
    ).lstrip("@")

    text = (
        message.get("text") or ""
    ).strip()

    if not chat_id:
        return

    # START
    if text == "/start":
        send_message(
            BOT_TOKEN,
            chat_id,
            "Привет!\n\n" + master_help()
        )
        return

    # HELP
    if text == "/help":
        send_message(
            BOT_TOKEN,
            chat_id,
            master_help()
        )
        return

    # CREATE KEY
    if text.startswith("/create key"):
        parts = text.split()

        if len(parts) != 3:
            send_message(
                BOT_TOKEN,
                chat_id,
                "Использование:\n"
                "/create key USER_ID\n\n"
                "Например:\n"
                "/create key 123456789"
            )
            return

        target_user_id = parts[2]

        if not target_user_id.isdigit():
            send_message(
                BOT_TOKEN,
                chat_id,
                "USER_ID должен быть числовым Telegram ID."
            )
            return

        key = create_key(
            username,
            target_user_id
        )

        if not key:
            send_message(
                BOT_TOKEN,
                chat_id,
                "Нет доступа.\n\n"
                "Создавать ключи могут только "
                "@hedgehoblock и @FSBIJ."
            )
            return

        send_message(
            BOT_TOKEN,
            chat_id,
            "Ключ создан.\n\n"
            f"Для пользователя: {target_user_id}\n\n"
            f"Ключ:\n{key}\n\n"
            "Ключ одноразовый."
        )

        return

    # CLONE
    if text == "/clone":
        PENDING[str(user_id)] = {
            "step": "waiting_token",
            "created_at": int(time.time())
        }

        save_json(
            PENDING_FILE,
            PENDING
        )

        send_message(
            BOT_TOKEN,
            chat_id,
            "Отправь токен Telegram-бота, "
            "который нужно подключить."
        )

        return

    state = PENDING.get(
        str(user_id)
    )

    if not state:
        return

    # WAITING TOKEN
    if state.get("step") == "waiting_token":

        token = text

        try:
            bot_info = get_me(token)

        except Exception:
            send_message(
                BOT_TOKEN,
                chat_id,
                "Токен недействителен.\n"
                "Отправь правильный токен."
            )
            return

        PENDING[str(user_id)] = {
            "step": "waiting_key",
            "token": token,
            "bot_id": str(bot_info["id"]),
            "bot_username": bot_info.get(
                "username",
                ""
            )
        }

        save_json(
            PENDING_FILE,
            PENDING
        )

        send_message(
            BOT_TOKEN,
            chat_id,
            "Токен принят.\n\n"
            f"Бот: @{bot_info.get('username', 'unknown')}\n\n"
            "Для продолжения нужен системный ключ.\n\n"
            "Где получить ключ:\n"
            "• @hedgehoblock\n"
            "• @FSBIJ\n\n"
            "Администратор создаёт его командой:\n"
            "/create key USER_ID\n\n"
            "После получения ключа отправь его сюда."
        )

        return

    # WAITING KEY
    if state.get("step") == "waiting_key":

        success, reason = use_key(
            text,
            user_id
        )

        if not success:
            send_message(
                BOT_TOKEN,
                chat_id,
                "Ключ не принят.\n\n"
                f"{reason}\n\n"
                "Если у тебя нет ключа, "
                "получи его у:\n"
                "• @hedgehoblock\n"
                "• @FSBIJ"
            )
            return

        token = state["token"]
        bot_id = state["bot_id"]
        bot_username = state.get(
            "bot_username",
            ""
        )

        CLONES[bot_id] = {
            "token": token,
            "username": bot_username,
            "owner_id": str(user_id),
            "created_at": int(time.time())
        }

        PENDING.pop(
            str(user_id),
            None
        )

        save_json(
            CLONES_FILE,
            CLONES
        )

        save_json(
            PENDING_FILE,
            PENDING
        )

        send_message(
            BOT_TOKEN,
            chat_id,
            "Готово!\n\n"
            f"@{bot_username} подключён "
            "к общей AI-системе.\n\n"
            "Теперь пользователи могут "
            "писать ему сообщения."
        )

        threading.Thread(
            target=clone_worker,
            args=(bot_id,),
            daemon=True
        ).start()


# -------------------------
# Clone bots
# -------------------------

def handle_clone_update(token, update):
    message = update.get("message") or {}

    chat_id = message.get(
        "chat",
        {}
    ).get("id")

    text = (
        message.get("text") or ""
    ).strip()

    if not chat_id:
        return

    if text in (
        "/start",
        "/help"
    ):
        send_message(
            token,
            chat_id,
            "Привет!\n\n"
            "Я AI-бот.\n"
            "Просто отправь свой вопрос."
        )
        return

    if not text:
        return

    try:
        answer = ask_ai(text)

    except Exception:
        answer = (
            "Произошла ошибка при обращении "
            "к AI. Попробуй ещё раз."
        )

    send_message(
        token,
        chat_id,
        answer
    )


def clone_worker(bot_id):
    clone = CLONES.get(bot_id)

    if not clone:
        return

    token = clone["token"]
    offset = 0

    try:
        telegram(
            token,
            "deleteWebhook",
            {
                "drop_pending_updates": False
            },
            timeout=20
        )
    except Exception:
        pass

    while True:

        try:
            result = telegram(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": [
                        "message"
                    ]
                },
                timeout=35
            )

            if not result.get("ok"):
                time.sleep(5)
                continue

            for update in result.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"] + 1
                )

                handle_clone_update(
                    token,
                    update
                )

        except Exception:
            time.sleep(5)


# -------------------------
# Main
# -------------------------

def start_existing_clones():
    for bot_id in list(CLONES.keys()):

        threading.Thread(
            target=clone_worker,
            args=(bot_id,),
            daemon=True
        ).start()


def main():

    init_files()
    load_state()

    start_existing_clones()

    try:
        telegram(
            BOT_TOKEN,
            "deleteWebhook",
            {
                "drop_pending_updates": False
            },
            timeout=20
        )
    except Exception:
        pass

    offset = 0

    while True:

        try:

            result = telegram(
                BOT_TOKEN,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": [
                        "message"
                    ]
                },
                timeout=35
            )

            if not result.get("ok"):
                time.sleep(5)
                continue

            for update in result.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"] + 1
                )

                handle_master(update)

        except KeyboardInterrupt:
            break

        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    main()