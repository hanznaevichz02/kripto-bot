import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "8801827940:AAH1KiGgn-Xq00-sm-uBcBegWGtQeY5UrOw")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1103768791")

SPIKE_MULTIPLIER = 2.5
VOL_MULTIPLIER = 2.0
PORTFOLIO = {'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638}}
ASSET_LIST = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

# Fungsi helper
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except: return 16000 

async def main():
    print("DEBUG: Memulai main()...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'headers': headers})
        
        print("DEBUG: Loading markets...")
        exchange.load_markets()
        print("DEBUG: Markets loaded.")

        bot = Bot(token=TOKEN)
        # Sisa logika kamu di sini...
        print("DEBUG: Bot berhasil diinisialisasi.")
        
        # Eksekusi singkat untuk test
        for symbol in ASSET_LIST:
            print(f"DEBUG: Sedang cek {symbol}")
            # Panggil fungsi cek_koin di sini nanti
            await asyncio.sleep(1)
            
        print("DEBUG: Selesai menjalankan main().")

    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    print("DEBUG: Memanggil asyncio.run(main())")
    asyncio.run(main())
