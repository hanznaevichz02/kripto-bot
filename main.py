import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta

# --- KONFIGURASI ---
TOKEN = "GANTI_DENGAN_TOKEN_BOT_MU"
CHAT_ID = "GANTI_DENGAN_CHAT_ID_MU"

# Multiplier untuk Spike
SPIKE_MULTIPLIER = 2.5
VOL_MULTIPLIER = 2.0

# Portofolio
PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},
}

# Daftar koin yang dipantau (Portfolio + Watchlist)
ASSET_LIST = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except:
        return 16000 # Fallback jika API gagal

async def kirim_laporan_porto(bot, exchange, usd_idr_rate):
    try:
        await bot.send_message(chat_id=CHAT_ID, text="📊 *LAPORAN PORTOFOLIO*", parse_mode='Markdown')
        for symbol in PORTFOLIO:
            try:
                ticker = exchange.fetch_ticker(symbol)
                curr_price_idr = ticker['last'] * usd_idr_rate
                p = PORTFOLIO[symbol]
                
                modal_idr = p['buy_price_idr'] * p['amount']
                current_value_idr = curr_price_idr * p['amount']
                pnl_val = current_value_idr - modal_idr
                pnl_pct = (pnl_val / modal_idr) * 100
                status = "🟢 PROFIT" if pnl_pct >= 0 else "🔴 LOSS"
                
                msg = (f"*{symbol}*\n"
                       f"Status: {status}\n"
                       f"Harga Beli: Rp {modal_idr / p['amount']:,.0f}\n"
                       f"Harga Sekarang: Rp {curr_price_idr:,.0f}\n"
                       f"P/L: {pnl_pct:.2f}% (Rp {pnl_val:,.0f})")
                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Gagal report {symbol}: {e}")
    except Exception as e:
        print(f"Error pada fungsi laporan: {e}")

async def cek_koin(exchange, symbol, bot):
    try:
        # Fetch data 1 jam (tambah limit biar cukup buat rolling)
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # EMA Calculations
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # Perhitungan Pivot S2 & R2
        prev = df.iloc[-2] # Candle yang sudah close
        p = (prev['high'] + prev['low'] + prev['close']) / 3
        r2 = p + (prev['high'] - prev['low'])
        s2 = p - (prev['high'] - prev['low'])
        
        # Spike Detector
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=10).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=10).mean().shift(1)
        
        curr = df.iloc[-2] # Candle yang sudah close
        is_spike_body = curr['body'] > (df['avg_body'].iloc[-2] * SPIKE_MULTIPLIER)
        is_spike_vol = curr['volume'] > (df['avg_vol'].iloc[-2] * VOL_MULTIPLIER)
        
        # 1. Sinyal BREAKOUT
        if curr['close'] > r2 and is_spike_body and is_spike_vol:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚀 *BREAKOUT {symbol}*\nHarga: {curr['close']:.4f}\nStatus: Tembus R2!", parse_mode='Markdown')

        # 2. Sinyal BREAKDOWN
        if curr['close'] < s2 and is_spike_body and is_spike_vol:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚨 *BREAKDOWN {symbol}*\nHarga: {curr['close']:.4f}\nTembus S2! Waspada!", parse_mode='Markdown')
            
        # 3. Sinyal EMA CROSS
        golden = (df['ema9'].iloc[-3] < df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] > df['ema21'].iloc[-2])
        dead = (df['ema9'].iloc[-3] > df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] < df['ema21'].iloc[-2])
        
        if golden: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *GOLDEN CROSS {symbol}*", parse_mode='Markdown')
        if dead: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *DEAD CROSS {symbol}*", parse_mode='Markdown')

    except Exception as e:
        print(f"Error pada {symbol}: {e}")

async def main():
    # Inisialisasi exchange
    exchange = ccxt.kucoin({'enableRateLimit': True})
    # Unduh daftar koin terbaru
    exchange.load_markets()    
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    now_wib = datetime.utcnow() + timedelta(hours=7)
    
    # 1. Eksekusi Analisa Sinyal (Selalu)
    for symbol in ASSET_LIST:
        await cek_koin(exchange, symbol, bot)
        await asyncio.sleep(1)
    
    # 2. Eksekusi Laporan Porto (Jam 9, 14, 20)
    # Penambahan menit < 15 untuk mencegah dobel notif dari cron-job
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
