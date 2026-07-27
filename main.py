"""
========================================================
   KRIPTO BOT — Smart Money Edition (Main Scanner)
   Versi: 3.4 (Swing 4H Risk Management & SMC Engine)
   SPOT MARKET
========================================================

   Sumber Data:
     - KuCoin (OHLCV Spot & Volume)   via ccxt
     - Fear & Greed Index             via alternative.me (GRATIS)
     - Funding Rate                   via KuCoin Futures (GRATIS)
     - Kurs USD/IDR                   via exchangerate-api.com (GRATIS)
  
   Konsep Utama:
     - Liquidity Sweep  → Whale nyapu stop loss ritel (1H Trigger)
     - Volume Absorption→ Akumulasi/distribusi diam-diam
     - Order Block      → Zona order besar (dengan filter mitigasi)
     - Swing Structure  → Batas SL/TP presisi berbasis Swing Low/High (4H)
     - CVD (Delta Vol)  → Arah akumulasi/distribusi dari rasio body
     - Funding Rate     → Sentimen pasar futures
     - Fear & Greed     → Sentimen makro pasar kripto
========================================================
"""

import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta, timezone

# ============================================================
# KONFIGURASI
# ============================================================
TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1_311_140_722, 'amount': 0.00076261},
    'ETH/USDT': {'buy_price_idr':    37_447_016, 'amount': 0.05060638},
}

ASSET_LIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'SUI/USDT',
    'XRP/USDT', 'LINK/USDT', 'AAVE/USDT', 'DOT/USDT', 'ONDO/USDT',
    'ARB/USDT', 'NEAR/USDT', 'ZEC/USDT', 'TAO/USDT', 'AVAX/USDT'
]

FUTURES_MAP = {
    'BTC/USDT': 'XBTUSDTM',
    'ETH/USDT': 'ETHUSDTM',
    'SOL/USDT': 'SOLUSDTM',
    'BNB/USDT': 'BNBUSDTM',
    'XRP/USDT': 'XRPUSDTM',
    'LINK/USDT': 'LINKUSDTM',
    'AAVE/USDT': 'AAVEUSDTM',
    'DOT/USDT':  'DOTUSDTM',
}

JAM_LAPORAN = {9, 14, 20}

# ============================================================
# API HELPERS
# ============================================================

def get_usd_idr() -> float:
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return float(r.json()['rates']['IDR'])
    except Exception:
        return 16_400.0

def get_fear_greed() -> dict:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()['data'][0]
        return {'value': int(d['value']), 'label': d['value_classification']}
    except Exception:
        return {'value': 50, 'label': 'Neutral'}

def get_funding_rate(exchange_futures, futures_symbol: str) -> float | None:
    try:
        info = exchange_futures.fetch_funding_rate(futures_symbol, params={'timeout': 5000})
        return float(info.get('fundingRate', 0))
    except ccxt.RequestTimeout:
        print(f"  ⚠️ Timeout API Funding Rate untuk {futures_symbol}")
        return None
    except Exception as e:
        print(f"  ⚠️ Error API Funding Rate {futures_symbol}: {e}")
        return None

def format_rp(nilai: float) -> str:
    return f"Rp {nilai:,.0f}"

# ============================================================
# KALKULASI INDIKATOR & STRUKTUR PASAR
# ============================================================

def hitung_atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def hitung_volume_delta(df: pd.DataFrame) -> pd.Series:
    hl = (df['high'] - df['low']).replace(0, 1e-9)
    delta = df['volume'] * ((df['close'] - df['open']) / hl)
    return delta

def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> dict:
    """Mencari area Swing Low dan Swing High dari struktur 4H."""
    swing_low = float(df_4h['low'].iloc[-window-1:-1].min())
    swing_high = float(df_4h['high'].iloc[-window-1:-1].max())
    return {'swing_high': swing_high, 'swing_low': swing_low}

def deteksi_order_block(df: pd.DataFrame) -> dict:
    result = {'bullish_ob': None, 'bearish_ob': None}
    avg_body = (df['close'] - df['open']).abs().rolling(10).median()

    for i in range(len(df) - 3, len(df) - 15, -1):
        row   = df.iloc[i]
        nxt   = df.iloc[i + 1]
        body  = abs(row['close'] - row['open'])

        if body < avg_body.iloc[i] * 1.2:
            continue

        if row['close'] < row['open'] and nxt['close'] > row['high']:
            if df.iloc[i+2:]['close'].min() >= row['low']:
                result['bullish_ob'] = {'high': float(row['high']), 'low': float(row['low'])}
                break

        if row['close'] > row['open'] and nxt['close'] < row['low']:
            if df.iloc[i+2:]['close'].max() <= row['high']:
                result['bearish_ob'] = {'high': float(row['high']), 'low': float(row['low'])}
                break

    return result

# ============================================================
# ANALISA UTAMA (SMC SCANNER)
# ============================================================

def analisa(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    usd_idr: float,
    funding_rate: float | None,
    fear_greed: dict,
    is_weekend: bool,
) -> dict | None:

    if len(df_1h) < 50 or len(df_4h) < 20:
        return None

    # Trend Macro 4H
    df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
    trend_4h_bull  = df_4h['close'].iloc[-1] > df_4h['ema50'].iloc[-1]

    c = df_1h.iloc[-2]  # Candle closed terakhir
    p = df_1h.iloc[-3]  # Candle sebelumnya

    harga_idr = c['close'] * usd_idr
    atr_idr   = hitung_atr(df_1h) * usd_idr

    avg_vol   = df_1h['volume'].iloc[-21:-1].median()
    avg_range = (df_1h['high'] - df_1h['low']).iloc[-21:-1].median()

    candle_range = c['high']  - c['low']
    body_size    = abs(c['close'] - c['open'])
    upper_wick   = c['high']  - max(c['close'], c['open'])
    lower_wick   = min(c['close'], c['open']) - c['low']
    vol_ratio    = round(c['volume'] / avg_vol, 2)

    vol_spike = c['volume'] > avg_vol * 1.5
    vol_ultra = c['volume'] > avg_vol * 2.5

    deltas    = hitung_volume_delta(df_1h)
    cvd_delta = round(deltas.iloc[-2], 4)
    cvd_naik  = cvd_delta > 0
    cvd_turun = cvd_delta < 0

    ob = deteksi_order_block(df_1h)

    dekat_bull_ob = False
    dekat_bear_ob = False
    
    if ob['bullish_ob']:
        ob['bullish_ob']['high_idr'] = ob['bullish_ob']['high'] * usd_idr
        ob['bullish_ob']['low_idr']  = ob['bullish_ob']['low'] * usd_idr
        ob_mid = (ob['bullish_ob']['high_idr'] + ob['bullish_ob']['low_idr']) / 2
        dekat_bull_ob = abs(harga_idr - ob_mid) / ob_mid < 0.008

    if ob['bearish_ob']:
        ob['bearish_ob']['high_idr'] = ob['bearish_ob']['high'] * usd_idr
        ob['bearish_ob']['low_idr']  = ob['bearish_ob']['low'] * usd_idr
        ob_mid = (ob['bearish_ob']['high_idr'] + ob['bearish_ob']['low_idr']) / 2
        dekat_bear_ob = abs(harga_idr - ob_mid) / ob_mid < 0.008

    bull_sweep = (lower_wick > candle_range * 0.35) and vol_spike and (c['close'] >= p['low'])
    bear_sweep = (upper_wick > candle_range * 0.35) and vol_spike and (c['close'] <= p['high'])

    is_absorption = vol_spike and (body_size < avg_range * 0.5)

    bull_breakout = (c['close'] > c['open']) and (body_size > avg_range * 1.0) and vol_spike
    bear_breakout = (c['close'] < c['open']) and (body_size > avg_range * 1.0) and vol_spike

    # HITUNG SL & TP DINAMIS BERDASARKAN SWING 4H
    swing_4h = deteksi_swing_4h(df_4h, window=7)
    swing_high_idr = swing_4h['swing_high'] * usd_idr
    swing_low_idr  = swing_4h['swing_low'] * usd_idr

    # Skenario Buy (Bullish)
    sl_buy = swing_low_idr - (0.5 * atr_idr)
    tp_buy = swing_high_idr
    if tp_buy <= harga_idr * 1.015:
        tp_buy = harga_idr + (3.5 * atr_idr)
    if sl_buy >= harga_idr * 0.985:
        sl_buy = harga_idr - (1.8 * atr_idr)

    # Skenario Sell (Bearish)
    sl_sell = swing_high_idr + (0.5 * atr_idr)
    tp_sell = swing_low_idr
    if tp_sell >= harga_idr * 0.985:
        tp_sell = harga_idr - (3.5 * atr_idr)
    if sl_sell <= harga_idr * 1.015:
        sl_sell = harga_idr + (1.8 * atr_idr)

    base = {
        'harga': harga_idr, 'vol_ratio': vol_ratio,
        'cvd_delta': cvd_delta, 'cvd_naik': cvd_naik, 'cvd_turun': cvd_turun,
        'trend_4h': 'BULLISH' if trend_4h_bull else 'BEARISH',
        'funding_rate': funding_rate, 'fear_greed': fear_greed,
        'is_weekend': is_weekend, 'vol_ultra': vol_ultra,
        'dekat_bull_ob': dekat_bull_ob, 'dekat_bear_ob': dekat_bear_ob,
        'ob_bull_zone': ob['bullish_ob'], 'ob_bear_zone': ob['bearish_ob'],
        'sl_buy': sl_buy, 'tp_buy': tp_buy,
        'sl_sell': sl_sell, 'tp_sell': tp_sell,
    }

    # 1. SWEEP (Reversal Diskon - Paling Valid untuk Entry Spot SMC) 
    if bull_sweep:
        return {**base, 'tipe': 'BULL_SWEEP', 'aksi': '🟢 GAS BELI (ENTRY DISKON)', 'strength': '🔥🔥🔥' if vol_ultra else '🔥🔥'}
    
    if bear_sweep:
        return {**base, 'tipe': 'BEAR_SWEEP', 'aksi': '🔴 GAS EXIT / TAKE PROFIT', 'strength': '🔥🔥🔥' if vol_ultra else '🔥🔥'}

    # 2. ORDER BLOCK (Zona Demand / Supply)
    if dekat_bull_ob and vol_spike:
        return {**base, 'tipe': 'BULL_OB', 'aksi': '🟢 ZONA BELI BANDAR', 'strength': '🔥🔥'}
    
    if dekat_bear_ob and vol_spike:
        return {**base, 'tipe': 'BEAR_OB', 'aksi': '🔴 ZONA JUAL BANDAR', 'strength': '🔥🔥'}

    # 3. ABSORPTION (Akumulasi / Distribusi Volume Meledak Spread Sempit)
    if is_absorption:
        if c['close'] >= c['open']:
            return {**base, 'tipe': 'AKUMULASI', 'aksi': '🟡 CICIL BELI (DCA)', 'strength': '🔥'}
        else:
            return {**base, 'tipe': 'DISTRIBUSI', 'aksi': '⚠️ HINDARI / AMBIL UNTUNG', 'strength': '🔥'}

    # 4. BREAKOUT (Jangan Langsung Hantam Beli - Tunggu Retest!)
    if bull_breakout:
        return {**base, 'tipe': 'BULL_BREAKOUT', 'aksi': '⏳ PANTAU (WAIT RETEST)', 'strength': '🔥🔥🔥' if vol_ultra else '🔥🔥'}
    
    if bear_breakout:
        return {**base, 'tipe': 'BEAR_BREAKOUT', 'aksi': '🚨 BAHAYA (POTENSI DUMP)', 'strength': '🔥🔥🔥' if vol_ultra else '🔥🔥'}

    return None

# ============================================================
# FORMAT PESAN TELEGRAM
# ============================================================

DESKRIPSI = {
    'BULL_SWEEP':    ("🐋 HARGA AKAN NAIK", "Bandar sapu SL ritel, Harga akan LONCAT NAIK."),
    'BEAR_SWEEP':    ("🚨 HARGA AKAN TURUN", "Bandar jebak ritel beli, Harga akan TURUN DRASTIS."),
    'BULL_OB':       ("📦 ZONA BELI BANDAR", "Harga kembali ke zona beli bandar sebelumnya."),
    'BEAR_OB':       ("📦 ZONA JUAL BANDAR", "Harga menyentuh zona jual bandar sebelumnya."),
    'AKUMULASI':     ("🤫 BANDAR BELI DIAM-DIAM", "Volume meledak, spread sempit. BANDAR BELI barang pelan-pelan."),
    'DISTRIBUSI':    ("⚠️ BANDAR JUAL DIAM-DIAM", "Volume meledak, spread sempit. BANDAR JUAL barang pelan-pelan."),
    'BULL_BREAKOUT': ("🚀 BREAKOUT VOLUME", "Modal besar JEBOL ATAP ke atas."),
    'BEAR_BREAKOUT': ("💥 BREAKDOWN VOLUME", "Modal besar JEBOL LANTAI ke bawah."),
}

AKSI_MAP = {
    'BULL_SWEEP':    "🟢 BELI / ENTRY SPOT (Bandar Sapu Bawah)",
    'BEAR_SWEEP':    "🔴 JUAL / TAKE PROFIT (Awas Bull Trap)",
    'BULL_OB':       "🟢 BELI (Antri Limit Order di Zona Demand)",
    'BEAR_OB':       "⏳ WAIT & SEE / JUAL (Lagi di Zona Supply)",
    'AKUMULASI':     "🟢 CICIL BELI (Bandar Lagi Nampung)",
    'DISTRIBUSI':    "🔴 JUAL / CASH OUT (Bandar Lagi Jualan)",
    'BULL_BREAKOUT': "🟢 BELI / FOLLOW TREND (Breakout Volume)",
    'BEAR_BREAKOUT': "⏳ TUNGGU DI BAWAH (Breakdown Volume)",
}

def format_pesan(symbol: str, s: dict) -> str:
    tipe, harga = s['tipe'], format_rp(s['harga'])
    judul, ket = DESKRIPSI.get(tipe, (tipe, ""))
    aksi_saran = AKSI_MAP.get(tipe, f"⚡ {s['aksi']}")
    cvd_arah = "↑ positif (dominan beli)" if s['cvd_naik'] else "↓ negatif (dominan jual)"

    fr = s['funding_rate']
    fr_str = "N/A"
    if fr is not None:
        if fr > 0.0005: fr_str = f"+{fr*100:.4f}% ⚠️ (rawan dump)"
        elif fr < -0.0005: fr_str = f"{fr*100:.4f}% ⚡ (potensi squeeze)"
        else: fr_str = f"{fr*100:+.4f}% (normal)"

    ob_info = ""
    if s.get('dekat_bull_ob') and s.get('ob_bull_zone'):
        z = s['ob_bull_zone']
        ob_info = f"\n*Area Order Block Bullish:*\n  {format_rp(z['low_idr'])} — {format_rp(z['high_idr'])}\n"
    elif s.get('dekat_bear_ob') and s.get('ob_bear_zone'):
        z = s['ob_bear_zone']
        ob_info = f"\n*Area Order Block Bearish:*\n  {format_rp(z['low_idr'])} — {format_rp(z['high_idr'])}\n"

    # ============================================================
    # PERUBAHAN LOGIKA KHUSUS SPOT (Pemisahan Setup Beli vs Jual)
    # ============================================================
    is_bullish = tipe in ['BULL_SWEEP', 'BULL_OB', 'AKUMULASI', 'BULL_BREAKOUT']

    if is_bullish:
        rm_text = (
            f"*Saran Manajemen Risiko (Spot):*\n"
            f"  • SL (Batas Rugi) : {format_rp(s['sl_buy'])}\n"
            f"  • TP (Ambil Cuan) : {format_rp(s['tp_buy'])}"
        )
    else:
        rm_text = (
            f"*Saran Eksekusi Spot (Amankan Cash):*\n"
            f"  • Target Serok Ulang : {format_rp(s['tp_sell'])}\n"
            f"  • Batal Turun (Invalidasi) : {format_rp(s['sl_sell'])}"
        )

    pesan = (
        f"*{judul}*\n"
        f"*{symbol}* {s['strength']}\n"
        f"\n"
        f"Harga   : {harga}\n"
        f"Tren 4H : *{s['trend_4h']}*\n"
        f"\n"
        f"👉 *Langkah : {aksi_saran}*\n"
        f"\n"
        f"*Jejak Whale:*\n"
        f"  • {ket}\n"
        f"  • Volume : *{s['vol_ratio']}x* median" + (" ⚡ ULTRA!" if s.get('vol_ultra') else "") + "\n"
        f"  • Delta  : {cvd_arah}\n"
        + ob_info +
        f"\n"
        f"*Sentimen Market:*\n"
        f"  • Fear/Greed : {s['fear_greed']['value']} ({s['fear_greed']['label']})\n"
        f"  • Funding    : {fr_str}\n"
        f"\n"
        f"{rm_text}"
    )
    return pesan

# ============================================================
# LAPORAN PORTOFOLIO
# ============================================================

async def kirim_laporan(bot: Bot, exchange, usd_idr: float, fear_greed: dict):
    total_modal = 0.0
    total_nilai = 0.0
    baris = []

    for sym, p in PORTFOLIO.items():
        try:
            ticker     = exchange.fetch_ticker(sym)
            harga_kini = ticker['last'] * usd_idr
            modal      = p['buy_price_idr'] * p['amount']
            nilai      = harga_kini * p['amount']
            pnl_val    = nilai - modal
            pnl_pct    = pnl_val / modal * 100
            ikon       = "🟢" if pnl_pct >= 0 else "🔴"
            total_modal += modal
            total_nilai += nilai
            baris.append(
                f"{ikon} *{sym}*\n"
                f"```\n"
                f"Beli : {format_rp(p['buy_price_idr'])}\n"
                f"Skrg : {format_rp(harga_kini)}\n"
                f"P/L  : {pnl_pct:+.2f}% ({format_rp(pnl_val)})\n"
                f"```"
            )
        except Exception as e:
            baris.append(f"⚠️ {sym} — gagal ({e})")

    total_pnl     = total_nilai - total_modal
    total_pnl_pct = total_pnl / total_modal * 100 if total_modal else 0
    ikon_total    = "🟢" if total_pnl_pct >= 0 else "🔴"
    now_wib       = datetime.now(timezone.utc) + timedelta(hours=7)
    
    fng_translation = {
        "Extreme Fear": "Ketakutan Ekstrem",
        "Fear": "Takut",
        "Neutral": "Netral",
        "Greed": "Serakah",
        "Extreme Greed": "Keserakahan Ekstrem"
    }
    label_indo = fng_translation.get(fear_greed['label'], fear_greed['label'])
    fg_str = f"{fear_greed['value']} — {label_indo}"

    behavior_map = {
        "Extreme Fear": "Ritel Panik JUAL, Bandar BELI",
        "Fear": "Ritel cicil JUAL, Bandar cicil BELI",
        "Neutral": "Ritel WAIT & SEE, Bandar Konsolidasi",
        "Greed": "Ritel cicil BELI, Bandar cicil JUAL",
        "Extreme Greed": "Ritel FOMO BELI, Bandar JUAL (TP)"
    }
    analisis_pasar = behavior_map.get(fear_greed['label'], "Ritel & Bandar Bergerak Dinamis")

    pesan = (
        f"📊 *PORTOFOLIO — {now_wib.strftime('%d %b %Y, %H:%M WIB')}*\n\n"
        + "\n".join(baris)
        + f"\n────────────────────\n"
        + f"{ikon_total} *SUMMARY*\n"
        + f"```\n"
        + f"Total Beli : {format_rp(total_modal)}\n"
        + f"Total Skrg : {format_rp(total_nilai)}\n"
        + f"Total P/L  : {total_pnl_pct:+.2f}% ({format_rp(total_pnl)})\n"
        + f"------------------------------\n"
        + f"Pasar      : {fg_str}\n"
        + f"Aksi       : {analisis_pasar}\n"
        + f"```"
    )
    await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

# ============================================================
# MAIN EXECUTOR
# ============================================================

async def main():
    exchange = ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'timeout': 30_000,
    })
    exchange.load_markets()

    exchange_futures = ccxt.kucoinfutures({
        'enableRateLimit': True,
        'timeout': 30_000,
    })

    bot        = Bot(token=TOKEN)
    usd_idr    = get_usd_idr()
    fear_greed = get_fear_greed()
    now_wib    = datetime.now(timezone.utc) + timedelta(hours=7)
    is_weekend = now_wib.weekday() in [5, 6]

    print(f"[{now_wib.strftime('%H:%M WIB')}] USD/IDR={usd_idr:,.0f} | "
          f"F&G={fear_greed['value']} ({fear_greed['label']}) | "
          f"Weekend={is_weekend}")

    for symbol in ASSET_LIST:
        try:
            bars_1h = exchange.fetch_ohlcv(symbol, '1h', limit=60)
            bars_4h = exchange.fetch_ohlcv(symbol, '4h', limit=60)

            df_1h = pd.DataFrame(
                bars_1h, columns=['timestamp','open','high','low','close','volume']
            ).astype({'open':float,'high':float,'low':float,'close':float,'volume':float})

            df_4h = pd.DataFrame(
                bars_4h, columns=['timestamp','open','high','low','close','volume']
            ).astype({'open':float,'high':float,'low':float,'close':float,'volume':float})

            futures_sym  = FUTURES_MAP.get(symbol)
            funding_rate = get_funding_rate(exchange_futures, futures_sym) if futures_sym else None

            hasil = analisa(df_1h, df_4h, usd_idr, funding_rate, fear_greed, is_weekend)

            if hasil:
                pesan = format_pesan(symbol, hasil)
                await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
                print(f"  ✅ Sinyal terkirim: {symbol} — {hasil['tipe']}")
            else:
                print(f"  — {symbol}: tidak ada sinyal")

        except Exception as e:
            print(f"  ❌ Error {symbol}: {e}")

        await asyncio.sleep(1.5)

    # Laporan portofolio 3× sehari (Pukul 09:00, 14:00, 20:00 WIB)
    if now_wib.hour in JAM_LAPORAN and now_wib.minute < 30:
        await kirim_laporan(bot, exchange, usd_idr, fear_greed)
        print("  📊 Laporan portofolio terkirim")

if __name__ == '__main__':
    asyncio.run(main())
