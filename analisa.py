"""
========================================================
    KRIPTO BOT — Advanced Analysis Script
    Fungsi  : Menganalisa tren, RSI, & Skenario Trading (TP/SL)
========================================================
"""

import os
import ccxt
import pandas as pd
import requests
from telegram import Bot

# --- KONFIGURASI ---
TOKEN  = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

raw_symbol = os.getenv("INPUT_SYMBOL", "BTC/USDT").upper().strip()
if "/" not in raw_symbol:
    SYMBOL = f"{raw_symbol}/USDT"
else:
    SYMBOL = raw_symbol

PAIR_NAME = SYMBOL.replace('/', '-').replace('USDT', 'IDR')

def get_usd_idr() -> float:
    try:
        r = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(r.json()['ticker']['last'])
    except Exception:
        return 18000.0

def run_advanced_analysis():
    print(f"DEBUG: Memulai analisa lanjutan untuk {SYMBOL}...")
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market: {e}")
        return

    usd_idr = get_usd_idr()

    try:
        bars_1h = exchange.fetch_ohlcv(SYMBOL, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(SYMBOL, timeframe='4h', limit=30)

        if len(bars_1h) < 40 or len(bars_4h) < 15:
            print(f"Data koin {SYMBOL} tidak mencukupi.")
            return

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)

        curr_idx = -2
        c = df_1h.iloc[curr_idx]
        harga_sekarang = float(c['close'] * usd_idr)

        # --- INDIKATOR TEKNIKAL 1H ---
        df_1h['ema9'] = df_1h['close'].ewm(span=9, adjust=False).mean()
        df_1h['ema21'] = df_1h['close'].ewm(span=21, adjust=False).mean()
        
        # ATR (Volatilitas)
        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()
        
        # RSI 14 (Momentum)
        delta = df_1h['close'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        ema_up = up.ewm(com=13, adjust=False).mean()
        ema_down = down.ewm(com=13, adjust=False).mean()
        rs = ema_up / ema_down
        df_1h['rsi'] = 100 - (100 / (1 + rs))

        # Variabel Nilai Indikator
        atr_idr = float(df_1h['atr'].iloc[curr_idx] * usd_idr)
        rsi_now = df_1h['rsi'].iloc[curr_idx]
        ema21_idr = float(df_1h['ema21'].iloc[curr_idx] * usd_idr)

        # --- LEVEL KUNCI 4H ---
        swing_low = float(df_4h['low'].iloc[-15:-1].min()) * usd_idr
        swing_high = float(df_4h['high'].iloc[-15:-1].max()) * usd_idr

        # --- LOGIKA SKENARIO & STATUS ---
        is_bullish = df_1h['ema9'].iloc[curr_idx] > df_1h['ema21'].iloc[curr_idx]
        
        # Status Momentum RSI
        if rsi_now >= 70:
            status_rsi = f"Overbought (RSI {rsi_now:.0f}) - Rawan Longsor"
        elif rsi_now <= 30:
            status_rsi = f"Oversold (RSI {rsi_now:.0f}) - Potensi Mantul"
        else:
            status_rsi = f"Normal (RSI {rsi_now:.0f})"

        # Skenario Trading
        if is_bullish:
            tren_teks = "BULLISH 🟢"
            rekomendasi = "Buy on Dip / Hold"
            area_beli = f"Rp {ema21_idr:,.0f} - Rp {harga_sekarang:,.0f}"
            target_tp = f"Rp {swing_high:,.0f}"
            batas_sl  = f"Rp {ema21_idr - (1.5 * atr_idr):,.0f}"
        else:
            tren_teks = "BEARISH 🔴"
            rekomendasi = "Wait & See / Serok Bawah"
            area_beli = f"Kisaran Rp {swing_low:,.0f}"
            target_tp = f"Rebound ke Rp {ema21_idr:,.0f}"
            batas_sl  = f"Rp {swing_low - (1.0 * atr_idr):,.0f}"

        # --- FORMAT PESAN TELEGRAM ---
        bot = Bot(token=TOKEN)
        msg = (
            f"```text\n"
            f"🔍 [ANALISA ADVANCED] — {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Harga       : Rp {harga_sekarang:,.0f}\n"
            f"• Tren (1H)   : {tren_teks}\n"
            f"• Momentum    : {status_rsi}\n\n"
            f"📋 SKENARIO TRADING SPOT\n"
            f"• Aksi        : {rekomendasi}\n"
            f"• Area Beli   : {area_beli}\n"
            f"• Take Profit : {target_tp}\n"
            f"• Cut Loss    : {batas_sl}\n"
            f"----------------------------------\n"
            f"Status: Selesai (On-Demand)\n"
            f"```"
        )
        
        import asyncio
        async def send():
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        
        asyncio.run(send())
        print(f"Sukses mengirim analisa {PAIR_NAME} ke Telegram.")

    except Exception as e:
        print(f"Error saat analisa {SYMBOL}: {e}")

if __name__ == '__main__':
    run_advanced_analysis()
