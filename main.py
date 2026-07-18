import os
import asyncio
import ccxt
import pandas as pd
import requests # Library baru untuk ambil kurs
from telegram import Bot

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20

WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'LRC/USDT', 'BNB/USDT', 
    'XRP/USDT', 'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'UNI/USDT', 
    'LTC/USDT', 'TRX/USDT', 'ATOM/USDT', 'ALGO/USDT', 'BCH/USDT', 
    'GRT/USDT', 'FIL/USDT', 'DOGE/USDT', 'SUI/USDT', 'ARB/USDT', 
    'TON/USDT', 'INJ/USDT', 'NEAR/USDT', 'OP/USDT', 'AVAX/USDT'
]

# Fungsi mengambil kurs otomatis
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        data = response.json()
        return data['rates']['IDR']
    except:
        return 16000 # Fallback jika API error

def hitung_rsi(data, period=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 0.000000001)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

async def cek_koin(exchange, coin, bot, usd_idr_rate):
    try:
        bars = exchange.fetch_ohlcv(coin, timeframe='1h', limit=50)
        if not bars or len(bars) < 25:
            return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['AvgVol'] = df['volume'].rolling(window=20).mean()
        df['RSI'] = hitung_rsi(df['close'])
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Hitung harga IDR dinamis
        harga_idr = curr['close'] * usd_idr_rate
        
        # --- 1. LAPORAN RUTIN (Heartbeat) ---
        if coin in ['BTC/USDT', 'ETH/USDT']:
            change_pct = ((curr['close'] - prev['close']) / prev['close']) * 100
            arah = "🟢 NAIK" if change_pct > 0 else "🔴 TURUN"
            pesan = (f"🕒 *Laporan Rutin {coin}*\n"
                     f"Perubahan: {arah} {change_pct:.2f}%\n"
                     f"Harga: {curr['close']:.2f} USDT (Rp {harga_idr:,.0f})")
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

        # --- 2. LOGIKA SINYAL TRADING ---
        is_volume_ok = curr['volume'] > curr['AvgVol']
        is_golden_cross = (prev['EMA9'] <= prev['EMA21']) and (curr['EMA9'] > curr['EMA21'])
        is_dead_cross = (prev['EMA9'] >= prev['EMA21']) and (curr['EMA9'] < curr['EMA21'])
        
        if is_golden_cross and is_volume_ok:
            pesan = (f"🟢 *SINYAL BELI {coin}*\n"
                     f"Harga: {curr['close']:.4f} USDT (Rp {harga_idr:,.0f})\n"
                     f"RSI: {curr['RSI']:.2f}")
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
        elif is_dead_cross:
            pesan = f"🔴 *SINYAL JUAL {coin}*\nHarga: {curr['close']:.4f} USDT (Rp {harga_idr:,.0f})\nStatus: Dead Cross"
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
    except Exception as e:
        print(f"Error pada {coin}: {e}")

async def main():
    exchange = ccxt.binance({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    # Ambil kurs sekali di awal agar efisien
    usd_idr_rate = get_usd_to_idr()
    
    for coin in WATCHLIST:
        await cek_koin(exchange, coin, bot, usd_idr_rate)
        await asyncio.sleep(2) 

if __name__ == '__main__':
    asyncio.run(main())
