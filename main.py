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

# --- FUNGSI HELPER ---
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 16000 

async def kirim_laporan_porto(bot, exchange, usd_idr_rate):
    try:
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
    except Exception as e:
        print(f"Error pada fungsi kirim_laporan_porto: {e}")

async def cek_koin(exchange, symbol, bot, usd_idr_rate):  # <-- Tambahkan usd_idr_rate di sini
    try:
        # Fetch data 1 jam
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        if len(bars) < 30:
            return
            
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. EMA Calculations
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # 2. Spike Detector
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=10).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=10).mean().shift(1)
        
        curr = df.iloc[-2]
        prev = df.iloc[-3]
        
        # 3. Perhitungan Pivot S2 & R2
        p = (prev['high'] + prev['low'] + prev['close']) / 3
        r2 = p + (prev['high'] - prev['low'])
        s2 = p - (prev['high'] - prev['low'])
        
        # Variabel Kondisi
        is_price_break = curr['close'] > r2
        is_price_breakdown = curr['close'] < s2
        is_spike_body = curr['body'] > (df['avg_body'].iloc[-2] * SPIKE_MULTIPLIER)
        is_spike_vol = curr['volume'] > (df['avg_vol'].iloc[-2] * VOL_MULTIPLIER)
        
        # Konversi harga ke IDR
        harga_idr = curr['close'] * usd_idr_rate
        
        # 4. Logika Sinyal BREAKOUT / BREAKDOWN (Berjenjang)
        # Notif Konfirmasi (Paling Prioritas)
        if (is_price_break or is_price_breakdown) and is_spike_body and is_spike_vol:
            tipe = "BREAKOUT" if is_price_break else "BREAKDOWN"
            await bot.send_message(chat_id=CHAT_ID, text=f"✅ *KONFIRMASI {tipe} {symbol}*\nHarga: Rp {harga_idr:,.0f}\n(Body & Vol Spike Terpenuhi!)", parse_mode='Markdown')
            
        # Notif Siaga (Body + Volume Spike saja)
        elif is_spike_body and is_spike_vol:
            await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ *SIAGA {symbol}*\nHarga: Rp {harga_idr:,.0f}\nBody & Volume Spike Terdeteksi!", parse_mode='Markdown')
            
        # Notif Awal (Price Break + Body Spike saja)
        elif (is_price_break or is_price_breakdown) and is_spike_body:
            tipe = "BREAKOUT" if is_price_break else "BREAKDOWN"
            await bot.send_message(chat_id=CHAT_ID, text=f"🔍 *AWAL {tipe} {symbol}*\nHarga: Rp {harga_idr:,.0f}\nBody Spike Terdeteksi!", parse_mode='Markdown')
            
        # 5. Sinyal EMA CROSS (Tetap)
        golden = (df['ema9'].iloc[-3] < df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] > df['ema21'].iloc[-2])
        dead = (df['ema9'].iloc[-3] > df['ema21'].iloc[-3]) and (df['ema9'].iloc[-2] < df['ema21'].iloc[-2])
        
        if golden: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *GOLDEN CROSS {symbol}*", parse_mode='Markdown')
        if dead: await bot.send_message(chat_id=CHAT_ID, text=f"🔔 *DEAD CROSS {symbol}*", parse_mode='Markdown')

    except Exception as e:
        print(f"Error analisa {symbol}: {e}")

# --- MAIN ---
async def main():
    print("DEBUG: Memulai main()...")
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
    
    # Eksekusi Analisa Sinyal (Pastikan usd_idr_rate ikut dikirim ke cek_koin)
    for symbol in ASSET_LIST:
        print(f"DEBUG: Sedang cek {symbol}")
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 
    
    # Eksekusi Laporan Porto
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        print("DEBUG: Mengirim laporan portofolio...")
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
