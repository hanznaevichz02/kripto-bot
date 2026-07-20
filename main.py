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

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},   
}

def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except:
        return 16000

async def kirim_laporan_porto(bot, exchange, usd_idr_rate):
    try:
        await bot.send_message(chat_id=CHAT_ID, text="📊 *LAPORAN PORTOFOLIO*", parse_mode='Markdown')
        
        for symbol in PORTFOLIO:
            try:
                ticker = exchange.fetch_ticker(symbol)
                curr_price_usd = ticker['last']
                curr_price_idr = curr_price_usd * usd_idr_rate
                
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

async def main():
    exchange = ccxt.kucoin({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    
    # Debug: Cek jam berapa server menjalankan bot ini
    now_wib = datetime.utcnow() + timedelta(hours=7)
    print(f"Bot jalan pada jam: {now_wib.strftime('%H:%M:%S')}")
    
    # KIRIM LAPORAN 3x SEHARI (Cek jam 9, 14, 20)
    # Gunakan range 1 jam agar lebih toleran terhadap delay cron-job
    if now_wib.hour in [9, 14, 20]:
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
