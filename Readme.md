# EMA-Cross Reversal Bot (Support/Resistance H1 + Flip Protection)

Bot trading otomatis untuk Bybit Futures (USDT Perpetual), timeframe H1 saja.
Hasil riset & backtest paling optimal sejauh ini (XRPUSDT 10 bulan H1):
**Total +249.45R, win rate 42.7%, avg +1.07R/trade.**

⚠️ Backtest ≠ jaminan hasil live. Selalu tes di **Testnet** dulu sebelum live.

## Cara kerja strategi

1. **Deteksi support/resistance** (basis body candle H1):
   - Support: candle turun → candle naik → candle ketiga tidak boleh close/wick lebih rendah dari level support.
   - Resistance: kebalikannya.
   - Dianggap **valid** kalau wick pembentuknya menyentuh level S/R sejenis sebelumnya yang masih "hidup" (belum pernah ditembus close candle manapun).

2. **Arah dibalik** — ini bagian penting:
   - Support valid → bias **SHORT** (bukan long/fade seperti S/R biasa).
   - Resistance valid → bias **LONG**.
   - Bias ini tetap "hidup" dan bisa dipakai berkali-kali (re-entry berulang) sampai muncul support/resistance valid yang benar-benar baru.

3. **Entry via EMA Cross** (EMA4 & EMA10, H1):
   - Bias Short + **death cross** → limit **SELL** di harga **wick (high)** candle penyebab cross. SL = wick + jarak yang sama ke arah berlawanan.
   - Bias Long + **golden cross** → limit **BUY** di **wick (low)** candle cross. SL = wick − jarak yang sama.
   - Cross searah baru sebelum limit lama fill → limit lama diganti ke wick terbaru.

4. **Flip Protection**:
   - Sedang pending atau sudah punya posisi di satu arah, lalu muncul cross **berlawanan** → limit dibatalkan / posisi ditutup market **saat itu juga**, tidak peduli profit atau rugi.
   - Bias tetap hidup, lanjut menunggu cross searah berikutnya.

5. **Trailing stop native Bybit**: aktif otomatis setelah profit mencapai rasio **1:6** dari jarak entry-SL (`TRAIL_ACT_R`), lebar trailing 1× jarak (`TRAIL_STOP`).

## Setup & Deploy (Railway)

1. Push folder ini ke repo GitHub kamu.
2. Buat project baru di [Railway](https://railway.app), connect ke repo tersebut.
3. Railway otomatis pakai `railway.toml` / `Procfile` → menjalankan `python bot_ema_flip.py`.
4. Di tab **Variables**, isi minimal:
   - `API_KEY` — API key Bybit
   - `API_SECRET` — API secret Bybit
   - `TESTNET` — `true` untuk testnet, `false` untuk live
5. Deploy. Bot langsung jalan begitu deploy selesai.
6. Buka `https://<project-kamu>.up.railway.app/view` untuk lihat log entry per koin, atau `/logs` untuk log mentah realtime, `/ohlc` untuk unduh data candle yang sedang dilihat bot (diagnostik).

## Environment Variables

Lihat `.env.example` untuk daftar lengkap. Yang **wajib**: `API_KEY`, `API_SECRET`.
Semua parameter strategi (`TRAIL_ACT_R`, `EMA_FAST`, `EMA_SLOW`, `RISK_PCT`, `LEVERAGE`, `MAX_CONCURRENT`, `ALLOW_HEDGE`) sudah punya default hasil backtest terbaik, tapi bisa dioverride tanpa perlu ubah kode.

⚠️ **Hedge Mode wajib aktif** di akun Bybit kamu (bot otomatis mencoba men-switch saat start) — karena Long dan Short bisa jalan bersamaan di koin yang sama.

## Menjalankan lokal (opsional)

```bash
pip install -r requirements.txt
cp .env.example .env   # lalu isi API_KEY & API_SECRET
export $(cat .env | xargs)   # linux/mac
python bot_ema_flip.py
```

## State & restart

Bot menyimpan progress (bias arah aktif, limit yang terpasang, posisi terbuka, penanda candle yang sudah diproses) ke `bot_state.json`. Kalau Railway redeploy/restart, bot akan lanjut dari state terakhir, bukan mulai dari nol — dan **tidak** akan membanjiri order dari sinyal historis lama (ada mekanisme inisialisasi sekali di run pertama yang menandai histori tanpa entry).

## Peringatan

- Ini adalah bot agresif (banyak sinyal, win rate menengah, mengandalkan trailing yang lari jauh untuk profit).
- Selalu mulai dengan `RISK_PCT` kecil dan `MAX_CONCURRENT` terbatas saat pertama kali live.
- Backtest dilakukan di 1 koin (XRPUSDT) periode 10 bulan — performa bisa berbeda di koin lain / kondisi pasar lain.
