import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime  # Tambahkan ini

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
THRESHOLD_NOTIF = 0.5 

WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 
    'XRP/USDT', 'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'UNI/USDT', 
    'LTC/USDT', 'TRX/USDT', 'ATOM/USDT', 'BCH/USDT', 
    'DOGE/USDT', 'SUI/USDT', 'ARB/USDT', 'NEAR/USDT', 'AVAX/USDT'
]

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},   
}

# --- FUNGSI TAMBAHAN ---
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except:
        return 16000

def hitung_rsi(data, period=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 0.000000001)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

# Fungsi untuk laporan porto khusus
async def kirim_laporan_porto(bot, exchange, usd_idr_rate):
    pesan_header = "🌙 *Laporan Harian Portofolio (20:00)*"
    await bot.send_message(chat_id=CHAT_ID, text=pesan_header, parse_mode='Markdown')
    
    for symbol in PORTFOLIO:
        ticker = exchange.fetch_ticker(symbol)
        curr_price = ticker['last']
        curr_price_idr = curr_price * usd_idr_rate
        p = PORTFOLIO[symbol]
        
        modal_idr = p['buy_price_idr'] * p['amount']
        current_value_idr = curr_price_idr * p['amount']
        profit_loss_idr = current_value_idr - modal_idr
        pnl_pct = (profit_loss_idr / modal_idr) * 100
        status = "🟢 PROFIT" if profit_loss_idr >= 0 else "🔴 LOSS"
        
        pesan = (f"*{symbol}*\n"
                 f"{status}: {pnl_pct:.2f}% | Rp {profit_loss_idr:,.0f}")
        await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
        await asyncio.sleep(1)

async def cek_koin(exchange, symbol, bot, usd_idr_rate):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=60)
        if len(bars) < 50: return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['RSI'] = hitung_rsi(df['close'])
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        curr_price = df['close'].iloc[-1]
        prev_price = df['close'].iloc[-2]
        ema50_val = df['EMA50'].iloc[-1]
        curr_price_idr = curr_price * usd_idr_rate
        
        bars_1d = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=60)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        ema50_1d = df_1d['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        tren_1d = "🟢 BULLISH" if curr_price > ema50_1d else "🔴 BEARISH"
        
        # 4. LAPORAN RUTIN (Filter 0.5%)
        if symbol in ['BTC/USDT', 'ETH/USDT']:
            change_pct = ((curr_price - prev_price) / prev_price) * 100
            if abs(change_pct) >= THRESHOLD_NOTIF:
                arah = "🟢 NAIK" if change_pct > 0 else "🔴 TURUN"
                pesan = (f"🕒 *Laporan Rutin {symbol}*\n"
                         f"Perubahan: {arah} {change_pct:.2f}%\n"
                         f"Tren: {tren_1d}")
                await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

        # 5. SINYAL
        prev_rsi = df['RSI'].iloc[-2]
        curr_rsi = df['RSI'].iloc[-1]
        
        if prev_rsi < 20 and curr_rsi >= 20:
            await bot.send_message(chat_id=CHAT_ID, text=f"🟢 *SINYAL BELI {symbol}*\nTren 1D: {tren_1d}\nRSI: {curr_rsi:.2f}", parse_mode='Markdown')
        elif prev_rsi > 80 and curr_rsi <= 80:
            await bot.send_message(chat_id=CHAT_ID, text=f"🔴 *SINYAL JUAL {symbol}*\nTren 1D: {tren_1d}\nRSI: {curr_rsi:.2f}", parse_mode='Markdown')

    except Exception as e:
        print(f"Error pada {symbol}: {e}")

async def main():
    exchange = ccxt.kucoin({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    
    # Cek waktu sekarang (WIB diasumsikan sistem server mengikuti local time)
    now = datetime.now()
    
    # Logika Laporan Harian Jam 20:00 (Range 5 menit)
    if now.hour == 20 and now.minute < 5:
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)
    
    # Loop Watchlist tetap berjalan
    for symbol in WATCHLIST:
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 

if __name__ == '__main__':
    asyncio.run(main())
