import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "GANTI_DENGAN_TOKEN_BOT_MU")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "GANTI_DENGAN_CHAT_ID_MU")

SPIKE_MULTIPLIER = 2.5
VOL_MULTIPLIER = 2.0

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},
}

ASSET_LIST = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

# Fungsi helper untuk mendapatkan kurs USD ke IDR
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 16000 

async def kirim_laporan_porto(bot, exchange, usd_idr_rate):
    await bot.send_message(chat_id=CHAT_ID, text="📊 *LAPORAN PORTOFOLIO*", parse_mode='Markdown')
    for symbol, p in PORTFOLIO.items():
        try:
            ticker = exchange.fetch_ticker(symbol)
            curr_price_idr = ticker['last'] * usd_idr_rate
            
            modal_idr = p['buy_price_idr'] * p['amount']
            current_value_idr = curr_price_idr * p['amount']
            pnl_val = current_value_idr - modal_idr
            pnl_pct = (pnl_val / modal_idr) * 100
            status = "🟢 PROFIT" if pnl_pct >= 0 else "🔴 LOSS"
            
            msg = (f"*{symbol}*\nStatus: {status}\n"
                   f"Beli: Rp {modal_idr / p['amount']:,.0f}\n"
                   f"Sekarang: Rp {curr_price_idr:,.0f}\n"
                   f"P/L: {pnl_pct:.2f}% (Rp {pnl_val:,.0f})")
            
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Gagal report {symbol}: {e}")

async def cek_koin(exchange, symbol, bot):
    try:
        # Fetch OHLCV dengan penanganan error
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Indikator
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        curr = df.iloc[-2]
        prev = df.iloc[-3]
        
        # Pivot S2 & R2
        p = (prev['high'] + prev['low'] + prev['close']) / 3
        r2 = p + (prev['high'] - prev['low'])
        s2 = p - (prev['high'] - prev['low'])
        
        # Spike Detector
        df['body'] = abs(df['close'] - df['open'])
        avg_body = df['body'].rolling(window=10).mean().iloc[-3]
        avg_vol = df['volume'].rolling(window=10).mean().iloc[-3]
        
        is_spike_body = curr['body'] > (avg_body * SPIKE_MULTIPLIER)
        is_spike_vol = curr['volume'] > (avg_vol * VOL_MULTIPLIER)
        
        # Logika Sinyal
        if curr['close'] > r2 and is_spike_body and is_spike_vol:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚀 *BREAKOUT {symbol}*\nHarga: {curr['close']:.4f}\nTembus R2!", parse_mode='Markdown')
        elif curr['close'] < s2 and is_spike_body and is_spike_vol:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚨 *BREAKDOWN {symbol}*\nHarga: {curr['close']:.4f}\nTembus S2!", parse_mode='Markdown')
            
        golden = (df['ema9'].iloc[-3] < df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] > df['ema21'].iloc[-2])
        dead = (df['ema9'].iloc[-3] > df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] < df['ema21'].iloc[-2])
        
        if golden: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *GOLDEN CROSS {symbol}*", parse_mode='Markdown')
        if dead: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *DEAD CROSS {symbol}*", parse_mode='Markdown')

    except Exception as e:
        print(f"Error analisa {symbol}: {e}")

async def main():
    # Setup Exchange dengan Header agar tidak diblokir
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    exchange = ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'headers': headers,
        'timeout': 30000 
    })
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market: {e}")
        return

    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    now_wib = datetime.utcnow() + timedelta(hours=7)
    
    # Eksekusi
    for symbol in ASSET_LIST:
        await cek_koin(exchange, symbol, bot)
        await asyncio.sleep(2) # Delay lebih aman
    
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
