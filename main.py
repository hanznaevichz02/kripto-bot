import os
import asyncio
import pandas as pd
import requests
import yfinance as yf
from telegram import Bot

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20

# Daftar koin yang dipantau
WATCHLIST = [
    'BTC-USD', 'ETH-USD', 'SOL-USD', 'LRC-USD', 'BNB-USD', 
    'XRP-USD', 'ADA-USD', 'DOT-USD', 'LINK-USD', 'UNI-USD', 
    'LTC-USD', 'TRX-USD', 'ATOM-USD', 'ALGO-USD', 'BCH-USD', 
    'GRT-USD', 'FIL-USD', 'DOGE-USD', 'SUI-USD', 'ARB-USD', 
    'TON-USD', 'INJ-USD', 'NEAR-USD', 'OP-USD', 'AVAX-USD'
]

# --- PENGATURAN PORTOFOLIO (Edit ini sesuai asetmu) ---
PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},   
}

def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except:
        return 16000 # Fallback jika API down

def hitung_rsi(data, period=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 0.000000001)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

async def cek_koin(symbol, bot, usd_idr_rate):
    try:
        # Mengambil data dari Yahoo Finance
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="2d", interval="1h")
        
        if len(df) < 25: return 

        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['AvgVol'] = df['Volume'].rolling(window=20).mean()
        df['RSI'] = hitung_rsi(df['Close'])
        
        curr_price = df['Close'].iloc[-1]
        prev_price = df['Close'].iloc[-2]
        
        curr_price_idr = curr_price * usd_idr_rate
        
        # --- LOGIKA PnL ---
        pnl_msg = ""
        if symbol in PORTFOLIO:
            p = PORTFOLIO[symbol]
            modal_idr = p['buy_price_idr'] * p['amount']
            current_value_idr = curr_price_idr * p['amount']
            profit_loss_idr = current_value_idr - modal_idr
            pnl_pct = (profit_loss_idr / modal_idr) * 100
            status = "🟢 PROFIT" if profit_loss_idr >= 0 else "🔴 LOSS"
            pnl_msg = f"\n{status}: {pnl_pct:.2f}% (Rp {profit_loss_idr:,.0f})"

        # --- LAPORAN RUTIN (BTC/ETH) ---
        if symbol in ['BTC-USD', 'ETH-USD']:
            change_pct = ((curr_price - prev_price) / prev_price) * 100
            arah = "🟢 NAIK" if change_pct > 0 else "🔴 TURUN"
            pesan = (f"🕒 *Laporan Rutin {symbol}*\n"
                     f"Perubahan 1 jam: {arah} {change_pct:.2f}%\n"
                     f"Harga: {curr_price:.2f} USD (Rp {curr_price_idr:,.0f})"
                     f"{pnl_msg}")
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

    except Exception as e:
        print(f"Error pada {symbol}: {e}")

async def main():
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    for symbol in WATCHLIST:
        await cek_koin(symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 

if __name__ == '__main__':
    asyncio.run(main())
