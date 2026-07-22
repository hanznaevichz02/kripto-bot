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

SPIKE_MULTIPLIER = 3.0
VOL_MULTIPLIER = 2.5

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},
}

ASSET_LIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'LTC/USDT', 'AAVE/USDT', 'ONDO/USDT','DOT/USDT',
    'LINK/USDT', 'BCH/USDT'
]

# --- FUNGSI HELPER ---
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 16000 

def cek_tekanan_order_book(exchange, symbol):
    try:
        # Ambil order book secara default tanpa limit, lalu slice 10 teratas di Python
        order_book = exchange.fetch_order_book(symbol)
        bids = order_book['bids'][:10]
        asks = order_book['asks'][:10]
        
        total_bid_volume = sum([item[1] for item in bids])
        total_ask_volume = sum([item[1] for item in asks])
        
        # Logika Imbalance Ratio
        if total_bid_volume > (total_ask_volume * 1.5):
            return "🟢 Dinding Beli (Bids) Tebal"
        elif total_ask_volume > (total_bid_volume * 1.5):
            return "🔴 Tembok Jual (Asks) Tebal"
        else:
            return "⚪ Order Book Netral"
    except Exception as e:
        # Cetak error aslinya di terminal/log biar ketahuan kalau ada masalah lain
        print(f"Debug Error Order Book {symbol}: {e}")
        return "⚠️ Order Book Gagal Dimuat"

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

async def cek_koin(exchange, symbol, bot, usd_idr_rate):
    try:
        # Fetch data 1 jam
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        if len(bars) < 30:
            return
            
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. EMA Calculations
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # 2. Spike Detector Base
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=10).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=10).mean().shift(1)
        
        # --- SISTEM DETEKSI WAKTU BERTINGKAT ---
        now_wib = datetime.utcnow() + timedelta(hours=7)
        menit_sekarang = now_wib.minute
        
        if menit_sekarang < 30:
            curr_idx = -2
            prev_idx = -3
            mode_scan = "🎯 *[1H MATANG]*"
            pengali_vol_live = 1.0 
        else:
            curr_idx = -1
            prev_idx = -2
            mode_scan = "⚠️ *[EARLY WARNING]*"
            pengali_vol_live = 60 / menit_sekarang 

        curr = df.iloc[curr_idx]
        prev = df.iloc[prev_idx]
        
        # 3. Perhitungan Pivot S2 & R2 (dan R3, S3)
        range_harga = prev['high'] - prev['low']
        p = (prev['high'] + prev['low'] + prev['close']) / 3
        r2 = p + range_harga
        s2 = p - range_harga
        r3 = p + (2 * range_harga) 
        s3 = p - (2 * range_harga) 
        
        is_price_break = curr['close'] > r2
        is_price_breakdown = curr['close'] < s2
        is_spike_body = curr['body'] > (df['avg_body'].iloc[curr_idx] * SPIKE_MULTIPLIER)
        
        vol_proyeksi = curr['volume'] * pengali_vol_live
        is_spike_vol = vol_proyeksi > (df['avg_vol'].iloc[curr_idx] * VOL_MULTIPLIER)
        
        harga_idr = curr['close'] * usd_idr_rate
        
        # Ambil Status Order Book Real-Time
        status_ob = cek_tekanan_order_book(exchange, symbol)
        
        # 4. Logika Sinyal BREAKOUT / BREAKDOWN (Berjenjang)
        if (is_price_break or is_price_breakdown) and is_spike_body and is_spike_vol:
            if is_price_break:
                tipe = "BREAKOUT"
                target_idr = r3 * usd_idr_rate
                prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (R3)"
            else:
                tipe = "BREAKDOWN"
                target_idr = s3 * usd_idr_rate
                prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (S3)"
                
            pesan = (
                f"{mode_scan}\n"
                f"✅ *KONFIRMASI {tipe} {symbol}*\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"_(Body & Vol Spike Terpenuhi!)_\n"
                f"Order Book: {status_ob}\n\n"
                f"{prediksi}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif is_spike_body and is_spike_vol:
            pesan = (
                f"{mode_scan}\n"
                f"⚡ *SIAGA {symbol}*\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"_(Body & Volume Spike Terdeteksi!)_\n"
                f"Order Book: {status_ob}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif (is_price_break or is_price_breakdown) and is_spike_body:
            if is_price_break:
                tipe = "BREAKOUT"
                target_idr = r3 * usd_idr_rate
                prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (R3)"
            else:
                tipe = "BREAKDOWN"
                target_idr = s3 * usd_idr_rate
                prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (S3)"

            pesan = (
                f"{mode_scan}\n"
                f"🔍 *AWAL {tipe} {symbol}*\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"_(Body Spike Terdeteksi, Kekurangan Volume!)_\n"
                f"Order Book: {status_ob}\n\n"
                f"{prediksi}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        # 5. Sinyal EMA CROSS
        slope_ema9 = abs(df['ema9'].iloc[curr_idx] - df['ema9'].iloc[prev_idx]) / df['ema9'].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.3  
        
        golden = (df['ema9'].iloc[prev_idx] < df['ema21'].iloc[prev_idx]) and (df['ema9'].iloc[curr_idx] > df['ema21'].iloc[curr_idx])
        dead = (df['ema9'].iloc[prev_idx] > df['ema21'].iloc[prev_idx]) and (df['ema9'].iloc[curr_idx] < df['ema21'].iloc[curr_idx])
        
        if golden and is_spike_vol and is_sudut_tajam:
            pesan = (
                f"{mode_scan}\n"
                f"🔔 *GOLDEN CROSS VALID {symbol}*\n"
                f"🚀 _Didukung Volume Spike & Sudut Mendongak!_\n"
                f"Order Book: {status_ob}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        if dead and is_spike_vol and is_sudut_tajam:
            pesan = (
                f"{mode_scan}\n"
                f"🔔 *DEAD CROSS VALID {symbol}*\n"
                f"📉 _Didukung Volume Spike & Sudut Menukik!_\n"
                f"Order Book: {status_ob}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

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
    
    for symbol in ASSET_LIST:
        print(f"DEBUG: Sedang cek {symbol}")
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 
    
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        print("DEBUG: Mengirim laporan portofolio...")
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
