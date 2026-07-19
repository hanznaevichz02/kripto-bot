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

SPIKE_MULTIPLIER = 2.5 
VOL_MULTIPLIER = 2.0   

WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'UNI/USDT', 'LTC/USDT', 
    'TRX/USDT', 'ATOM/USDT', 'BCH/USDT', 'AAVE/USDT', 'DOGE/USDT', 
    'SUI/USDT', 'ARB/USDT', 'NEAR/USDT', 'AVAX/USDT', 'TAO/USDT', 
    'ONDO/USDT', 'HYPE/USDT'
]

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},   
}

# --- FUNGSI ---

def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except:
        return 16000

async def kirim_laporan_rutin(bot, exchange):
    for symbol in ['BTC/USDT', 'ETH/USDT']:
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            curr_price = df['close'].iloc[-1]
            ma20 = df['close'].rolling(window=20).mean().iloc[-1]
            tren = "🟢 BULLISH" if curr_price > ma20 else "🔴 BEARISH"
            await bot.send_message(chat_id=CHAT_ID, text=f"🕒 *Laporan Rutin {symbol}*\nHarga: {curr_price:.2f}\nTren (MA20): {tren}", parse_mode='Markdown')
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error laporan rutin {symbol}: {e}")

async def kirim_laporan_porto(bot, exchange, usd_idr_rate):
    await bot.send_message(chat_id=CHAT_ID, text="🌙 *Laporan Harian Portofolio*", parse_mode='Markdown')
    for symbol in PORTFOLIO:
        try:
            ticker = exchange.fetch_ticker(symbol)
            curr_price = ticker['last']
            p = PORTFOLIO[symbol]
            modal_idr = p['buy_price_idr'] * p['amount']
            current_value_idr = (curr_price * usd_idr_rate) * p['amount']
            pnl_pct = ((current_value_idr - modal_idr) / modal_idr) * 100
            status = "🟢 PROFIT" if pnl_pct >= 0 else "🔴 LOSS"
            await bot.send_message(chat_id=CHAT_ID, text=f"*{symbol}* ({status}): {pnl_pct:.2f}%", parse_mode='Markdown')
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Gagal report {symbol}: {e}")

async def cek_koin(exchange, symbol, bot, usd_idr_rate):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        if len(bars) < 30: return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # --- PERSIAPAN ---
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        # Perhitungan Pivot S2 (Support) & R2 (Resistance)
        prev_candle = df.iloc[-2]
        p = (prev_candle['high'] + prev_candle['low'] + prev_candle['close']) / 3
        r2 = p + (prev_candle['high'] - prev_candle['low'])
        s2 = p - (prev_candle['high'] - prev_candle['low'])

        # Data candle terakhir (closed)
        last_closed = df.iloc[-2]
        curr_price = last_closed['close']
        
        # --- 1. ALARM: TREND REVERSAL (Khusus Aset Portofolio) ---
        if symbol in PORTFOLIO and curr_price < df['ma20'].iloc[-2]:
             await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ *WARNING: Tren Berubah {symbol}*\nHarga ({curr_price:.4f}) turun di bawah MA20. Potensi dump/reversal!", parse_mode='Markdown')

        # --- 2. ALARM: BREAKDOWN (Panic Selling) ---
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=10).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=10).mean().shift(1)
        
        is_spike_body = last_closed['body'] > (df['avg_body'].iloc[-2] * SPIKE_MULTIPLIER)
        is_spike_vol = last_closed['volume'] > (df['avg_vol'].iloc[-2] * VOL_MULTIPLIER)
        is_breakdown_s2 = curr_price < s2

        if is_breakdown_s2 and is_spike_body and is_spike_vol:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚨 *BREAKDOWN MOMENTUM {symbol}*\nHarga: {curr_price:.4f}\nTembus Support S2! Hati-hati Panic Selling!", parse_mode='Markdown')

        # --- 3. ALARM: BREAKOUT (Hanya jika Uptrend) ---
        is_uptrend = curr_price > df['ma20'].iloc[-2]
        if is_uptrend:
            if curr_price > r2 and is_spike_body and is_spike_vol:
                await bot.send_message(chat_id=CHAT_ID, text=f"🚀 *BREAKOUT MOMENTUM {symbol}*\n{curr_price:.4f} Tembus R2!", parse_mode='Markdown')

        # --- 4. ALARM: PREP (EMA CROSS) ---
        golden_cross = (df['ema9'].iloc[-3] < df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] > df['ema21'].iloc[-2])
        dead_cross = (df['ema9'].iloc[-3] > df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] < df['ema21'].iloc[-2])
        
        if golden_cross: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *PREP: Golden Cross {symbol}*", parse_mode='Markdown')
        elif dead_cross: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *PREP: Dead Cross {symbol}*", parse_mode='Markdown')

    except Exception as e:
        print(f"Error pada {symbol}: {e}")

async def main():
    exchange = ccxt.kucoin({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    now_wib = datetime.utcnow() + timedelta(hours=7)
    
    if now_wib.hour in [9, 14, 20]:
        await kirim_laporan_rutin(bot, exchange)
        if now_wib.hour == 20:
            await kirim_laporan_porto(bot, exchange, usd_idr_rate)
    
    for symbol in WATCHLIST:
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2)

if __name__ == '__main__':
    asyncio.run(main())
