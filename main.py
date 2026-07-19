import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SPIKE_MULTIPLIER = 2.0   # Sensitivitas harga (dikecilkan agar lebih mudah terdeteksi)
VOL_MULTIPLIER = 2.0     # Sensitivitas volume (Volume harus 2x lipat rata-rata)

WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'UNI/USDT', 'LTC/USDT', 
    'TRX/USDT', 'ATOM/USDT', 'BCH/USDT', 'AAVE/USDT', 'DOGE/USDT', 
    'SUI/USDT', 'ARB/USDT', 'NEAR/USDT', 'AVAX/USDT', 'TAO/USDT', 
    'ONDO/USDT', 'HYPE/USDT'
]

# ... [Fungsi kirim_laporan_rutin dan kirim_laporan_porto tetap sama] ...

async def cek_koin(exchange, symbol, bot, usd_idr_rate):
    try:
        # Ambil data lebih banyak untuk perhitungan pivot yang akurat
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        if len(bars) < 20: return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. Perhitungan Pivot Point (Dari candle terakhir yang sudah close)
        prev_candle = df.iloc[-2]
        p = (prev_candle['high'] + prev_candle['low'] + prev_candle['close']) / 3
        r2 = p + (prev_candle['high'] - prev_candle['low']) # Rumus R2 sederhana

        # 2. Spike Detector (Body & Volume)
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=5).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=5).mean().shift(1)
        
        curr_price = df['close'].iloc[-1]
        curr_body = df['body'].iloc[-1]
        curr_vol = df['volume'].iloc[-1]
        
        # Logika Kondisi
        is_spike_price = curr_body > (df['avg_body'].iloc[-1] * SPIKE_MULTIPLIER)
        is_spike_vol = curr_vol > (df['avg_vol'].iloc[-1] * VOL_MULTIPLIER)
        is_breakout_r2 = curr_price > r2

        # --- EKSEKUSI SINYAL ---
        if is_breakout_r2 and is_spike_price and is_spike_vol:
            await bot.send_message(
                chat_id=CHAT_ID, 
                text=f"🚀 *BREAKOUT MOMENTUM {symbol}*\n"
                     f"Harga: {curr_price:.4f}\n"
                     f"Status: Tembus R2 ({r2:.4f})\n"
                     f"Konfirmasi: Body & Volume Spike!",
                parse_mode='Markdown'
            )

    except Exception as e:
        print(f"Error pada {symbol}: {e}")

# ... [Fungsi main tetap sama] ...
