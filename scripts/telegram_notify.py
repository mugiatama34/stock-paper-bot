import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] token/chat_id missing, skipping message:\n", text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})


def send_photo(photo_path: str, caption: str = "") -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[telegram] token/chat_id missing, skipping photo: {photo_path}")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as f:
        requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"photo": f})


def notify_trade(strategy: str, action: str, symbol: str, qty: int, price: float, reasoning: str) -> None:
    emoji = "🟢" if action == "BUY" else "🔴"
    text = (
        f"{emoji} *{action}* — {strategy}\n"
        f"{symbol}  x{qty} @ ${price:.2f}\n"
        f"Gerekçe: {reasoning}"
    )
    send_message(text)
