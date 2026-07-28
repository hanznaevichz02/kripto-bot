"""
========================================================
    KRIPTO BOT — Manual Analysis Script
    Fungsi  : Menganalisa koin pilihan via input GitHub Actions
    Output  : Kirim hasil prediksi jangka pendek & panjang ke Telegram
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

# Menangkap input simbol dari GitHub Actions (default ke BTC/USDT jika kosong)
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

def run_manual_analysis():
    print(f"DEBUG: Memulai analisa manual untuk {SYMBOL}...")
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
        harga_idr = float(c['close'] * usd_idr)

        # Indikator Teknikal (EMA & ATR)
        df_1h['ema9'] = df_1h['close'].ewm(span=9, adjust=False).mean()
        df_1h['ema21'] = df_1h['close'].ewm(span=21, adjust=False).mean()
        
        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()
        
        atr_idr = float(df_1h['atr'].iloc[curr_idx] * usd_idr)

        # Swing 4H untuk Jangka Panjang & Pendek
        swing_low = float(df_4h['low'].iloc[-8:-1].min()) * usd_idr
        swing_high = float(df_4h['high'].iloc[-8:-1].max()) * usd_idr

        # Penentuan Tren & Target
        is_bullish = df_1h['ema9'].iloc[curr_idx] > df_1h['ema21'].iloc[curr_idx]
        
        tren_pendek = "Potensi Naik" if is_bullish else "Potensi Turun"
        
        # Dipecah menjadi status dan nominal harga agar muat di layar HP
        status_pendek = "Turun ke" if not is_bullish else "Naik ke"
        harga_pendek  = f"Rp {swing_low:,.0f}" if not is_bullish else f"Rp {swing_high:,.0f}"
        
        status_panjang = "Naik ke" if is_bullish else "Turun ke"
        harga_panjang  = f"Rp {swing_high:,.0f}" if is_bullish else f"Rp {swing_low:,.0f}"

        # Kirim ke Telegram dengan format dibungkus block code ( ``` )
        bot = Bot(token=TOKEN)
        msg = (
            f"```text\n"
            f"🔍 [ANALISA] — {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Tren Pendek     : {tren_pendek}\n"
            f"• Jangka Pendek   : {status_pendek}\n"
            f"                    {harga_pendek}\n"
            f"• Jangka Panjang  : {status_panjang}\n"
            f"                    {harga_panjang}\n"
            f"• Harga Sekarang  : Rp {harga_idr:,.0f}\n"
            f"----------------------------------\n"
            f"Status: Selesai (On-Demand)\n"
            f"```"
        )
        
        import asyncio
        async def send():
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        
        asyncio.run(send())
        print(f"Sukses mengirim analisa manual {PAIR_NAME} ke Telegram.")

    except Exception as e:
        print(f"Error saat analisa {SYMBOL}: {e}")

if __name__ == '__main__':
    run_manual_analysis()
