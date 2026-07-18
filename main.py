import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20

# Daftar koin yang dipantau
WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'LRC/USDT', 'BNB/USDT', 
    'XRP/USDT', 'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'UNI/USDT', 
    'LTC/USDT', 'TRX/USDT', 'ATOM/USDT', 'ALGO/USDT', 'BCH/USDT', 
    'GRT/USDT', 'FIL/USDT', 'DOGE/USDT', 'SUI/USDT', 'ARB/USDT', 
    'TON/USDT', 'INJ/USDT', 'NEAR/USDT', 'OP/USDT', 'AVAX/USDT'
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

async def cek_koin(exchange, coin, bot, usd_idr_rate):
    try:
        bars = exchange.fetch_ohlcv(coin, timeframe='1h', limit=50)
        if not bars or len(bars) < 25: return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['AvgVol'] = df['volume'].rolling(window=20).mean()
        df['RSI'] = hitung_rsi(df['close'])
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Hitung Nilai IDR
        curr_price_idr = curr['close'] * usd_idr_rate
        
        # --- LOGIKA PnL (Profit & Loss) ---
        pnl_msg = ""
        if coin in PORTFOLIO:
            p = PORTFOLIO[coin]
            modal_idr = p['buy_price_idr'] * p['amount']
            current_value_idr = curr_price_idr * p['amount']
            profit_loss_idr = current_value_idr - modal_idr
            pnl_pct = (profit_loss_idr / modal_idr) * 100
            
            status = "🟢 PROFIT" if profit_loss_idr >= 0 else "🔴 LOSS"
            pnl_msg = f"\n{status}: {pnl_pct:.2f}% (Rp {profit_loss_idr:,.0f})"

        # --- 1. LAPORAN RUTIN (Hanya untuk BTC/ETH) ---
        if coin in ['BTC/USDT', 'ETH/USDT']:
            change_pct = ((curr['close'] - prev['close']) / prev['close']) * 100
            arah = "🟢 NAIK" if change_pct > 0 else "🔴 TURUN"
            pesan = (f"🕒 *Laporan Rutin {coin}*\n"
                     f"Perubahan 1 jam: {arah} {change_pct:.2f}%\n"
                     f"Harga: {curr['close']:.2f} USD (Rp {curr_price_idr:,.0f})"
                     f"{pnl_msg}")
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

        # --- 2. LOGIKA SINYAL TRADING (Semua Koin) ---
        is_volume_ok = curr['volume'] > curr['AvgVol']
        is_golden_cross = (prev['EMA9'] <= prev['EMA21']) and (curr['EMA9'] > curr['EMA21'])
        is_dead_cross = (prev['EMA9'] >= prev['EMA21']) and (curr['EMA9'] < curr['EMA21'])
        
        if is_golden_cross and is_volume_ok:
            await bot.send_message(chat_id=CHAT_ID, text=f"🟢 *SINYAL BELI {coin}*\nHarga: {curr['close']:.4f} USD\nRSI: {curr['RSI']:.2f}", parse_mode='Markdown')
        elif is_dead_cross:
            await bot.send_message(chat_id=CHAT_ID, text=f"🔴 *SINYAL JUAL {coin}*\nHarga: {curr['close']:.4f} USD\nStatus: Dead Cross", parse_mode='Markdown')
            
    except Exception as e:
        print(f"Error pada {coin}: {e}")

async def main():
    exchange = ccxt.bybit({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    for coin in WATCHLIST:
        await cek_koin(exchange, coin, bot, usd_idr_rate)
        await asyncio.sleep(2) 

if __name__ == '__main__':
    asyncio.run(main())
