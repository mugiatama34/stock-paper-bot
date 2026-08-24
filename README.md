# Stock Paper Bot

ABD borsasında 3 paralel stratejiyle çalışan, sanal $10.000'lık hesaplarla işlem yapan
paper-trading botu. Her strateji kendi ledger'ını tutar, her işlemde Telegram bildirimi
gönderir ve GitHub Pages üzerinde her an bakabileceğin canlı bir dashboard sunar.

## Stratejiler
| Strateji | Yaklaşım | Risk profili |
|---|---|---|
| `ai_momentum` | AI/tech hisselerinde trend + relative-strength momentum | Yüksek |
| `mean_reversion` | Uzun vadeli trendi sağlam hisselerde RSI aşırı-satım dönüşü | Orta-düşük |
| `balanced` | Trend filtresi + nötr RSI + hacim teyidi ile dengeli giriş | Orta |

Her biri $10.000 sanal sermaye ile başlar, birbirinden bağımsız çalışır — böylece
performanslarını doğrudan karşılaştırabilirsin.

## Kurulum

### 1. Telegram Bot oluştur (henüz yoksa)
- @BotFather ile yeni bot oluştur, token al
- Bota mesaj at, sonra `https://api.telegram.org/bot<TOKEN>/getUpdates` ile chat_id'ni bul

### 2. Repo Secrets ekle
GitHub reposunda **Settings → Secrets and variables → Actions**:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

(Zaten crypto-scanner reposunda bunlar varsa aynı bot'u burada da kullanabilirsin.)

### 3. Actions'a yazma izni ver
**Settings → Actions → General → Workflow permissions** → "Read and write permissions" seç
(bot her çalıştığında ledger'ı ve raporu commit'leyip push edecek).

### 4. GitHub Pages'i aç
**Settings → Pages → Source** → "GitHub Actions" seç.
İlk `pages_deploy.yml` çalışmasından sonra dashboard şu adreste yayında olacak:
`https://<kullanici-adi>.github.io/stock-paper-bot/`

### 5. İlk çalıştırmayı manuel tetikle
**Actions** sekmesi → `Trade Bot` workflow'u → "Run workflow" (main branch).
Ardından `Daily Report` workflow'unu da manuel tetikleyip dashboard'ın ve ilk
Telegram bildiriminin geldiğini doğrula.

## Otomatik çalışma takvimi
- **Trade Bot**: Hafta içi, ABD piyasa saatlerinde saatlik (UTC 14:00–21:00)
- **Daily Report**: Hafta içi, piyasa kapanışında (~UTC 21:30)
- **Weekly Report**: Her Cuma kapanışta
- **Monthly Report**: Ay sonunda
- **Yearly Report**: 31 Aralık

## Riski yönetim mantığı
- Her trade, o stratejinin **mevcut nakdinin %2'si** kadar risk edilecek şekilde
  boyutlandırılır (stop-loss mesafesine göre); tek bir pozisyon nakdin %25'ini geçemez.
- Her pozisyonun bir stop-loss'u vardır; `trade_bot.py` her çalıştığında önce stop
  seviyelerini kontrol eder.

## Yerelde test etmek istersen
```bash
pip install -r requirements.txt
cd scripts
python trade_bot.py balanced      # tek strateji test
python generate_report.py --mode daily
```

## Sonraki adımlar / genişletme fikirleri
- Yeni bir strateji eklemek için `scripts/strategies/` altına yeni bir modül + ledger JSON
- Backtest modülü (geçmiş veri üzerinde stratejileri karşılaştırma) — bu kısım için
  Claude Desktop / Claude Code ile daha hızlı iterasyon yapılabilir.
