import os
import sys

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TIMEOUT = 10  # saniye — ağ sorununda run'ın süresiz asılı kalmasını önler


def send_message(text: str) -> None:
    """Telegram'a mesaj gönderir. Ağ/HTTP hatalarını yutar; bildirim başarısızlığı
    hiçbir koşulda çağıran akışı çökertmemeli."""
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] token/chat_id missing, skipping message:\n", text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[telegram] mesaj gönderilemedi, akış devam ediyor: {e}", file=sys.stderr)


def send_photo(photo_path: str, caption: str = "") -> None:
    """Telegram'a fotoğraf gönderir. Ağ/HTTP hatalarını yutar; bildirim başarısızlığı
    hiçbir koşulda çağıran akışı çökertmemeli."""
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[telegram] token/chat_id missing, skipping photo: {photo_path}")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"photo": f},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
    except Exception as e:
        print(f"[telegram] fotoğraf gönderilemedi, akış devam ediyor: {e}", file=sys.stderr)


def notify_trade(strategy: str, action: str, symbol: str, qty: int, price: float, reasoning: str) -> None:
    emoji = "🟢" if action == "BUY" else "🔴"
    text = (
        f"{emoji} *{action}* — {strategy}\n"
        f"{symbol}  x{qty} @ ${price:.2f}\n"
        f"Gerekçe: {reasoning}"
    )
    send_message(text)
