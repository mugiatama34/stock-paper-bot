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


def notify_buy(strategy: str, symbol: str, qty: int, price: float, stop_price: float,
               reasoning: str) -> None:
    """Alım bildirimi. `reasoning` boş/None olsa da mesaj gönderilir, o satır atlanır."""
    lines = [
        f"🟢 *ALIM* — {strategy}",
        f"{symbol}  x{qty} @ ${price:.2f}",
    ]
    if stop_price is not None:
        try:
            lines.append(f"Stop: ${float(stop_price):.2f}")
        except (TypeError, ValueError):
            pass
    if reasoning:
        lines.append(f"Gerekçe: {reasoning}")
    send_message("\n".join(lines))


def notify_sell(strategy: str, symbol: str, qty: int, price: float, reasoning: str,
                 pnl: float = None, pnl_pct: float = None, held_days: int = None) -> None:
    """Satım bildirimi. Hesaplanamayan alanlar (K/Z, süre, gerekçe) sessizce atlanır;
    bildirim hiçbir koşulda başarısız olmaz."""
    lines = [
        f"🔴 *SATIM* — {strategy}",
        f"{symbol}  x{qty} @ ${price:.2f}",
    ]
    if pnl is not None:
        sign = "+" if pnl >= 0 else "-"
        pct_part = ""
        if pnl_pct is not None:
            pct_sign = "+" if pnl_pct >= 0 else "-"
            pct_part = f" ({pct_sign}{abs(pnl_pct):.1f}%)"
        lines.append(f"K/Z: {sign}${abs(pnl):.2f}{pct_part}")
    if held_days is not None:
        lines.append(f"Süre: {held_days} gün" if held_days >= 1 else "Süre: aynı gün")
    if reasoning:
        lines.append(f"Gerekçe: {reasoning}")
    send_message("\n".join(lines))
