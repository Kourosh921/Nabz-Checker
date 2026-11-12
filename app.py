

import os
import re
import json
import time
import threading
import logging
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Nabz Checker")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "dollariran5").strip().lstrip("@")
TGJU_API_BASE = os.getenv("TGJU_API_BASE", "http://localhost:8080").rstrip("/")
DATA_FILE = Path(os.getenv("DATA_FILE", "state.json"))

ASSETS = {
    "دلار": {
        "key": "price_dollar_rl",
        "aliases": ["دلار آزاد", "دلار"],
    },
    "یورو": {
        "key": "price_eur",
        "aliases": ["یورو", "EUR"],
    },
    "درهم": {
        "key": "price_aed",
        "aliases": ["درهم امارات", "درهم"],
    },
    "طلای ۱۸": {
        "key": "geram18",
        "aliases": ["طلای ۱۸ عیار", "طلای 18 عیار", "گرم طلای ۱۸"],
    },
    "سکه امامی": {
        "key": "sekeb",
        "aliases": ["سکه امامی", "تمام سکه"],
    },
}

state = {
    "channel": {},
    "tgju": {},
    "updated_at": None,
    "last_message": None,
    "status": "در انتظار اتصال",
    "errors": [],
}

lock = threading.Lock()
session = requests.Session()


def normalize_digits(value):
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return str(value).translate(table)


def extract_number(value):
    value = normalize_digits(value)
    value = value.replace(",", "").replace("٬", "").replace(" ", "")
    match = re.search(r"\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def parse_channel_message(message):
    found = {}

    for name, asset in ASSETS.items():
        for alias in asset["aliases"]:
            pattern = re.escape(alias)
            match = re.search(
                pattern + r"\s*[:：\-]?\s*([\d۰-۹٠-٩٬,\.]+)",
                message,
                re.IGNORECASE
            )
            if match:
                number = extract_number(match.group(1))
                if number is not None:
                    found[name] = number
                    break

    return found


def get_tgju_price(key):
    url = f"{TGJU_API_BASE}/v1/items/{key}"
    response = session.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()
    price = data.get("price", {})

    if isinstance(price, dict):
        value = price.get("value")
    else:
        value = price

    if value is None:
        raise ValueError(f"قیمت {key} در پاسخ TGJU پیدا نشد")

    # قیمت‌های TGJU بر حسب ریال هستند؛ تبدیل به تومان
    return float(value) / 10


def refresh_tgju():
    prices = {}
    errors = []

    for name, asset in ASSETS.items():
        try:
            prices[name] = get_tgju_price(asset["key"])
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    with lock:
        state["tgju"] = prices
        state["errors"] = errors
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if prices:
            state["status"] = "دریافت قیمت انجام شد"
        else:
            state["status"] = "اتصال به TGJU ناموفق بود"


def save_state():
    try:
        with lock:
            snapshot = dict(state)
        DATA_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        logging.exception("Could not save state")


def load_state():
    if DATA_FILE.exists():
        try:
            saved = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            with lock:
                state.update(saved)
        except Exception:
            logging.exception("Could not load state")


def telegram_loop():
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is missing")
        with lock:
            state["status"] = "توکن ربات تنظیم نشده"
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    offset = None

    while True:
        try:
            params = {
                "timeout": 30,
                "allowed_updates": json.dumps(["channel_post"]),
            }
            if offset is not None:
                params["offset"] = offset

            response = session.get(url, params=params, timeout=40)
            response.raise_for_status()
            result = response.json()

            if not result.get("ok"):
                raise RuntimeError(str(result))

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                post = update.get("channel_post")
                if not post:
                    continue

                chat = post.get("chat", {})
                username = chat.get("username", "").lower()

                if username != CHANNEL_USERNAME.lower():
                    continue

                message = post.get("text") or post.get("caption") or ""
                prices = parse_channel_message(message)

                with lock:
                    state["channel"] = prices
                    state["last_message"] = message
                    state["status"] = "پیام جدید کانال دریافت شد"

                refresh_tgju()
                save_state()

        except Exception as exc:
            logging.exception("Telegram polling error")
            with lock:
                state["errors"] = [str(exc)]
                state["status"] = "خطا در دریافت پیام تلگرام"
            time.sleep(5)


@app.on_event("startup")
def startup():
    load_state()
    threading.Thread(target=telegram_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!doctype html>
    <html lang="fa" dir="rtl">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Nabz Checker</title>
      <style>
        body {
          margin: 0; padding: 22px;
          background: #10131b; color: #f4f5f8;
          font-family: Tahoma, sans-serif;
        }
        main { max-width: 850px; margin: auto; }
        header, .card {
          background: #1a2030; border: 1px solid #30394c;
          border-radius: 16px; padding: 18px; margin-bottom: 14px;
        }
        h1 { margin: 0 0 8px; }
        .muted { color: #aab3c5; font-size: 13px; }
        .item {
          display: grid; grid-template-columns: 1fr 1fr 1fr;
          gap: 10px; padding: 15px 0;
          border-bottom: 1px solid #30394c;
        }
        .item:last-child { border: 0; }
        .value { font-weight: bold; margin-top: 7px; }
        .positive { color: #ff7979; }
        .negative { color: #58d6a0; }
        @media(max-width: 560px) {
          .item { grid-template-columns: 1fr 1fr; }
          .difference { grid-column: 1 / -1; }
        }
      </style>
    </head>
    <body>
    <main>
      <header>
        <h1>NABZ CHECKER</h1>
        <div class="muted">مقایسه قیمت کانال نبض دلار با TGJU</div>
        <p id="status">در حال اتصال...</p>
        <div class="muted" id="updated"></div>
      </header>
      <section class="card">
        <div id="prices">در انتظار دریافت اطلاعات...</div>
      </section>
      <section class="card">
        <div class="muted">آخرین پیام دریافت‌شده از کانال</div>
        <p id="message">هنوز پیامی دریافت نشده است.</p>
      </section>
    </main>
    <script>
      const fmt = n => Number(n).toLocaleString("en-US", {
        maximumFractionDigits: 2
      });

      async function update() {
        try {
          const response = await fetch("/api/state");
          const data = await response.json();

          document.getElementById("status").textContent =
            "وضعیت: " + data.status;
          document.getElementById("updated").textContent =
            "آخرین به‌روزرسانی: " + (data.updated_at || "نامشخص");
          document.getElementById("message").textContent =
            data.last_message || "هنوز پیامی دریافت نشده است.";

          const names = Object.keys(data.tgju || {});
          let html = "";

          for (const name of names) {
            const market = data.tgju[name];
            const channel = data.channel[name];
            let diff = "قیمت کانال موجود نیست";
            let cls = "";

            if (channel !== undefined && market !== undefined) {
              const amount = channel - market;
              const percent = market ? amount / market * 100 : 0;
              diff = fmt(amount) + " تومان (" + percent.toFixed(2) + "%)";
              cls = amount > 0 ? "positive" : amount < 0 ? "negative" : "";
            }

            html += `
              <div class="item">
                <div>${name}<div class="value">${channel === undefined ? "—" : fmt(channel)}</div><small class="muted">کانال</small></div>
                <div><div class="value">${fmt(market)}</div><small class="muted">TGJU</small></div>
                <div class="difference ${cls}"><div class="value">${diff}</div><small class="muted">اختلاف</small></div>
              </div>`;
          }

          document.getElementById("prices").innerHTML =
            html || "هنوز قیمت معتبری دریافت نشده است.";
        } catch (error) {
          document.getElementById("status").textContent =
            "خطا در دریافت اطلاعات";
        }
      }

      update();
      setInterval(update, 30000);
    </script>
    </body>
    </html>
    """


@app.get("/api/state")
def api_state():
    with lock:
        result = dict(state)

    return result


@app.get("/health")
def health():
    return {"ok": True, "service": "Nabz Checker"}
