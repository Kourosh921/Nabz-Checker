
import os
import re
import json
import time
import logging
import threading
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------- CONFIG ----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME", "dollariran5"
).strip().lstrip("@").lower()

# اصلاح خودکار آدرس TGJU
TGJU_API_BASE = os.getenv(
    "TGJU_API_BASE", "http://localhost:8080"
).strip().rstrip("/")

if not TGJU_API_BASE.startswith(("http://", "https://")):
    TGJU_API_BASE = "http://" + TGJU_API_BASE

DATA_FILE = Path(
    os.getenv("DATA_FILE", str(BASE_DIR / "state.json"))
)

POLL_TIMEOUT = 30
TGJU_REFRESH_INTERVAL = 120

# ---------------- ASSETS ----------------

ASSETS = {
    "دلار": {
        "key": "price_dollar_rl",
        "aliases": ["دلار آزاد", "قیمت دلار", "دلار"]
    },
    "یورو": {
        "key": "price_eur",
        "aliases": ["یورو"]
    },
    "درهم": {
        "key": "price_aed",
        "aliases": ["درهم امارات", "درهم"]
    },
    "طلای ۱۸ عیار": {
        "key": "geram18",
        "aliases": [
            "طلای ۱۸ عیار",
            "طلای 18 عیار",
            "گرم طلای ۱۸"
        ]
    },
    "سکه امامی": {
        "key": "sekeb",
        "aliases": ["سکه امامی", "تمام سکه"]
    }
}

# ---------------- APP ----------------

app = FastAPI(title="Nabz Checker")

if not STATIC_DIR.exists():
    raise RuntimeError("پوشه static پیدا نشد")

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static"
)

session = requests.Session()
lock = threading.RLock()

state = {
    "channel": {},
    "tgju": {},
    "updated_at": None,
    "last_message": None,
    "status": "در انتظار اتصال",
    "errors": []
}

# ---------------- HELPERS ----------------

def normalize_digits(value):
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return str(value).translate(table)


def extract_number(value):
    value = normalize_digits(value)
    value = value.replace(",", "").replace("٬", "")
    value = value.replace(" ", "")

    match = re.search(r"\d+(?:\.\d+)?", value)

    if not match:
        return None

    try:
        return float(match.group())
    except ValueError:
        return None


def parse_channel_message(message):
    found = {}
    message = normalize_digits(message)

    for name, asset in ASSETS.items():
        for alias in asset["aliases"]:
            pattern = (
                re.escape(alias)
                + r"\s*[:：\-]?\s*"
                + r"([\d,٬]+(?:\.\d+)?)"
            )

            match = re.search(
                pattern,
                message,
                flags=re.IGNORECASE
            )

            if match:
                number = extract_number(match.group(1))

                if number is not None:
                    found[name] = number
                    break

    return found


def find_price_value(data):
    if isinstance(data, (int, float, str)):
        return extract_number(data)

    if isinstance(data, list):
        for item in data:
            result = find_price_value(item)
            if result is not None:
                return result

    if isinstance(data, dict):
        preferred_keys = [
            "value",
            "price",
            "current",
            "last",
            "p",
            "close",
            "last_price"
        ]

        for key in preferred_keys:
            if key in data:
                result = find_price_value(data[key])
                if result is not None:
                    return result

        for value in data.values():
            if isinstance(value, (dict, list)):
                result = find_price_value(value)
                if result is not None:
                    return result

    return None


# ---------------- STORAGE ----------------

def save_state():
    try:
        DATA_FILE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with lock:
            snapshot = dict(state)

        DATA_FILE.write_text(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception:
        logging.exception("Saving state failed")


def load_state():
    if not DATA_FILE.exists():
        return

    try:
        saved = json.loads(
            DATA_FILE.read_text(encoding="utf-8")
        )

        with lock:
            state.update(saved)

    except Exception:
        logging.exception("Loading state failed")


# ---------------- TGJU ----------------

def get_tgju_price(key):
    url = f"{TGJU_API_BASE}/v1/items/{key}"

    response = session.get(
        url,
        timeout=20
    )
    response.raise_for_status()

    data = response.json()
    value = find_price_value(data)

    if value is None:
        raise ValueError(
            f"قیمت معتبر برای {key} پیدا نشد"
        )

    # تبدیل ریال به تومان
    return value / 10


def refresh_tgju():
    prices = {}
    errors = []

    for name, asset in ASSETS.items():
        try:
            prices[name] = get_tgju_price(asset["key"])

        except Exception as exc:
            logging.warning("TGJU %s: %s", name, exc)
            errors.append(f"{name}: {exc}")

    with lock:
        if prices:
            state["tgju"] = {
                **state["tgju"],
                **prices
            }

            state["updated_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.gmtime()
            )

            state["status"] = "دریافت قیمت از TGJU انجام شد"

        else:
            state["status"] = "دریافت قیمت از TGJU ناموفق بود"

        state["errors"] = errors

    save_state()


# ---------------- TELEGRAM ----------------

def telegram_request(method, params=None):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده است")

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    response = session.get(
        url,
        params=params or {},
        timeout=40
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(str(data))

    return data.get("result", [])


def telegram_loop():
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is missing")

        with lock:
            state["status"] = "توکن ربات تنظیم نشده"

        return

    offset = None

    while True:
        try:
            params = {
                "timeout": POLL_TIMEOUT,
                "allowed_updates": json.dumps(["channel_post"])
            }

            if offset is not None:
                params["offset"] = offset

            updates = telegram_request(
                "getUpdates",
                params
            )

            for update in updates:
                offset = update["update_id"] + 1

                post = update.get("channel_post")

                if not post:
                    continue

                chat = post.get("chat", {})

                username = (
                    chat.get("username") or ""
                ).lower()

                if username != CHANNEL_USERNAME:
                    continue

                message = (
                    post.get("text")
                    or post.get("caption")
                    or ""
                )

                prices = parse_channel_message(message)

                with lock:
                    state["channel"] = prices
                    state["last_message"] = message
                    state["status"] = "پیام جدید کانال دریافت شد"

                logging.info(
                    "Telegram channel message received"
                )

                refresh_tgju()
                save_state()

        except Exception as exc:
            logging.exception("Telegram polling failed")

            with lock:
                state["errors"] = [str(exc)]
                state["status"] = "خطا در دریافت پیام تلگرام"

            time.sleep(5)


# ---------------- BACKGROUND ----------------

def tgju_background_loop():
    while True:
        try:
            refresh_tgju()

        except Exception:
            logging.exception("Background TGJU refresh failed")

        time.sleep(TGJU_REFRESH_INTERVAL)


@app.on_event("startup")
def startup():
    load_state()

    logging.info("Nabz Checker started")
    logging.info("TGJU API: %s", TGJU_API_BASE)

    threading.Thread(
        target=telegram_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=tgju_background_loop,
        daemon=True
    ).start()


# ---------------- WEBSITE ----------------

@app.get("/")
def home():
    return FileResponse(
        str(STATIC_DIR / "index.html")
    )


@app.get("/api/state")
def api_state():
    with lock:
        result = dict(state)

    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store"}
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "Nabz Checker"
    }
