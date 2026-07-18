import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
THRESHOLD_NOTIF = 0.5 # Threshold laporan rutin (dalam persen)

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

async def cek_koin(exchange, symbol, bot, usd_idr_rate):
    try:
        # 1. AMBIL DATA 1H (Momentum)
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=60)
        if len(bars) < 50: return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['RSI'] = hitung_rsi(df['close'])
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        curr_price = df['close'].iloc[-1]
        prev_price = df['close'].iloc[-2]
        ema50_val = df['EMA50'].iloc[-1]
        curr_price_idr = curr_price * usd_idr_rate
        
        # 2. AMBIL DATA 1D (Tren Besar)
        bars_1d = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=60)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        ema50_1d = df_1d['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        tren_1d = "🟢 BULLISH (Aman)" if curr_price > ema50_1d else "🔴 BEARISH (Hati-hati)"
        
        # 3. LOGIKA PnL
        pnl_msg = ""
        if symbol in PORTFOLIO:
            p = PORTFOLIO[symbol]
            modal_idr = p['buy_price_idr'] * p['amount']
            current_value_idr = curr_price_idr * p['amount']
            profit_loss_idr = current_value_idr - modal_idr
            pnl_pct = (profit_loss_idr / modal_idr) * 100
            status = "🟢 PROFIT" if profit_loss_idr >= 0 else "🔴 LOSS"
            pnl_msg = f"\n{status}: {pnl_pct:.2f}% (Rp {profit_loss_idr:,.0f})"

        # 4. LAPORAN RUTIN (Filter berdasarkan THRESHOLD_NOTIF)
        if symbol in ['BTC/USDT', 'ETH/USDT']:
            change_pct = ((curr_price - prev_price) / prev_price) * 100
            
            # Cek apakah perubahan signifikan
            if abs(change_pct) >= THRESHOLD_NOTIF:
                arah = "🟢 NAIK" if change_pct > 0 else "🔴 TURUN"
                pesan = (f"🕒 *Laporan Rutin {symbol}*\n"
                         f"Perubahan 1 jam: {arah} {change_pct:.2f}%\n"
                         f"Tren 1D: {tren_1d}\n"
                         f"Harga: {curr_price:.2f} USD (Rp {curr_price_idr:,.0f})"
                         f"{pnl_msg}")
                await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

        # 5. LOGIKA MOMENTUM & LABEL RISIKO (Tetap Normal)
        prev_rsi = df['RSI'].iloc[-2]
        curr_rsi = df['RSI'].iloc[-1]
        
        # Sinyal Beli
        if prev_rsi < 20 and curr_rsi >= 20:
            risiko = "🟢 LOW RISK" if curr_price > ema50_val else "🔴 HIGH RISK"
            await bot.send_message(chat_id=CHAT_ID, 
                                   text=f"🟢 *SINYAL BELI {symbol}*\n"
                                        f"Status: {risiko} (Counter-Trend)\n"
                                        f"Catatan Tren 1D: {tren_1d}\n"
                                        f"Harga: {curr_price:.4f} USD\n"
                                        f"RSI: {curr_rsi:.2f}", 
                                   parse_mode='Markdown')
        
        # Sinyal Jual
        elif prev_rsi > 80 and curr_rsi <= 80:
            risiko = "🟢 LOW RISK" if curr_price < ema50_val else "🔴 HIGH RISK"
            await bot.send_message(chat_id=CHAT_ID, 
                                   text=f"🔴 *SINYAL JUAL {symbol}*\n"
                                        f"Status: {risiko} (Melawan Arus)\n"
                                        f"Catatan Tren 1D: {tren_1d}\n"
                                        f"Harga: {curr_price:.4f} USD\n"
                                        f"RSI: {curr_rsi:.2f}", 
                                   parse_mode='Markdown')

    except Exception as e:
        print(f"Error pada {symbol}: {e}")

async def main():
    exchange = ccxt.kucoin({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    
    for symbol in WATCHLIST:
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 

if __name__ == '__main__':
    asyncio.run(main())
