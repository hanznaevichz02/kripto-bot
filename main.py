"""
========================================================
    KRIPTO BOT — Smart Money Edition (Main Scanner)
    Versi: 4.0 (Integrasi Informan Pribadi & Radar)
    SPOT MARKET
========================================================
"""

import os
import json
import asyncio
import logging
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import httpx
import pandas as pd
import ccxt.async_support as ccxt
from telegram import Bot

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CryptoBot")

# ============================================================
# KONFIGURASI UTAMA
# ============================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MIN_RRR_THRESHOLD = 1.2      # Batas minimum RRR disesuaikan untuk scalping/swing pendek
MIN_PROFIT_PCT_THRESHOLD = 1.3 # Target profit minimal untuk cover fee transaksi dan pajak

PORTFOLIO: Dict[str, Dict[str, float]] = {
    'BTC/USDT': {'buy_price_idr': 1_311_140_722, 'amount': 0.00076261},
    'ETH/USDT': {'buy_price_idr':    37_447_016, 'amount': 0.05060638},
    'AVAX/USDT': {'buy_price_idr':     118_350, 'amount': 5.8661},
}

ASSET_LIST: List[str] = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'SUI/USDT',
    'XRP/USDT', 'LINK/USDT', 'AAVE/USDT', 'DOT/USDT', 'ONDO/USDT',
    'ARB/USDT', 'NEAR/USDT', 'ZEC/USDT', 'TAO/USDT', 'AVAX/USDT',
    'ADA/USDT'
]

# Mapping Simbol Spot ke Kucoin Futures (Lengkap 16 Koin)
FUTURES_MAP: Dict[str, str] = {
    'BTC/USDT': 'XBTUSDTM',  'ETH/USDT': 'ETHUSDTM',
    'SOL/USDT': 'SOLUSDTM',  'BNB/USDT': 'BNBUSDTM',
    'SUI/USDT': 'SUIUSDTM',  'XRP/USDT': 'XRPUSDTM',
    'LINK/USDT': 'LINKUSDTM', 'AAVE/USDT': 'AAVEUSDTM',
    'DOT/USDT':  'DOTUSDTM',  'ONDO/USDT': 'ONDOUSDTM',
    'ARB/USDT':  'ARBUSDTM',  'NEAR/USDT': 'NEARUSDTM',
    'ZEC/USDT':  'ZECUSDTM',  'TAO/USDT':  'TAOUSDTM',
    'AVAX/USDT': 'AVAXUSDTM', 'ADA/USDT':  'ADAUSDTM'
}

JAM_LAPORAN = {9, 14, 20}
BULLISH_SIGNAL_TYPES = {'BULL_SWEEP', 'BULL_OB', 'AKUMULASI', 'BULL_BREAKOUT'}

DESKRIPSI = {
    'BULL_SWEEP':     ("HARGA AKAN NAIK", "Bandar sapu SL ritel disertai FVG, siap loncat naik."),
    'BEAR_SWEEP':     ("HARGA AKAN TURUN", "Bandar jebak ritel beli, siap dump."),
    'BULL_OB':         ("ZONA BELI BANDAR", "Harga kembali ke area demand institusi + FVG."),
    'BEAR_OB':         ("ZONA JUAL BANDAR", "Harga menyentuh area supply institusi."),
    'AKUMULASI':       ("AKUMULASI WHALE", "Volume besar, spread sempit (Nampung barang)."),
    'DISTRIBUSI':      ("DISTRIBUSI WHALE", "Volume besar, spread sempit (Jualan barang)."),
    'BULL_BREAKOUT':  ("BREAKOUT VOLUME", "Modal besar jebol atap ke atas + FVG."),
    'BEAR_BREAKOUT':  ("BEAR_BREAKOUT", "Modal besar jebol lantai ke bawah."),
}

# ============================================================
# ASYNC API HELPERS
# ============================================================

async def get_usd_idr(client: httpx.AsyncClient) -> float:
    try:
        r = await client.get("https://indodax.com/api/ticker/usdtidr", timeout=5.0)
        return float(r.json()['ticker']['last'])
    except Exception as e:
        logger.warning(f"Gagal mengambil kurs USD/IDR ({e}), menggunakan fallback Rp 18,000")
        return 18000.0

async def get_fear_greed(client: httpx.AsyncClient) -> Dict[str, Any]:
    try:
        r = await client.get("https://api.alternative.me/fng/?limit=1", timeout=5.0)
        d = r.json()['data'][0]
        return {'value': int(d['value']), 'label': d['value_classification']}
    except Exception as e:
        logger.warning(f"Gagal mengambil Fear & Greed ({e}), menggunakan fallback Neutral")
        return {'value': 50, 'label': 'Neutral'}

async def get_funding_rate(exchange_futures: ccxt.Exchange, futures_symbol: str) -> Optional[float]:
    try:
        info = await exchange_futures.fetch_funding_rate(futures_symbol)
        return float(info.get('fundingRate', 0))
    except Exception as e:
        logger.warning(f"Funding Rate error [{futures_symbol}]: {e}")
        return None

def format_rp(nilai: float) -> str:
    return f"Rp {nilai:,.0f}"

# ============================================================
# KALKULASI INDIKATOR & SMC
# ============================================================

def hitung_atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-2])

def hitung_volume_delta(df: pd.DataFrame) -> pd.Series:
    hl = (df['high'] - df['low']).replace(0, 1e-9)
    return df['volume'] * ((df['close'] - df['open']) / hl)

def hitung_sudut(df: pd.DataFrame, period: int = 10) -> float:
    if len(df) < period: return 0.0
    y = df['close'].iloc[-period:].values
    x = np.arange(period)
    y_norm = (y - y[0]) / (y[0] + 1e-9) * 100
    slope, _ = np.polyfit(x, y_norm, 1)
    return float(math.degrees(math.atan(slope)))

def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> Dict[str, float]:
    swing_low = float(df_4h['low'].iloc[-window-1:-1].min())
    swing_high = float(df_4h['high'].iloc[-window-1:-1].max())
    return {'swing_high': swing_high, 'swing_low': swing_low}

def deteksi_order_block(df: pd.DataFrame) -> Dict[str, Optional[Dict[str, float]]]:
    result = {'bullish_ob': None, 'bearish_ob': None}
    avg_body = (df['close'] - df['open']).abs().rolling(10).median()
    for i in range(len(df) - 3, max(len(df) - 15, 0), -1):
        row = df.iloc[i]
        nxt = df.iloc[i + 1]
        body = abs(row['close'] - row['open'])
        if body < avg_body.iloc[i] * 1.2: continue

        if row['close'] < row['open'] and nxt['close'] > row['high']:
            if df.iloc[i+2:]['close'].min() >= row['low']:
                result['bullish_ob'] = {'high': float(row['high']), 'low': float(row['low'])}
                break

        if row['close'] > row['open'] and nxt['close'] < row['low']:
            if df.iloc[i+2:]['close'].max() <= row['high']:
                result['bearish_ob'] = {'high': float(row['high']), 'low': float(row['low'])}
                break
    return result

def deteksi_fvg(df: pd.DataFrame) -> Dict[str, Optional[Dict[str, float]]]:
    result = {'bullish_fvg': None, 'bearish_fvg': None}
    if len(df) < 5: return result
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    
    if c3['low'] > c1['high']:
        result['bullish_fvg'] = {'high': float(c3['low']), 'low': float(c1['high'])}
    elif c3['high'] < c1['low']:
        result['bearish_fvg'] = {'high': float(c1['low']), 'low': float(c3['high'])}
    return result

# ============================================================
# ANALISA UTAMA SCANNER
# ============================================================

def analisa(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    usd_idr: float,
    funding_rate: Optional[float],
    fear_greed: dict,
    is_weekend: bool,
) -> Optional[Dict[str, Any]]:

    if len(df_1h) < 50 or len(df_4h) < 20: return None

    # Tambahan: EMA & Fase 4H untuk Informan
    df_4h['ema9'] = df_4h['close'].ewm(span=9, adjust=False).mean()
    df_4h['ema21'] = df_4h['close'].ewm(span=21, adjust=False).mean()
    df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
    
    c_4h, e9, e21 = df_4h['close'].iloc[-1], df_4h['ema9'].iloc[-1], df_4h['ema21'].iloc[-1]
    trend_4h_bull = c_4h > df_4h['ema50'].iloc[-1]

    if c_4h > e9 and e9 > e21: fase_4h = "NAIK KOKOH 🟢"
    elif c_4h > e9 and e9 <= e21: fase_4h = "REBOUND ↗️"
    elif c_4h < e9 and e9 < e21: fase_4h = "TURUN 🔴"
    elif c_4h < e9 and e9 >= e21: fase_4h = "KOREKSI ↘️"
    else: fase_4h = "SIDEWAYS ⚪"

    # Tambahan: RSI 4H untuk Informan
    delta_4h = df_4h['close'].diff()
    up, down = delta_4h.clip(lower=0), -1 * delta_4h.clip(upper=0)
    rs = (up.ewm(com=13, adjust=False).mean()) / (down.ewm(com=13, adjust=False).mean())
    rsi_4h = float((100 - (100 / (1 + rs))).iloc[-1])

    c, p = df_1h.iloc[-2], df_1h.iloc[-3]
    latest_c = df_1h.iloc[-1]
    harga_idr = latest_c['close'] * usd_idr
    atr_idr = hitung_atr(df_1h) * usd_idr
    sudut_tren = hitung_sudut(df_1h)

    avg_vol = df_1h['volume'].iloc[-21:-1].median()
    avg_range = (df_1h['high'] - df_1h['low']).iloc[-21:-1].median()

    candle_range = c['high'] - c['low']
    body_size = abs(c['close'] - c['open'])
    upper_wick = c['high'] - max(c['close'], c['open'])
    lower_wick = min(c['close'], c['open']) - c['low']
    vol_ratio = round(c['volume'] / (avg_vol if avg_vol > 0 else 1), 2)

    vol_spike = c['volume'] > avg_vol * 1.5
    vol_ultra = c['volume'] > avg_vol * 2.5

    deltas = hitung_volume_delta(df_1h)
    cvd_delta = round(deltas.iloc[-2], 4)
    cvd_naik = cvd_delta > 0

    ob = deteksi_order_block(df_1h)
    fvg = deteksi_fvg(df_1h)
    
    dekat_bull_ob = dekat_bear_ob = False
    if ob['bullish_ob']:
        ob_mid = ((ob['bullish_ob']['high'] + ob['bullish_ob']['low']) / 2) * usd_idr
        dekat_bull_ob = abs(harga_idr - ob_mid) / ob_mid < 0.008

    if ob['bearish_ob']:
        ob_mid = ((ob['bearish_ob']['high'] + ob['bearish_ob']['low']) / 2) * usd_idr
        dekat_bear_ob = abs(harga_idr - ob_mid) / ob_mid < 0.008

    ada_bullish_fvg = fvg['bullish_fvg'] is not None
    ada_bearish_fvg = fvg['bearish_fvg'] is not None

    bull_sweep = (lower_wick > candle_range * 0.35) and vol_spike and (c['close'] >= p['low']) and ada_bullish_fvg
    bear_sweep = (upper_wick > candle_range * 0.35) and vol_spike and (c['close'] <= p['high']) and ada_bearish_fvg
    is_absorption = vol_spike and (body_size < avg_range * 0.5)

    bull_breakout = (c['close'] > c['open']) and (body_size > avg_range * 1.0) and vol_spike and ada_bullish_fvg
    bear_breakout = (c['close'] < c['open']) and (body_size > avg_range * 1.0) and vol_spike

    swing_4h = deteksi_swing_4h(df_4h, window=7)
    swing_high_idr = swing_4h['swing_high'] * usd_idr
    swing_low_idr = swing_4h['swing_low'] * usd_idr

    sl_buy = swing_low_idr - (0.5 * atr_idr)
    tp_buy = swing_high_idr if swing_high_idr > harga_idr * 1.015 else harga_idr + (3.5 * atr_idr)
    if sl_buy >= harga_idr * 0.985: sl_buy = harga_idr - (1.8 * atr_idr)

    sl_sell = swing_high_idr + (0.5 * atr_idr)
    tp_sell = swing_low_idr if swing_low_idr < harga_idr * 0.985 else harga_idr - (3.5 * atr_idr)
    if sl_sell <= harga_idr * 1.015: sl_sell = harga_idr + (1.8 * atr_idr)

    risk_buy = max(harga_idr - sl_buy, 1.0)
    reward_buy = max(tp_buy - harga_idr, 0.0)
    rrr_buy = round(reward_buy / risk_buy, 2)
    profit_pct = round((reward_buy / harga_idr) * 100, 2)

    skor_dasar = 50.0
    if trend_4h_bull: skor_dasar += 15.0
    if vol_ultra: skor_dasar += 15.0
    elif vol_spike: skor_dasar += 10.0
    if cvd_naik: skor_dasar += 10.0
    if ada_bullish_fvg: skor_dasar += 10.0
    if funding_rate is not None and funding_rate <= 0.0005: skor_dasar += 5.0
    skor_final = min(round(skor_dasar, 1), 100.0)

    base = {
        'harga': harga_idr, 'vol_ratio': vol_ratio,
        'cvd_delta': cvd_delta, 'cvd_naik': cvd_naik,
        'trend_4h': 'BULLISH' if trend_4h_bull else 'BEARISH',
        'fase_4h': fase_4h, 'rsi_4h': rsi_4h,
        'funding_rate': funding_rate, 'fear_greed': fear_greed,
        'is_weekend': is_weekend, 'vol_ultra': vol_ultra,
        'sl_buy': sl_buy, 'tp_buy': tp_buy,
        'sl_sell': sl_sell, 'tp_sell': tp_sell,
        'rrr': rrr_buy, 'profit_pct': profit_pct,
        'high_price': latest_c['high'] * usd_idr, 
        'low_price': latest_c['low'] * usd_idr,
        'skor': skor_final, 'sudut': sudut_tren,
    }

    strength = '🔥🔥🔥' if vol_ultra else '🔥🔥'

    if bull_sweep: return {**base, 'tipe': 'BULL_SWEEP', 'aksi': '🟢 BELI / ENTRY DISKON (Anti-Fake Sweep + FVG)', 'strength': strength}
    if bear_sweep: return {**base, 'tipe': 'BEAR_SWEEP', 'aksi': '🔴 JUAL / TAKE PROFIT (Awas Trap)', 'strength': strength}
    if dekat_bull_ob and vol_spike and ada_bullish_fvg: return {**base, 'tipe': 'BULL_OB', 'aksi': '🟢 BELI (Antri Limit di Demand + FVG)', 'strength': '🔥🔥'}
    if dekat_bear_ob and vol_spike: return {**base, 'tipe': 'BEAR_OB', 'aksi': '⏳ WAIT & SEE / CASH OUT', 'strength': '🔥🔥'}
    if is_absorption:
        aksi = '🟢 CICIL BELI (DCA Santai)' if c['close'] >= c['open'] else '🔴 AMANKAN CASH / SELL'
        tipe = 'AKUMULASI' if c['close'] >= c['open'] else 'DISTRIBUSI'
        return {**base, 'tipe': tipe, 'aksi': aksi, 'strength': '🔥'}
    if bull_breakout: return {**base, 'tipe': 'BULL_BREAKOUT', 'aksi': '🟢 FOLLOW TREND (Breakout + FVG)', 'strength': strength}
    if bear_breakout: return {**base, 'tipe': 'BEAR_BREAKOUT', 'aksi': '⏳ TUNGGU DI BAWAH (Wait Drop)', 'strength': strength}

    return None

# ============================================================
# TELEGRAM FORMATTERS
# ============================================================

def format_pesan(symbol: str, s: dict, is_porto_alert: bool = False) -> str:
    tipe, harga = s['tipe'], format_rp(s['harga'])
    judul, ket = DESKRIPSI.get(tipe, (tipe, ""))
    
    fr = s['funding_rate']
    if fr is None: fr_str = "N/A"
    elif fr > 0.0005: fr_str = f"+{fr*100:.4f}% (Rawan Dump)"
    elif fr < -0.0005: fr_str = f"{fr*100:.4f}% (Squeeze)"
    else: fr_str = f"{fr*100:+.4f}% (Normal)"

    is_bullish = tipe in BULLISH_SIGNAL_TYPES
    if is_bullish:
        rm_label_1, rm_val_1 = "TP (Target)", format_rp(s['tp_buy'])
        rm_label_2, rm_val_2 = "SL (Batas) ", format_rp(s['sl_buy'])
    else:
        rm_label_1, rm_val_1 = "Serok Bawah", format_rp(s['tp_sell'])
        rm_label_2, rm_val_2 = "Invalidasi ", format_rp(s['sl_sell'])

    header = f"🚨 <b>WARNING PORTOFOLIO — {symbol}</b>" if is_porto_alert else f"⚡ <b>QUANT SIGNAL — {symbol}</b>"
    vol_ultra_str = " (ULTRA)" if s.get('vol_ultra') else ""

    return (
        f"{header} {s['strength']}\n"
        f"<code>"
        f"[ 1. SIGNAL DETECTION ]\n"
        f"  • Trigger : {judul}\n"
        f"  • Tren 4H : {s['trend_4h']} ({s['fase_4h']})\n"
        f"  • Skor    : {s.get('skor', 0.0)} / 100\n"
        f"  • Sudut   : {s.get('sudut', 0.0):+.2f}°\n"
        f"  • RSI 4H  : {s.get('rsi_4h', 0.0):.1f}\n"
        f"  • Harga   : {harga}\n"
        f"------------------------------\n"
        f"[ 2. MARKET METRICS ]\n"
        f"  • Volume  : {s['vol_ratio']}x median{vol_ultra_str}\n"
        f"  • Delta   : {'Beli Dominan' if s['cvd_naik'] else 'Jual Dominan'}\n"
        f"  • Funding : {fr_str}\n"
        f"  • Market  : {s['fear_greed']['value']} ({s['fear_greed']['label']})\n"
        f"------------------------------\n"
        f"[ 3. RISK MANAGEMENT ]\n"
        f"  • {rm_label_1} : {rm_val_1}\n"
        f"  • {rm_label_2} : {rm_val_2}\n"
        f"  • RRR Ratio : {s.get('rrr', 0.0):.2f}x\n"
        f"  • Target %  : {s.get('profit_pct', 0.0):.2f}%\n"
        f"</code>\n"
        f"🎯 <b>ACTION PLAN :</b> {s['aksi']}\n"
        f"💡 <b>Insight    :</b> {ket}"
    )

def format_informan_radar(kumpulan_semua: List[dict], best_symbol: str) -> str:
    """Format rekap koin-koin runner-up yang terdeteksi bullish untuk dikirim ke user"""
    radar = [s for s in kumpulan_semua if s['symbol'] != best_symbol and s['tipe'] in BULLISH_SIGNAL_TYPES and s.get('skor', 0) >= 60]
    
    if not radar: return ""
        
    radar.sort(key=lambda x: x.get('skor', 0), reverse=True)
    
    lines = ["🕵️‍♂️ <b>RADAR INFORMAN PRIBADI</b>", "<i>Koin potensial lain yang terpantau:</i>\n"]
    for s in radar[:5]: # Tampilkan max 5 koin terbaik lainnya
        lines.append(f"🔹 <b>{s['symbol']}</b> | Skor: {s['skor']}")
        lines.append(f"  ├ Fase  : {s['fase_4h']} | RSI: {s.get('rsi_4h', 0):.1f}")
        lines.append(f"  ├ Setup : {s['tipe'].replace('_', ' ')}")
        if not s.get('is_tradeable'):
            lines.append(f"  └ ⚠️ <i>Skip Bot Trading (RRR {s['rrr']}x / Profit {s.get('profit_pct',0):.1f}%)</i>")
        else:
            lines.append(f"  └ ✅ <i>Lolos Syarat Bot, kalah peringkat utama.</i>")
        lines.append("") # Spasi antar koin
    
    return "\n".join(lines)

# ============================================================
# LAPORAN PORTOFOLIO
# ============================================================

async def kirim_laporan(bot: Bot, exchange: ccxt.Exchange, usd_idr: float, fear_greed: dict):
    total_modal = total_nilai = 0.0
    baris = []

    try:
        tickers = await exchange.fetch_tickers(list(PORTFOLIO.keys()))
    except Exception:
        tickers = {}

    for sym, p in PORTFOLIO.items():
        try:
            last_price = tickers[sym]['last'] if sym in tickers else (await exchange.fetch_ticker(sym))['last']
            harga_kini = last_price * usd_idr
            modal = p['buy_price_idr'] * p['amount']
            nilai = harga_kini * p['amount']
            pnl_val = nilai - modal
            pnl_pct = (pnl_val / modal) * 100
            ikon = "🟢" if pnl_pct >= 0 else "🔴"

            total_modal += modal
            total_nilai += nilai

            baris.append(
                f"{ikon} <b>{sym}</b>\n<code>"
                f"Beli : {format_rp(p['buy_price_idr'])}\n"
                f"Skrg : {format_rp(harga_kini)}\n"
                f"P/L  : {pnl_pct:+.2f}% ({format_rp(pnl_val)})\n</code>"
            )
        except Exception as e:
            baris.append(f"⚠️ {sym} — gagal ({e})")

    total_pnl = total_nilai - total_modal
    total_pnl_pct = (total_pnl / total_modal * 100) if total_modal else 0
    ikon_total = "🟢" if total_pnl_pct >= 0 else "🔴"
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)

    fng_translation = {
        "Extreme Fear": "Ketakutan Ekstrem", "Fear": "Takut",
        "Neutral": "Netral", "Greed": "Serakah", "Extreme Greed": "Keserakahan Ekstrem"
    }
    behavior_map = {
        "Extreme Fear": "Ritel Panik JUAL, Bandar BELI",
        "Fear": "Ritel cicil JUAL, Bandar cicil BELI",
        "Neutral": "Ritel WAIT & SEE, Bandar Konsolidasi",
        "Greed": "Ritel cicil BELI, Bandar cicil JUAL",
        "Extreme Greed": "Ritel FOMO BELI, Bandar JUAL (TP)"
    }

    label_indo = fng_translation.get(fear_greed['label'], fear_greed['label'])
    analisis_pasar = behavior_map.get(fear_greed['label'], "Ritel & Bandar Bergerak Dinamis")

    pesan = (
        f"📊 <b>PORTOFOLIO — {now_wib.strftime('%d %b %Y, %H:%M WIB')}</b>\n\n"
        + "\n".join(baris)
        + f"\n────────────────────\n"
        + f"{ikon_total} <b>SUMMARY</b>\n<code>"
        + f"Total Beli : {format_rp(total_modal)}\n"
        + f"Total Skrg : {format_rp(total_nilai)}\n"
        + f"Total P/L  : {total_pnl_pct:+.2f}% ({format_rp(total_pnl)})\n"
        + f"------------------------------\n"
        + f"Pasar      : {fear_greed['value']} — {label_indo}\n"
        + f"Aksi       : {analisis_pasar}\n</code>"
    )
    await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='HTML')

# ============================================================
# WORKER SCANNER PER ASSET
# ============================================================
async def scan_asset(
    symbol: str, exchange: ccxt.Exchange, exchange_futures: ccxt.Exchange,
    usd_idr: float, fear_greed: dict, is_weekend: bool, bot: Bot, semaphore: asyncio.Semaphore
) -> Optional[Dict[str, Any]]:

    async with semaphore:
        try:
            bars_1h_task = exchange.fetch_ohlcv(symbol, '1h', limit=60)
            bars_4h_task = exchange.fetch_ohlcv(symbol, '4h', limit=60)
            bars_1h, bars_4h = await asyncio.gather(bars_1h_task, bars_4h_task)

            df_1h = pd.DataFrame(bars_1h, columns=['timestamp','open','high','low','close','volume']).astype(float)
            df_4h = pd.DataFrame(bars_4h, columns=['timestamp','open','high','low','close','volume']).astype(float)

            futures_sym = FUTURES_MAP.get(symbol)
            funding_rate = await get_funding_rate(exchange_futures, futures_sym) if futures_sym else None

            hasil = analisa(df_1h, df_4h, usd_idr, funding_rate, fear_greed, is_weekend)

            if not hasil: return None
            hasil['symbol'] = symbol

            is_bullish = hasil['tipe'] in BULLISH_SIGNAL_TYPES
            is_porto = symbol in PORTFOLIO

            if is_bullish:
                # Cek kelayakan untuk Bot Trading (Otomatis)
                hasil['is_tradeable'] = True
                if hasil['rrr'] < MIN_RRR_THRESHOLD or hasil.get('profit_pct', 0.0) < MIN_PROFIT_PCT_THRESHOLD:
                    hasil['is_tradeable'] = False
                    logger.info(f"👀 {symbol}: Masuk Radar Informan, tapi Skip Bot Trade (RRR/Profit kurang).")
                
                # Kita TETAP return hasil (berbeda dengan kode lama) agar masuk ke Radar Informan
                return hasil

            elif is_porto:
                # Warning portofolio jika ada sinyal Jual/Bearish
                logger.warning(f"🚨 WARNING PORTOFOLIO: {symbol} terdeteksi sinyal JUAL/DUMP ({hasil['tipe']})!")
                pesan_warning = format_pesan(symbol, hasil, is_porto_alert=True)
                await bot.send_message(chat_id=CHAT_ID, text=pesan_warning, parse_mode='HTML')
                return None
            else:
                return None

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")
            return None

# ============================================================
# MAIN EXECUTOR
# ============================================================
async def main():
    if not TOKEN or not CHAT_ID: return

    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30_000})
    exchange_futures = ccxt.kucoinfutures({'enableRateLimit': True, 'timeout': 30_000})
    bot = Bot(token=TOKEN)

    try:
        await exchange.load_markets()
        async with httpx.AsyncClient() as client:
            usd_idr_task = get_usd_idr(client)
            fear_greed_task = get_fear_greed(client)
            usd_idr, fear_greed = await asyncio.gather(usd_idr_task, fear_greed_task)

        now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
        is_weekend = now_wib.weekday() in [5, 6]

        semaphore = asyncio.Semaphore(5)
        tasks = [scan_asset(s, exchange, exchange_futures, usd_idr, fear_greed, is_weekend, bot, semaphore) for s in ASSET_LIST]
        
        hasil_scan = await asyncio.gather(*tasks)
        semua_sinyal_bullish = [s for s in hasil_scan if s is not None]

        # 1. PISAHKAN UNTUK BOT TRADING (Sinyal Utama)
        trade_candidates = [s for s in semua_sinyal_bullish if s.get('is_tradeable', False)]
        best_signal = None
        
        if trade_candidates:
            trade_candidates.sort(key=lambda x: (abs(x.get('sudut', 0.0)), x.get('skor', 0.0), x.get('rrr', 0.0)), reverse=True)
            best_signal = trade_candidates[0]

            logger.info(f"📢 Mengirim Sinyal Utama: {best_signal['symbol']} ...")
            pesan = format_pesan(best_signal['symbol'], best_signal)
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='HTML')
        else:
            logger.info("— Tidak ada sinyal BELI yang lolos filter Bot Trading siklus ini.")

        # 2. EKSEKUSI RADAR INFORMAN (Laporan Koin Potensial Lainnya)
        best_sym = best_signal['symbol'] if best_signal else ""
        radar_msg = format_informan_radar(semua_sinyal_bullish, best_sym)
        if radar_msg:
            await bot.send_message(chat_id=CHAT_ID, text=radar_msg, parse_mode='HTML')
            logger.info("📢 Radar Informan Pribadi berhasil dikirim.")

        # 3. EXPORT KE PAPER TRADER
        signal_export = {
            "timestamp": now_wib.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": best_signal['symbol'] if best_signal else "NONE",
            "signal_type": best_signal['tipe'] if best_signal else None,
            "score": best_signal.get('skor', 0.0) if best_signal else 0.0,
            "angle": best_signal.get('sudut', 0.0) if best_signal else 0.0,
            "current_price": best_signal['harga'] if best_signal else 0.0,
            "high_price": best_signal['high_price'] if best_signal else 0.0,
            "low_price": best_signal['low_price'] if best_signal else 0.0,
            "sl_price": best_signal['sl_buy'] if best_signal else 0.0,
            "tp_price": best_signal['tp_buy'] if best_signal else 0.0,
            "rrr": best_signal.get('rrr', 0.0) if best_signal else 0.0,
            "profit_pct": best_signal.get('profit_pct', 0.0) if best_signal else 0.0
        }

        with open("signal_main.json", "w") as f:
            json.dump(signal_export, f, indent=4)

        # 4. LAPORAN PORTOFOLIO REGULER
        if now_wib.hour in JAM_LAPORAN and now_wib.minute < 30:
            await kirim_laporan(bot, exchange, usd_idr, fear_greed)

    except Exception as e:
        logger.error(f"Error utama pada executor main(): {e}")
    finally:
        try: await exchange.close()
        except: pass
        try: await exchange_futures.close()
        except: pass

if __name__ == '__main__':
    asyncio.run(main())
