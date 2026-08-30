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

## Bulgular

### 2026-08 — Backtest/canlı evren tutarsızlığı

`backtest.py` evreni dolar-hacme göre en likit 80 isimle sınırlıyordu; `trade_bot.py`
ise canlıda hiçbir top-N filtresi uygulamadan sektör haritasındaki tüm sembolleri
tarıyordu. Yani backtest, canlı sistemi temsil etmiyordu. Düzeltildi: backtest artık
canlı evren mantığını kullanıyor.

Bu düzeltmeden önce yapılmış tüm strateji karşılaştırmaları geçersizdir. Daraltılmış
evrende alınan sonuçlar, düzeltme sonrası bazı durumlarda tersine döndü.

**Ölçülen sonuçlar (düzeltme sonrası, cost_bps=10, iki ayrık pencere)**
- `ai_momentum` her iki pencerede de SPY al-tut'u geçti: 2021-06→2023-06 döneminde
  +%18.1 (SPY +%3.6), 2023-06→2025-06 döneminde +%44.1 (SPY +%43.5), Sharpe 1.47
  (SPY 1.19), Max DD -%11.2 (SPY -%18.8).
- Diğer beş strateji her iki pencerede de SPY'ın gerisinde kaldı.
- Düzeltme öncesi umut verici görünen `trend_donchian` üstünlüğü, tam evrende
  ortadan kalktı.
- Test dönemi teknoloji ağırlıklı bir yükseliş dönemidir; `ai_momentum`'un
  Nasdaq-100 evreni bu dönemde yapısal olarak avantajlıydı. İki pencere
  istatistiksel genelleme için yetersizdir.

**Çürütülen hipotezler**

Aşağıdaki alternatifler test edildi ve risk başına getiride anlamlı iyileşme
sağlamadı: Bollinger alt bant girişi, rolling z-score girişi, Donchian kanal
kırılımı girişi, geniş trailing stop (4.0×ATR). Not: ilk üçü daraltılmış evrende
test edildi, sonuçları bu nedenle kesin değildir.

**Açık bulgu — mean_reversion stop uyumsuzluğu**

İşlem verisi analizi, `mean_reversion` stratejisinde trailing stop ile kapanan
pozisyonların %97.7-100'ünün zararla kapandığını gösterdi; aynı stratejide sinyal
bazlı çıkışlarda zarar oranı %7.6-14.6. Stop, bu stratejide kâr koruma değil
yalnızca stop-loss işlevi görüyor. Stop mesafesini genişletmek (4.0×ATR) sorunu
çözmedi. Mekanizma uyumsuzluğu olarak açık kalmıştır.

**Ölçülemeyenler**

Rejim filtresinin net etkisi, atıl nakdin getiriye etkisi ve tarama sıklığının
fırsat maliyeti ölçülemedi. Ortak sebep: reddedilen alım sinyalleri ve nakit
zaman serisi hiçbir yerde loglanmıyor. Bu konulara dönülecekse önce bu kayıtların
eklenmesi gerekir.

## Öğrenme günlüğü

### 2026-08-30

**Yapılanlar**
- stock-paper-bot projesi Claude Code'a bağlandı; projeye özel CLAUDE.md yazıldı
  (dokunulmaz dosyalar, onay gerektiren değişiklikler, backtest zorunluluğu).
- kod-denetci ve strateji-arastirmaci sub-agent'ları kuruldu.
- Ledger yazma güvenliği düzeltildi (atomik yazma, hata izolasyonu), backtest
  ölçüm altyapısı eklendi (işlem bazlı çıktı, tarih aralığı parametresi).
- Backtest ile canlı bot arasındaki evren tutarsızlığı bulundu ve düzeltildi.
- Üç yeni strateji varyantı test edildi; dört hipotez çürütüldü.
- Zamanlama, Telegram bildirimleri, rapor arşivi ve dashboard yeniden düzenlendi.

**Öğrendiklerim**
- Sub-agent'ın değeri kendi bağımsız bağlamında çalışmasıdır; denetim ve araştırma
  gibi tarafsızlık gerektiren işler için ideal.
- tools alanı teknik kilittir, metin talimatı yalnızca ricadır. Araç verilmemişse
  ajan o işi yapamaz; metinde yasaklanmışsa yapmayabilir. Güvenlik kritik sınırlar
  metne bırakılmamalı.
- Bir hipotezi test etmeden önce çürütme koşulu yazılı olarak belirlenmelidir.
  İnsan beyni çıkan sonuca göre değerlendirme yapmayı çok seviyor.
- Ölçüm altyapısı analiz kadar önemlidir. Maliyet varsayımı, evren tanımı veya
  çıkış mantığındaki en ufak değişiklik sonucu tersine çevirebiliyor.
- Ölçüm aleti kalibre değilken yapılan tüm karşılaştırmalar geçersizdir. Backtest
  evreni ile canlı evren farklıydı; bu bulunana kadar alınan sonuçların bir kısmı
  yanlıştı.
- Bir ajanın "ölçülemiyor" demesi iyi bir işarettir; tarafsızdır ve yönlendirici
  değildir. Yerine bir şey uydursaydı yanıltıcı olurdu.
- Dört hipotezin de benzer sonuç vermesinin sebebi tesadüf değil: hepsi giriş
  kuralı veya stop mesafesi düzeyindeydi, mimarinin ortak parçaları (çıkış mantığı,
  pozisyon boyutlandırma, rejim filtresi) hiç değişmedi.
- Hata izolasyonu sistemi çökmekten korur ama sessiz başarısızlık üretebilir; bu
  yüzden düzenli denetim şarttır. Dashboard yenileme fonksiyonu bir PR sonrası
  çalışmayı bıraktı ve hata yutulduğu için fark edilmedi.

**Açık sorular**
- Botun mimarisinde (çıkış mekanizması, pozisyon boyutlandırma, rejim filtresi)
  yapısal bir iyileştirme alanı var mı? Reddedilen sinyaller ve nakit serisi
  loglanmadığı için bu ölçülemiyor.
