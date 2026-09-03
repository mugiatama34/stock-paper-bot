# CLAUDE.md

Proje: ABD borsasında GitHub Actions üzerinden otonom çalışan, üç paralel paper-trading stratejisi (ai_momentum, mean_reversion, balanced). Her strateji kendi sanal sermayesi ve ledger'ı ile çalışır. Veri kaynağı yfinance. Bildirimler Telegram üzerinden.

## Dokunulamaz dosyalar — okunabilir, asla yazılamaz

- `data/portfolio_*.json` - botun canlı hafızası, tüm işlem geçmişi burada
- `data/universe_cache.json`
- `docs/index.html`, `docs/backtest.html`
- `reports/` altındaki tüm dosyalar

Bunlar Actions tarafından üretilir. Tutarsız veya hatalı görünseler bile düzeltilmez; durum rapor edilir, karar kullanıcıya bırakılır.

## Onay gerektiren değişiklikler — plan yazılır ve onay beklenir, onaysız uygulanmaz

- `strategies/` altındaki herhangi bir dosya
- Risk parametreleri (pozisyon başına risk, nakit oranı, sektör limiti, ATR stop, rejim filtresi)
- `.github/workflows/` altındaki herhangi bir dosya

## Strateji ve risk değişikliği prosedürü

- Strateji ve risk değişikliklerinde: değişiklik backtest.py ile test edilir, öncesi ve sonrası sonuçları PR açıklamasında karşılaştırmalı olarak paylaşılır. Backtest çalıştırılmadan strateji değişikliği PR'a gönderilmez.
- Claude Code oturumlarında Yahoo Finance'e ağ erişimi yoktur; gerçek veriyle backtest çalıştırmak için backtest.yml workflow'u ilgili branch üzerinde workflow_dispatch ile manuel tetiklenir. Bu, oturum içinden yapılabilir.

## Sırlar

- Sırlar: API anahtarı, token veya şifre koda yazılmaz. Ortam değişkeni (os.environ) ve GitHub Actions secrets üzerinden okunur.

## Kırılgan altyapı

- Kırılgan altyapı — dokunmadan önce sor: Rapor ve /trade workflow'ları commit'lerini `[skip ci]` ile atar ve `pages_deploy.yml` workflow_run ile tetiklenir. Bu yapı sonsuz döngüyü engellemek içindir.

## Çalışma kuralları

- Her değişiklik ayrı branch ve ayrı PR ile gönderilir; görev tamamlandığında PR ayrıca istemeden açılır. Tek PR tek iş.
- Açıklamalar Türkçe yazılır.
- main'e doğrudan commit atılmaz.
- Doğrulama amaçlı çalıştırılan backtest'lerin ürettiği reports/ ve docs/ altındaki çıktı dosyaları PR'a commit edilmez; sonuçlar yalnızca PR açıklamasında raporlanır. Bu dosyalar yalnızca main üzerinde zamanlanmış veya manuel workflow çalıştırmaları tarafından güncellenir.
