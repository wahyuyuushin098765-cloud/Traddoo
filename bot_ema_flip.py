"""
============================================================
BOT RESISTANCE MURNI (H1) — LIMIT SELL DI UJUNG WICK C1
============================================================
Strategi diganti total dari versi EMA-cross/flip sebelumnya. Sekarang
memakai strategi hasil backtest_snr.py: Resistance murni, TANPA EMA cross,
TANPA RSI gate, TANPA swing gate, TANPA flip protection. Support SUDAH
DIHILANGKAN -- bot ini HANYA entry Short.

RINGKASAN STRATEGI
------------------
1. DETEKSI RESISTANCE (H1, basis body candle):
   - C1 bullish (close>open), C2 bearish (close<open). Level = close[C1].
   - KIRI: 5 candle SEBELUM C1 -- wick (high) tidak boleh melebihi level.
   - KANAN: 5 candle SETELAH C1,C2 (C3..C7) -- wick (high) tidak boleh
     menyentuh level SAMA SEKALI, semua 5 candle harus bersih.
   - WICK: C1 wajib punya wick atas sungguhan (beda dari body). Wick atas
     C2 harus LEBIH PANJANG dari wick C1. Kalau tidak terpenuhi, level
     gugur (tidak pernah dipakai).

2. ENTRY (begitu C7 closed & semua syarat di atas terpenuhi):
   - LANGSUNG pasang LIMIT SELL (GTC) di harga UJUNG WICK C1 (high[C1]),
     BUKAN di body/level. Order ini nyata nangkring di orderbook Bybit --
     tidak ada simulasi "approach/arm" seperti di backtest (karena di live
     order limitnya memang benar-benar terpasang, tinggal nunggu harga
     kesana).
   - SL = SL_PCT dari entry (di atas entry, karena Short).
   - Tiap level HANYA dipakai 1x (tidak ada re-entry, tidak ada bias yang
     "hidup" menunggu sinyal lain seperti versi lama).

3. TRAILING STOP native Bybit:
   - Aktif otomatis setelah profit mencapai TRAIL_ACT_R x jarak(entry,SL).
   - Lebar trailing = TRAIL_STOP x jarak.

Fitur yang DIHAPUS dari versi sebelumnya (sudah tidak relevan dengan
strategi baru ini): EMA cross (EMA_FAST/EMA_SLOW), RSI gate, swing gate,
flip protection (FLIP_MIN_R), bias Support/Long. Env var terkait fitur2
itu (EMA_FAST, EMA_SLOW, RSI_GATE_*, SWING_GATE_ENABLED, FLIP_MIN_R) boleh
tetap ada di Railway (tidak dipakai lagi, tidak error) atau dihapus.
============================================================
"""

import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
import os
import time
import sys
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

# ============================================================
# LOG SERVER — akses via https://xxx.up.railway.app/logs /entries /view /ohlc
# ============================================================
LOG_FILE   = "bot.log"
ENTRY_FILE = "entries.log"

def log_entry(text):
    import datetime
    ts = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)).strftime('[%Y-%m-%d %H:%M:%S] ')
    try:
        with open(ENTRY_FILE, 'a', encoding='utf-8') as f:
            f.write(ts + text.replace('\n', '\n' + ' ' * len(ts)) + '\n')
    except Exception:
        pass
    print(text)

class _Tee:
    def __init__(self):
        self._out     = sys.__stdout__
        self._file    = open(LOG_FILE, 'a', buffering=1, encoding='utf-8')
        self._newline = True
    def write(self, msg):
        import datetime
        out = ''
        for ch in msg:
            if self._newline and ch != '\n':
                out += (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)).strftime('[%H:%M:%S] ')
                self._newline = False
            out += ch
            if ch == '\n':
                self._newline = True
        self._out.write(out)
        self._file.write(out)
    def flush(self):
        self._out.flush()
        self._file.flush()

sys.stdout = _Tee()

LAST_OHLC = {}

def _parse_log_blocks(text):
    import re
    ts_re   = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ?')
    coin_re = re.compile(r'\b([A-Z0-9]{2,15}USDT)\b')
    blocks, cur = [], None
    for line in text.split('\n'):
        m = ts_re.match(line)
        if m:
            if cur is not None:
                blocks.append(cur)
            cur = {'ts': m.group(1), 'lines': [line]}
        elif cur is not None:
            cur['lines'].append(line)
    if cur is not None:
        blocks.append(cur)
    out = []
    for b in blocks:
        block_text = '\n'.join(b['lines']).rstrip('\n')
        cm = coin_re.search(block_text)
        out.append({'ts': b['ts'], 'coin': (cm.group(1) if cm else None), 'text': block_text})
    return out

class _LogHandler(BaseHTTPRequestHandler):
    def _send(self, body, ctype='text/plain; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        import datetime as _dt
        path = self.path.split('?', 1)[0]
        query = {}
        if '?' in self.path:
            for kv in self.path.split('?', 1)[1].split('&'):
                if '=' in kv:
                    k, v = kv.split('=', 1); query[k] = v

        if path == '/entries':
            try:
                with open(ENTRY_FILE, 'r', encoding='utf-8') as f:
                    data = f.read()
            except Exception:
                data = '(belum ada entry)'
            return self._send(data)

        if path == '/view':
            try:
                with open(ENTRY_FILE, 'r', encoding='utf-8') as f:
                    raw = f.read()
            except Exception:
                raw = ''
            blocks = _parse_log_blocks(raw)
            coin_last_ts = {}
            for b in blocks:
                if b['coin']:
                    coin_last_ts[b['coin']] = b['ts']
            coins_sorted = sorted(coin_last_ts.keys(), key=lambda c: coin_last_ts[c], reverse=True)
            html = ("<!doctype html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width, initial-scale=1, maximum-scale=1'>"
                    "<title>Bot Log</title>"
                    "<style>"
                    "*{box-sizing:border-box}"
                    "html,body{width:100%;overflow-x:hidden}"
                    "body{font-family:'Courier New',monospace;background:#0d0d0d;color:#ddd;margin:0;padding:0;"
                    "font-size:13px}"
                    ".topbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 10px;"
                    "background:#181818;border-bottom:1px solid #333;position:sticky;top:0;z-index:2}"
                    ".tabbtn{background:#222;color:#ccc;border:1px solid #444;border-radius:6px;padding:8px 14px;"
                    "cursor:pointer;font-size:13px;flex:0 0 auto}"
                    ".tabbtn.active{background:#2a6;color:#fff;border-color:#2a6}"
                    ".minilinks{display:flex;gap:10px;margin-left:auto;flex-wrap:wrap}"
                    "a.mini{color:#7ad;text-decoration:none;font-size:12px;white-space:nowrap}"
                    ".wrap{display:flex;flex-direction:column;min-height:calc(100vh - 48px)}"
                    "@media(min-width:700px){.wrap{flex-direction:row;height:calc(100vh - 48px)}}"
                    ".sidebar{display:none;border-bottom:1px solid #333;background:#151515;"
                    "max-height:38vh;overflow-y:auto}"
                    "@media(min-width:700px){.sidebar{max-height:none;height:100%;width:180px;"
                    "border-bottom:none;border-right:1px solid #333;flex:0 0 180px}}"
                    ".sidebar.show{display:block}"
                    ".coinbtn{display:block;width:100%;text-align:left;background:none;border:none;color:#ccc;"
                    "padding:10px 14px;cursor:pointer;font-size:13px;border-bottom:1px solid #222}"
                    ".coinbtn:active,.coinbtn:hover{background:#222}"
                    ".coinbtn.active{background:#26a;color:#fff}"
                    ".main{flex:1;overflow-y:auto;overflow-x:hidden;padding:8px 10px;white-space:pre-wrap;"
                    "word-break:break-word;font-size:12px;line-height:1.5;-webkit-overflow-scrolling:touch}"
                    ".blk{padding:5px 0;border-bottom:1px solid #1c1c1c}"
                    "@media(min-width:700px){.main{font-size:13px;padding:10px 16px}}"
                    "</style></head><body>"
                    "<div class='topbar'>"
                    "<button id='tab-semua' class='tabbtn active' onclick=\"setTab('semua')\">Semua</button>"
                    "<button id='tab-percoin' class='tabbtn' onclick=\"setTab('percoin')\">Per Koin</button>"
                    "<div class='minilinks'>"
                    "<a class='mini' href='/entries'>raw</a>"
                    "<a class='mini' href='/logs'>console</a>"
                    "<a class='mini' href='/ohlc'>ohlc</a>"
                    "</div></div>"
                    "<div class='wrap'>"
                    "<div id='sidebar' class='sidebar'></div>"
                    "<div id='main' class='main'></div>"
                    "</div>"
                    "<script>"
                    f"const BLOCKS = {json.dumps(blocks)};"
                    f"const COINS = {json.dumps(coins_sorted)};"
                    "let mode='semua', selCoin=null;"
                    "function render(){"
                    "  const main=document.getElementById('main');"
                    "  const sidebar=document.getElementById('sidebar');"
                    "  document.getElementById('tab-semua').className='tabbtn'+(mode==='semua'?' active':'');"
                    "  document.getElementById('tab-percoin').className='tabbtn'+(mode==='percoin'?' active':'');"
                    "  if(mode==='semua'){"
                    "    sidebar.className='sidebar';"
                    "    main.innerHTML=BLOCKS.map(b=>'<div class=\"blk\">'+esc(b.text)+'</div>').join('');"
                    "  } else {"
                    "    sidebar.className='sidebar show';"
                    "    sidebar.innerHTML=COINS.map(c=>'<button class=\"coinbtn'+(c===selCoin?' active':'')+'\" "
                    "onclick=\"selectCoin(\\''+c+'\\')\">'+c+'</button>').join('');"
                    "    if(!selCoin){main.innerHTML='<i>Pilih koin di atas/kiri.</i>';}"
                    "    else{"
                    "      const filtered=BLOCKS.filter(b=>b.coin===selCoin);"
                    "      main.innerHTML=filtered.length?filtered.map(b=>'<div class=\"blk\">'+esc(b.text)+'</div>').join('')"
                    "        :'<i>Belum ada log untuk '+selCoin+'.</i>';"
                    "    }"
                    "  }"
                    "  main.scrollTop=main.scrollHeight;"
                    "}"
                    "function esc(s){const d=document.createElement('div');d.innerText=s;return d.innerHTML;}"
                    "function setTab(m){mode=m;render();}"
                    "function selectCoin(c){selCoin=c;render();}"
                    "render();"
                    "</script></body></html>")
            return self._send(html, 'text/html; charset=utf-8')

        if path == '/logs':
            try:
                with open(LOG_FILE, 'r', encoding='utf-8') as f:
                    data = ''.join(f.readlines()[-200:])
            except Exception:
                data = ''
            return self._send(data)

        if path == '/ohlc':
            sym = query.get('symbol'); tf = query.get('tf', '60')
            if sym:
                df = LAST_OHLC.get((sym, str(tf)))
                if df is None:
                    return self._send(f"(data {sym} tf{tf} belum ada — tunggu bot scan dulu)")
                rows = ["ts_ms,waktu_WIB,open,high,low,close,volume"]
                for _, r in df.iterrows():
                    t = _dt.datetime.utcfromtimestamp(int(r['ts']) / 1000) + _dt.timedelta(hours=7)
                    rows.append(f"{int(r['ts'])},{t:%Y-%m-%d %H:%M:%S},"
                                f"{r['open']:.10g},{r['high']:.10g},{r['low']:.10g},{r['close']:.10g},{r.get('vol',0):.10g}")
                csv = "\n".join(rows)
                fname = f"{sym}_tf{tf}_{_dt.datetime.utcnow():%Y%m%d_%H%M}.csv"
                return self._send(csv, 'text/csv; charset=utf-8',
                                  {'Content-Disposition': f'attachment; filename="{fname}"'})
            keys = sorted(LAST_OHLC.keys())
            if not keys:
                return self._send("<h3>Belum ada data. Tunggu bot scan beberapa detik lalu refresh.</h3>"
                                  "<a href='/ohlc'>refresh</a>", 'text/html; charset=utf-8')
            syms = sorted({k[0] for k in keys})
            html = ["<html><head><meta charset='utf-8'><title>Unduh OHLC</title>",
                    "<style>body{font-family:sans-serif;background:#111;color:#eee;padding:16px}"
                    "a.btn{display:inline-block;margin:3px;padding:6px 10px;background:#2a6;color:#fff;"
                    "text-decoration:none;border-radius:5px}h4{margin:14px 0 4px}</style></head><body>",
                    "<h2>Unduh OHLC (data yg dilihat bot)</h2>",
                    "<p><a href='/logs'>/logs</a> · <a href='/entries'>/entries</a> · <a href='/view'>/view</a> · <a href='/ohlc'>refresh</a></p>"]
            for s in syms:
                html.append(f"<h4>{s}</h4>")
                if (s, '60') in LAST_OHLC:
                    html.append(f"<a class='btn' href='/ohlc?symbol={s}&tf=60'>⬇ H1 (60m)</a>")
            html.append("</body></html>")
            return self._send("\n".join(html), 'text/html; charset=utf-8')

        if path == '/':
            return self._send("<html><body style='font-family:sans-serif;background:#111;color:#eee;padding:16px'>"
                              "<h2>Bot EMA-Cross Reversal + Flip Protection</h2>"
                              "<p><a href='/view' style='color:#6cf'>/view</a> · "
                              "<a href='/logs' style='color:#6cf'>/logs</a> · "
                              "<a href='/entries' style='color:#6cf'>/entries</a> · "
                              "<a href='/ohlc' style='color:#6cf'><b>/ohlc — unduh data OHLC</b></a></p></body></html>",
                              'text/html; charset=utf-8')

        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass

PORT = int(os.environ.get('PORT', 8080))
threading.Thread(
    target=lambda: HTTPServer(('0.0.0.0', PORT), _LogHandler).serve_forever(),
    daemon=True
).start()
print(f"📡 Log server jalan di port {PORT} → /logs")

# ============================================================
# CONFIG
# ============================================================
API_KEY    = os.environ.get('API_KEY', '')
API_SECRET = os.environ.get('API_SECRET', '')
CATEGORY   = "linear"
TESTNET    = os.environ.get('TESTNET', 'false').lower() == 'true'

if not API_KEY or not API_SECRET:
    raise ValueError("❌ API_KEY dan API_SECRET belum diset!")

session = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)

# ── Strategy params (Resistance murni, hasil backtest_snr.py) ──
TIMEFRAME        = "60"    # H1 saja
TRAIL_ACT_R      = float(os.environ.get('TRAIL_ACT_R', 4.0))   # trailing aktif di rasio 1:TRAIL_ACT_R dari SL
TRAIL_STOP       = float(os.environ.get('TRAIL_STOP', 1.0))    # lebar trailing = TRAIL_STOP x jarak(entry,SL)
TRAIL_TIMEOUT_DAYS = 3      # safety net: force-close kalau peak macet N hari (None = matikan)
RISK_PCT         = float(os.environ.get('RISK_PCT', 0.01))     # risk per trade = 1% equity
LEVERAGE         = int(os.environ.get('LEVERAGE', 25))
MIN_ORDER_USD    = 5.0
ORDER_BUMP_FLOOR = 4.0
MAX_CONCURRENT   = int(os.environ.get('MAX_CONCURRENT', 10))
MIN_DIST_PCT     = float(os.environ.get('MIN_DIST_PCT', 0.002))   # floor keamanan SL minimum
                             # dari entry (jaga2, seharusnya tidak pernah kepakai krn SL_PCT
                             # default > floor ini)
SL_PCT           = float(os.environ.get('SL_PCT', 0.02))   # jarak SL dari entry (wick c1)

ALLOW_HEDGE = os.environ.get('ALLOW_HEDGE', 'true').lower() == 'true'
def _pidx(side):
    return (1 if side == "Buy" else 2) if ALLOW_HEDGE else 0
def _akey(coin, direction):
    return f"{coin}|{direction}" if ALLOW_HEDGE else coin

# Hasil backtest strategi Resistance murni (1 tahun H1) -- hanya koin dengan
# ROI% > 0 yang dipakai bot live ini.
SYMBOLS = [
    'CFXUSDT',    # +31.7%
    'LTCUSDT',    # +22.0%
    'GALAUSDT',   # +21.7%
    'AVAXUSDT',   # +10.8%
    'SOLUSDT',    # +10.4%
    'SANDUSDT',   # +9.1%
    'DOGEUSDT',   # +9.1%
    'UNIUSDT',    # +7.6%
    'MNTUSDT',    # +6.7%
    'ORCAUSDT',   # +6.3%
    'DASHUSDT',   # +5.3%
    'XVGUSDT',    # +3.6%
    'AAVEUSDT',   # +2.3%
]

bot_start_ts      = 0
pending           = {}   # _akey -> {'coin','direction','entry','sl','dist','order_id'}
active_positions  = {}   # _akey -> {'coin','side','entry','sl','dist','trail_dist','trail_set',...}
last_seen         = {}   # coin -> confirm_ts level resistance TERAKHIR yg sudah diproses (dedup)

instrument_cache = {}

# ============================================================
# STATE PERSISTENCE
# ============================================================
STATE_FILE = os.environ.get("STATE_FILE_PATH", "bot_state.json")

def save_state():
    try:
        data = {
            "pending": pending, "active_positions": active_positions,
            "last_seen": last_seen,
        }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        print(f"⚠️ save_state gagal: {e}")

def load_state():
    global pending, active_positions, last_seen
    if not os.path.exists(STATE_FILE):
        print(f"ℹ️ {STATE_FILE} belum ada — mulai dari kosong (normal di run pertama).")
        return
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        pending           = data.get("pending", {})
        active_positions  = data.get("active_positions", {})
        last_seen         = data.get("last_seen", {})
        print(f"✅ State dimuat: {len(pending)} pending, "
              f"{len(active_positions)} posisi aktif.")
    except Exception as e:
        print(f"⚠️ load_state gagal ({e}) — mulai dari kosong.")

# ============================================================
# FUNGSI DATA
# ============================================================

def get_data(symbol, interval, limit=200):
    try:
        res = session.get_kline(category=CATEGORY, symbol=symbol, interval=interval, limit=limit)
        if res['retCode'] == 0:
            df = pd.DataFrame(res['result']['list'], columns=['ts','open','high','low','close','vol','turnover'])
            df[['open','high','low','close','vol','turnover','ts']] = \
                df[['open','high','low','close','vol','turnover','ts']].apply(pd.to_numeric)
            df = df.iloc[::-1].reset_index(drop=True)
            LAST_OHLC[(symbol, str(interval))] = df
            return df
        print(f"⚠️ get_data {symbol} {interval}: {res.get('retMsg','')}")
        return None
    except Exception as e:
        print(f"⚠️ get_data {symbol} {interval}: {e}")
        return None


def get_instrument_info(symbol):
    if symbol in instrument_cache:
        return instrument_cache[symbol]
    try:
        res = session.get_instruments_info(category=CATEGORY, symbol=symbol)
        if res['retCode'] == 0:
            info = res['result']['list'][0]
            lot  = info['lotSizeFilter']
            data = {
                'min_qty'     : float(lot['minOrderQty']),
                'qty_step'    : float(lot['qtyStep']),
                'tick_size'   : float(info['priceFilter']['tickSize']),
                'max_leverage': float(info.get('leverageFilter', {}).get('maxLeverage', 10)),
            }
            instrument_cache[symbol] = data
            return data
    except Exception as e:
        print(f"⚠️ instrument_info {symbol}: {e}")
    return {'min_qty': 0.01, 'qty_step': 0.01, 'tick_size': 0.0001, 'max_leverage': 10}


def round_qty(qty, step):
    step_str  = f'{step:.10f}'.rstrip('0')
    precision = len(step_str.split('.')[-1]) if '.' in step_str else 0
    return round(int(qty / step) * step, precision)


def round_price(price, tick):
    tick_str  = f'{tick:.10f}'.rstrip('0')
    precision = len(tick_str.split('.')[-1]) if '.' in tick_str else 0
    return round(round(price / tick) * tick, precision)


# ============================================================
# DETEKSI SUPPORT / RESISTANCE (H1, basis body candle) — sumber bias arah
# ============================================================

# ============================================================
# DETEKSI RESISTANCE (H1, basis body candle) — Resistance murni,
# port dari backtest_snr.py. Support SUDAH DIHILANGKAN.
# ============================================================

N_LEFT   = 5    # candle kiri yang wajib bersih (wick tidak boleh melebihi level)
N_RIGHT  = 5    # candle kanan yang wajib bersih (wick tidak boleh menyentuh level)
WICK_EPS = 1e-9   # beda minimum supaya wick dianggap "ada" (tidak sama dgn body)

def find_levels(df):
    """Deteksi level Resistance dari candle H1 (basis body candle).
    C1 bullish -> C2 bearish. Level = close[C1]. Valid kalau:
      - KIRI : 5 candle sebelum C1, wick (high) tidak boleh melebihi level.
      - KANAN: 5 candle setelah C2 (C3..C7), wick (high) tidak boleh
        menyentuh level SAMA SEKALI, semua 5 candle harus bersih.
      - WICK : C1 wajib punya wick atas sungguhan (beda dari body), dan
        wick atas C2 harus LEBIH PANJANG dari wick C1.
    entry_price = UJUNG WICK C1 (high[C1]), bukan body/level.
    confirm_ts  = waktu candle kanan terakhir (C7) selesai -- level baru
                  boleh dipakai SETELAH candle ini closed.
    c1_ts       = waktu candle C1 -- "tempat" resistance itu sesungguhnya
                  terbentuk (dipakai buat log/laporan saja).
    Return list dict: {'level','entry_price','c1_ts','confirm_ts'}."""
    o = df['open'].values; h = df['high'].values; l = df['low'].values; c = df['close'].values
    ts = df['ts'].values
    n = len(df)
    levels = []
    for i in range(N_LEFT, n - (1 + N_RIGHT)):
        if c[i] > o[i] and c[i + 1] < o[i + 1]:          # bullish lalu bearish -> resistance
            R = c[i]
            right_ok = all(h[i + 2 + k] < R - 1e-9 for k in range(N_RIGHT))
            left_ok = all(h[i - 1 - k] < R - 1e-9 for k in range(N_LEFT))
            upper_wick_c1 = h[i] - c[i]          # wick atas C1 (bullish): high - close
            upper_wick_c2 = h[i + 1] - o[i + 1]  # wick atas C2 (bearish): high - open
            wick_ok = (upper_wick_c1 > WICK_EPS) and (upper_wick_c2 > upper_wick_c1 + WICK_EPS)
            if right_ok and left_ok and wick_ok:
                last_right_i = i + 2 + (N_RIGHT - 1)   # index C7
                levels.append({
                    'level': R, 'entry_price': h[i],
                    'c1_ts': int(ts[i]), 'confirm_ts': int(ts[last_right_i]),
                })
    levels.sort(key=lambda e: e['confirm_ts'])
    return levels


# ============================================================
# FUNGSI ORDER
# ============================================================

def place_limit_order(symbol, side, entry_p, sl_p):
    """Limit order GTC di entry_p (ujung wick c1, level resistance), SL + trailing native Bybit
    langsung terpasang. Trailing aktif setelah profit +TRAIL_ACT_R x dist (rasio 1:TRAIL_ACT_R)."""
    try:
        info    = get_instrument_info(symbol)
        res_bal = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        acct    = res_bal['result']['list'][0]
        balance = float(acct['totalEquity'])
        avail   = float(acct.get('totalAvailableBalance') or balance)
        risk_usd = balance * RISK_PCT
        dist     = abs(entry_p - sl_p)
        if dist == 0:
            print(f"⚠️ {symbol}: dist entry-SL = 0, skip.")
            return None

        min_dist = entry_p * MIN_DIST_PCT
        if dist < min_dist:
            dist  = min_dist
            sl_p  = entry_p - dist if side == "Buy" else entry_p + dist

        raw_qty = risk_usd / dist
        qty     = round_qty(raw_qty, info['qty_step'])
        if qty < info['min_qty']:
            print(f"⚠️ {symbol}: Qty {qty} < minOrderQty {info['min_qty']}, skip.")
            return None

        order_value = qty * entry_p
        if order_value < MIN_ORDER_USD:
            if order_value >= ORDER_BUMP_FLOOR:
                old_ov = order_value
                qty = round_qty(MIN_ORDER_USD / entry_p, info['qty_step'])
                if qty * entry_p < MIN_ORDER_USD:
                    qty = round_qty(qty + info['qty_step'], info['qty_step'])
                order_value = qty * entry_p
                new_risk = qty * dist
                print(f"⬆️ {symbol}: order ${old_ov:.2f}->${order_value:.2f} "
                      f"(risk ${new_risk:.2f} ~ {new_risk/risk_usd:.2f}x target).")
            else:
                print(f"⚠️ {symbol}: Order ~${order_value:.2f} < ${ORDER_BUMP_FLOOR:.0f}, skip.")
                return None

        entry_r  = round_price(entry_p, info['tick_size'])
        sl_r     = round_price(sl_p,    info['tick_size'])
        trail_r  = round_price(TRAIL_STOP * dist, info['tick_size'])
        active_r = round_price(
            entry_p + TRAIL_ACT_R * dist if side == "Buy"
            else entry_p - TRAIL_ACT_R * dist, info['tick_size'])

        lev_int = 10
        try:
            max_lev = float(info.get('max_leverage', 10))
            lev_int = int(min(LEVERAGE, max_lev))
            res_lev = session.set_leverage(category=CATEGORY, symbol=symbol,
                                           buyLeverage=str(lev_int), sellLeverage=str(lev_int))
            if res_lev.get('retCode', -1) not in (0, 110043):
                print(f"   ⚠️ {symbol}: set_leverage gagal: {res_lev.get('retMsg','')} — coba lanjut")
        except Exception as e:
            if '110043' not in str(e):
                print(f"   ⚠️ {symbol}: set_leverage error: {e} — coba lanjut")

        required_margin = (qty * entry_p) / lev_int
        if required_margin > avail * 0.9:
            print(f"⚠️ {symbol}: Margin tidak cukup — butuh ~${required_margin:.2f}, avail ${avail:.2f}. Skip.")
            return None

        print(f"   Balance:{balance:.2f} Avail:{avail:.2f} Risk:{risk_usd:.2f} Dist:{dist:.6f} "
              f"Trail:{trail_r} ActiveP:{active_r} Qty:{qty} Entry:{entry_r} SL:{sl_r} "
              f"Lev:{lev_int}x Margin:~${required_margin:.2f}")

        res = session.place_order(
            category=CATEGORY, symbol=symbol, side=side,
            orderType="Limit", qty=str(qty), price=str(entry_r),
            stopLoss=str(sl_r), trailingStop=str(trail_r), activePrice=str(active_r),
            positionIdx=_pidx(side), timeInForce="GTC")
        if res['retCode'] == 0:
            return res['result']['orderId'], qty, entry_r, sl_r, dist
        print(f"⚠️ {symbol}: Limit order ditolak → {res.get('retMsg','')} (code:{res['retCode']})")
        return None
    except Exception as e:
        print(f"⚠️ {symbol}: place_limit_order error → {e}")
        return None


def cancel_order(symbol, order_id):
    try:
        res = session.cancel_order(category=CATEGORY, symbol=symbol, orderId=order_id)
        if res['retCode'] == 0:
            print(f"   ✅ {symbol}: Order {order_id[:8]}… dibatalkan.")
        else:
            print(f"   ⚠️ {symbol}: Cancel gagal → {res.get('retMsg','')} (code:{res['retCode']})")
    except Exception as e:
        print(f"   ⚠️ {symbol}: cancel_order error → {e}")


def _order_exists(symbol, order_id):
    try:
        res = session.get_open_orders(category=CATEGORY, symbol=symbol, orderId=order_id)
        if res['retCode'] == 0:
            for o in res['result']['list']:
                if o.get('orderId') == order_id and \
                        o.get('orderStatus') in ('New', 'PartiallyFilled', 'Untriggered'):
                    return True
            return False
    except Exception:
        pass
    return False


def get_open_position(symbol, want_side=None):
    try:
        res = session.get_positions(category=CATEGORY, symbol=symbol)
        if res['retCode'] == 0:
            for pos in res['result']['list']:
                if float(pos['size']) <= 0:
                    continue
                if ALLOW_HEDGE and want_side is not None and pos.get('side') != want_side:
                    continue
                return pos
        return None
    except Exception:
        return None


def close_position(symbol, side, qty_str, reason="manual"):
    """Force-close posisi dengan market order reduceOnly (dipakai FLIP protection & trail timeout)."""
    try:
        close_side = 'Sell' if side == 'Buy' else 'Buy'
        info  = get_instrument_info(symbol)
        qty_r = round_qty(float(qty_str), info['qty_step'])
        if qty_r <= 0:
            return False
        res = session.place_order(
            category=CATEGORY, symbol=symbol, side=close_side, orderType="Market",
            qty=str(qty_r), reduceOnly=True, positionIdx=_pidx(side), timeInForce="IOC"
        )
        if res.get('retCode') == 0:
            print(f"⏹️  {symbol}: Posisi ditutup market ({reason})")
            return True
        print(f"⚠️ {symbol}: close_position gagal → {res.get('retMsg','')} (code:{res.get('retCode')})")
        return False
    except Exception as e:
        print(f"⚠️ {symbol}: close_position error → {e}")
        return False


def _get_actual_exit_price(symbol):
    try:
        res = session.get_closed_pnl(category=CATEGORY, symbol=symbol, limit=1)
        if res['retCode'] == 0 and res['result']['list']:
            exit_p = float(res['result']['list'][0].get('avgExitPrice', 0))
            if exit_p > 0:
                return exit_p
    except Exception as e:
        print(f"⚠️ {symbol}: get_closed_pnl error: {e}")
    return None


# ============================================================
# TRAILING STOP (fallback pemasangan + deteksi posisi closed)
# ============================================================

def check_trailing_sl(key):
    if key not in active_positions:
        return
    p    = active_positions[key]
    coin = p.get('coin', key)
    side = p.get('side')
    pos  = get_open_position(coin, side)

    if pos is None:
        actual_exit = _get_actual_exit_price(coin)
        exit_str    = f"{actual_exit:.6f}" if actual_exit else "?"
        log_entry(f"📭 {coin} [{p.get('direction','')}]: Posisi tutup @ {exit_str} "
                  f"(entry {p.get('entry',0):.6g} SL {p.get('sl',0):.6g}).")
        del active_positions[key]
        return

    try:
        curr_price = float(pos['markPrice'])
        entry = p['entry']; dist = p.get('dist', 0); side = p['side']

        peak      = p.get('peak', entry)
        peak_time = p.get('peak_time', p.get('entry_time', time.time()))
        new_peak  = max(peak, curr_price) if side == 'Buy' else min(peak, curr_price)
        if new_peak != peak:
            active_positions[key]['peak']      = new_peak
            active_positions[key]['peak_time'] = time.time()
            peak_time = time.time()

        if TRAIL_TIMEOUT_DAYS:
            timeout_sec = TRAIL_TIMEOUT_DAYS * 24 * 3600
            if time.time() - peak_time > timeout_sec:
                qty_pos = pos.get('size', '0')
                hours_stuck = (time.time() - peak_time) / 3600
                print(f"⏰ {coin}: Trail timeout {TRAIL_TIMEOUT_DAYS} hari (peak stuck {hours_stuck:.1f}h)")
                if close_position(coin, side, qty_pos, reason="trail timeout"):
                    log_entry(f"⏰ {coin} [{p.get('direction','')}]: Ditutup paksa (trail timeout).")
                    del active_positions[key]
                return

        if dist > 0 and not p.get('trail_set', False):
            trail_dist = p.get('trail_dist', TRAIL_STOP * dist)
            info       = get_instrument_info(coin)
            tick       = info.get('tick_size', 0.0001)
            trail_r    = round_price(trail_dist, tick)
            active_p   = round_price(entry + TRAIL_ACT_R * dist if side == "Buy" else entry - TRAIL_ACT_R * dist, tick)
            if trail_r > 0 and active_p > 0:
                try:
                    res_ts = session.set_trading_stop(
                        category=CATEGORY, symbol=coin, trailingStop=str(trail_r),
                        activePrice=str(active_p), positionIdx=_pidx(side))
                    if res_ts['retCode'] == 0:
                        active_positions[key]['trail_set'] = True
                        print(f"📍 {coin}: Trailing stop {trail_r} dipasang (aktif @ {active_p} = entry±{TRAIL_ACT_R}R)")
                    else:
                        print(f"⚠️ {coin}: Gagal set trailing stop: {res_ts.get('retMsg','')} (code:{res_ts['retCode']})")
                except Exception as e:
                    print(f"⚠️ {coin}: set_trading_stop error: {e}")
    except Exception:
        pass


# ============================================================
# KONEKSI
# ============================================================

def test_connection():
    try:
        res = session.get_server_time()
        if res['retCode'] == 0:
            print(f"✅ Koneksi Bybit OK | Server time: {res['result']['timeSecond']}")
            return True
        print(f"❌ Bybit error: {res}")
        return False
    except Exception as e:
        print(f"❌ Gagal konek: {e}")
        return False


# ============================================================
# LOGIKA UTAMA per koin: deteksi resistance baru -> langsung pasang limit Short
# ============================================================

def _count_slots():
    return len(active_positions) + len(pending)


def process_resistance_signals(coin, df_closed):
    """Cek level Resistance baru (confirm_ts > last_seen[coin]) -> LANGSUNG pasang
    LIMIT SELL (GTC) di ujung wick C1. Tidak ada bias/re-entry/flip -- tiap level
    resistance yang valid dipakai PERSIS SEKALI: begitu confirm, coba pasang limit
    (kalau slot penuh, dilewati -- tidak dicoba ulang, sama seperti versi cross
    sebelumnya)."""
    key = _akey(coin, 'Short')
    levels = find_levels(df_closed)
    newest_seen = last_seen.get(coin, 0)

    for lv in levels:
        if lv['confirm_ts'] <= newest_seen:
            continue
        newest_seen = lv['confirm_ts']   # tandai diproses APAPUN hasilnya (tidak diulang lagi)

        if key in pending or key in active_positions:
            print(f"⏭️  {coin} [Short]: level resistance baru muncul tp sudah ada "
                  f"pending/posisi Short, skip.")
            continue

        entry = lv['entry_price']
        dist  = entry * SL_PCT
        if dist <= 0:
            continue
        sl = entry + dist   # Short -> SL di atas entry

        if _count_slots() >= MAX_CONCURRENT:
            print(f"⏭️  {coin} [Short]: slot penuh ({MAX_CONCURRENT}), level resistance dilewati.")
            continue

        result = place_limit_order(coin, "Sell", entry, sl)
        if result is not None:
            order_id, qty, entry_r, sl_r, dist_r = result
            pending[key] = {'coin': coin, 'direction': 'Short',
                             'entry': entry_r, 'sl': sl_r, 'dist': dist_r, 'order_id': order_id}
            log_entry(f"📉 {coin} [Short]: Resistance valid (c1 @ {lv['c1_ts']}) — "
                      f"limit SELL @ wick {entry_r:.6g} SL {sl_r:.6g}")

    last_seen[coin] = newest_seen


def manage_pending(coin):
    """Cek pending order utk coin ini: sudah fill? order masih ada di exchange?"""
    key = _akey(coin, 'Short')
    st  = pending.get(key)
    if st is None:
        return

    pos = get_open_position(coin, 'Sell')
    if pos is not None:
        entry_actual = float(pos.get('avgPrice') or st['entry'])
        dist_actual  = abs(entry_actual - st['sl'])
        active_positions[key] = {
            'coin': coin, 'side': 'Sell', 'direction': 'Short',
            'entry': entry_actual, 'sl': st['sl'], 'dist': dist_actual,
            'trail_dist': TRAIL_STOP * dist_actual, 'trail_set': False,
            'peak': entry_actual, 'peak_time': time.time(), 'entry_time': time.time(),
        }
        log_entry(f"✅ {coin} [Short]: LIMIT FILLED @ {entry_actual:.6g} SL {st['sl']:.6g}")
        del pending[key]
        return

    if not _order_exists(coin, st['order_id']):
        print(f"⚠️ {coin} [Short]: order {st['order_id'][:8]}… tak ditemukan lagi — dibuang dari pending.")
        del pending[key]


# ============================================================
# MAIN LOOP
# ============================================================

def run_bot():
    global bot_start_ts
    bot_start_ts = time.time()
    load_state()
    print("BOT RESISTANCE MURNI (Short saja) — H1")
    print(f"CONFIG | trail aktif 1:{TRAIL_ACT_R:.0f} | trail width {TRAIL_STOP:.1f}x | "
          f"risk {RISK_PCT*100:.0f}%/trade | lev {LEVERAGE}x | slot max {MAX_CONCURRENT} | "
          f"HEDGE {'ON' if ALLOW_HEDGE else 'off'} | SL {SL_PCT*100:.2f}% dari entry (wick c1) | "
          f"{len(SYMBOLS)} koin")
    if not test_connection():
        print("⛔ Tidak bisa konek ke Bybit.")
        return
    if ALLOW_HEDGE:
        try:
            r = session.switch_position_mode(category=CATEGORY, coin="USDT", mode=3)
            rc = r.get('retCode', -1)
            if rc == 0:
                print("🔀 Hedge mode AKTIF.")
            elif rc == 110025:
                print("🔀 Hedge mode sudah aktif.")
            else:
                print(f"⚠️ switch_position_mode: {r.get('retMsg','')} (code:{rc})")
        except Exception as e:
            print(f"⚠️ switch_position_mode error: {e}")

    first_run = (len(last_seen) == 0)

    while True:
        now = time.time()
        wait_sec = 300 - (now % 300) + 2
        if wait_sec > 300:
            wait_sec = 2
        print(f"⏱️  Tunggu {wait_sec:.0f} detik...")
        time.sleep(wait_sec)

        for _k in list(active_positions.keys()):
            try:
                check_trailing_sl(_k)
            except Exception as e:
                print(f"⚠️ Trailing SL {_k}: {e}")

        n_active, n_pending = len(active_positions), len(pending)
        print(f"\n{'='*55}")
        print(f"📊 SLOT: {n_active + n_pending}/{MAX_CONCURRENT} (posisi:{n_active} | limit:{n_pending})")
        for k, p in active_positions.items():
            print(f"   POSISI {p.get('coin')} [{p.get('direction')}] @ {p.get('entry',0):.6g} SL:{p.get('sl',0):.6g}")
        for k, s in pending.items():
            print(f"   LIMIT  {s.get('coin')} [{s.get('direction')}] @ {s.get('entry',0):.6g} SL:{s.get('sl',0):.6g}")
        print(f"{'='*55}")

        for coin in SYMBOLS:
            try:
                time.sleep(2)
                df_all = get_data(coin, TIMEFRAME, limit=200)
                if df_all is None or len(df_all) < (N_LEFT + 2 + N_RIGHT + 1):
                    continue
                df_closed = df_all.iloc[:-1].reset_index(drop=True)   # buang candle yg masih berjalan

                if first_run:
                    # Inisialisasi run pertama: tandai semua level historis sbg sudah
                    # diproses TANPA memasang order (hindari banjir entry dari sinyal lama).
                    levels = find_levels(df_closed)
                    if levels:
                        last_seen[coin] = max(lv['confirm_ts'] for lv in levels)
                    continue

                manage_pending(coin)
                process_resistance_signals(coin, df_closed)

            except Exception as e:
                print(f"⚠️ Error {coin}: {e}")
                continue

        if first_run:
            first_run = False
            print("✅ Inisialisasi selesai — histori lama ditandai, mulai memantau sinyal BARU mulai sekarang.")

        save_state()


if __name__ == "__main__":
    run_bot()
