# Resistance Bot (H1, Short saja)

Bot trading otomatis untuk Bybit Futures (USDT Perpetual), timeframe H1 saja.
Strategi: **Resistance murni**, hasil riset & backtest `backtest_snr.py`
(45 koin, 1 tahun H1) — hanya koin dengan ROI% positif yang dipakai.

⚠️ Backtest ≠ jaminan hasil live. Selalu tes di **Testnet** dulu sebelum live.

## Cara kerja strategi

1. **Deteksi Resistance** (basis body candle H1):
   - C1 bullish (close>open) → C2 bearish (close<open). Level = close[C1].
   - **KIRI**: 5 candle sebelum C1 — wick (high) tidak boleh melebihi level.
   - **KANAN**: 5 candle setelah C2 (C3–C7) — wick (high) tidak boleh
     menyentuh level sama sekali, semua 5 candle harus bersih.
   - **WICK**: C1 wajib punya wick atas sungguhan (beda dari body), dan
     wick atas C2 harus **lebih panjang** dari wick C1. Kalau tidak
     terpenuhi, level gugur.
   - Strategi **Support sudah dihilangkan** — bot ini hanya entry **Short**.

2. **Entry**: begitu candle C7 closed dan semua syarat terpenuhi, bot
   **langsung** memasang **limit SELL (GTC)** di harga **ujung wick C1**
   (bukan body/level). SL = `SL_PCT` dari entry (di atas entry, karena
   Short). Tiap level resistance **hanya dipakai 1x** — tidak ada
   re-entry, tidak ada bias yang "hidup" menunggu sinyal lain.

3. **Trailing stop native Bybit**: aktif otomatis setelah profit mencapai
   rasio `TRAIL_ACT_R` dari jarak entry–SL, lebar trailing `TRAIL_STOP` ×
   jarak.

Fitur dari versi bot sebelumnya (EMA cross, RSI gate, swing gate, flip
protection, bias Support→Short/Resistance→Long) **sudah dihapus** karena
tidak lagi relevan dengan strategi ini.

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

| Var | Default | Keterangan |
|---|---|---|
| `API_KEY` / `API_SECRET` | — | **wajib** |
| `TESTNET` | `false` | `true` untuk testnet |
| `SL_PCT` | `0.02` | jarak SL dari entry (wick c1) |
| `TRAIL_ACT_R` | `4.0` | trailing aktif di rasio 1:N dari SL |
| `TRAIL_STOP` | `1.0` | lebar trailing = N × jarak(entry,SL) |
| `RISK_PCT` | `0.01` | risk per trade (1% equity) |
| `LEVERAGE` | `25` | leverage |
| `MAX_CONCURRENT` | `10` | slot maksimum (posisi + limit pending) |
| `MIN_DIST_PCT` | `0.002` | floor keamanan SL minimum dari entry |
| `ALLOW_HEDGE` | `true` | wajib `true` kalau mau Long & Short bareng (saat ini strategi cuma Short) |

Beberapa var Railway lain yang mungkin masih ada dari setup sebelumnya
(`EMA_FAST`, `EMA_SLOW`, `RSI_GATE_*`, `SWING_GATE_ENABLED`, `FLIP_MIN_R`,
`FEE_ENTRY_PCT`, `FEE_EXIT_PCT`, `FILTER_*`, `BACKTEST_DAYS`, `CACHE_DIR`,
`INITIAL_BALANCE`) **tidak dipakai lagi** oleh bot ini — aman dibiarkan
atau dihapus dari Railway, tidak akan menyebabkan error.

⚠️ **Hedge Mode** diaktifkan otomatis bot saat start (untuk jaga-jaga kalau nanti ditambah arah Long lagi).

## Menjalankan lokal (opsional)

```bash
pip install -r requirements.txt
cp .env.example .env   # lalu isi API_KEY & API_SECRET
export $(cat .env | xargs)   # linux/mac
python bot_ema_flip.py
```

## State & restart

Bot menyimpan progress (level resistance terakhir yang sudah diproses,
limit yang terpasang, posisi terbuka) ke `bot_state.json`. Kalau Railway
redeploy/restart, bot akan lanjut dari state terakhir, bukan mulai dari
nol — dan **tidak** akan membanjiri order dari sinyal historis lama (ada
mekanisme inisialisasi sekali di run pertama yang menandai histori tanpa
entry).

## Peringatan

- Selalu mulai dengan `RISK_PCT` kecil dan `MAX_CONCURRENT` terbatas saat pertama kali live.
- Backtest dilakukan di 45 koin, 1 tahun H1 — hanya koin dengan ROI% positif yang masuk `SYMBOLS` di kode. Performa live bisa berbeda dari backtest.
