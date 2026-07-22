import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SPIKE_MULTIPLIER = 2.5
VOL_MULTIPLIER = 2.0

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1311140722, 'amount': 0.00076261}, 
    'ETH/USDT': {'buy_price_idr': 37447016, 'amount': 0.05060638},
}

ASSET_LIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'LTC/USDT', 'AAVE/USDT', 'ONDO/USDT', 'DOT/USDT',
    'LINK/USDT', 'BCH/USDT'
]

# --- FUNGSI HELPER ---
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 16000 

def cek_aktivitas_transaksi(exchange, symbol):
    try:
        trades = exchange.fetch_trades(symbol, limit=50)
        
        volume_beli = 0.0
        volume_jual = 0.0
        
        for trade in trades:
            side = trade.get('side')
            amount = trade.get('amount', 0)
            price = trade.get('price', 0)
            notional = amount * price 
            
            if side == 'buy':
                volume_beli += notional
            elif side == 'sell':
                volume_jual += notional
                
        total_volume = volume_beli + volume_jual
        
        if total_volume == 0:
            return "Buy: 50.0% | Sell: 50.0%"
            
        pct_beli = (volume_beli / total_volume) * 100
        pct_jual = (volume_jual / total_volume) * 100
        
        return f"Buy: {pct_beli:.1f}% | Sell: {pct_jual:.1f}%"
            
    except Exception as e:
        print(f"Debug Error Trades {symbol}: {e}")
        return "⚠️ Data Transaksi Gagal Dimuat"

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
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        if len(bars) < 40:
            return
            
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. EMA Calculations
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # 2. Spike Detector Base
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=5).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=5).mean().shift(1)
        
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
        
        # 3. Perhitungan Pivot S2, R2 hingga S4, R4
        range_harga = prev['high'] - prev['low']
        p = (prev['high'] + prev['low'] + prev['close']) / 3
        r2 = p + range_harga
        s2 = p - range_harga
        r3 = p + (2 * range_harga) 
        s3 = p - (2 * range_harga) 
        r4 = p + (3 * range_harga)  
        s4 = p - (3 * range_harga)  
        
        is_price_break = curr['close'] > r2
        is_price_breakdown = curr['close'] < s2
        is_spike_body = curr['body'] > (df['avg_body'].iloc[curr_idx] * SPIKE_MULTIPLIER)
        
        vol_proyeksi = curr['volume'] * pengali_vol_live
        is_spike_vol = vol_proyeksi > (df['avg_vol'].iloc[curr_idx] * VOL_MULTIPLIER)
        
        harga_idr = curr['close'] * usd_idr_rate
        status_aktivitas = cek_aktivitas_transaksi(exchange, symbol)
        
        # --- 4. LOGIKA TAMBAHAN: HIGHER HIGH (HH) & HIGHER LOW (HL) ---
        # Mencari titik Swing High (puncak lokal) dan Swing Low (lembah lokal) dari 20 candle terakhir
        recent_window = df.iloc[-20:]
        recent_high = recent_window['high'].max()
        recent_low = recent_window['low'].min()
        
        # Bandingkan dengan gelombang sebelumnya (misal 20 candle sebelum jendela tersebut)
        prev_window = df.iloc[-20:-10]
        prev_high = prev_window['high'].max()
        prev_low = prev_window['low'].min()
        
        is_higher_high = recent_high > prev_high
        is_higher_low = recent_low > prev_low
        
        struktur_pasar = ""
        if is_higher_high and is_higher_low:
            struktur_pasar = "\n📈 *Struktur Market: Uptrend Kuat (HH & HL Terbentuk)*"
        elif is_higher_high:
            struktur_pasar = "\n↗️ *Struktur Market: Potensi HH (Puncak Baru)*"

        # 5. Logika Sinyal BREAKOUT / BREAKDOWN (Berjenjang hingga S4/R4)
        if (is_price_break or is_price_breakdown) and is_spike_body and is_spike_vol:
            if is_price_break:
                tipe = "BREAKOUT"
                if curr['close'] >= r3:
                    target_idr = r4 * usd_idr_rate
                    prediksi = f"⚠️ Sudah tembus R3! Perkiraan target lanjut: Rp {target_idr:,.0f} (R4)"
                else:
                    target_idr = r3 * usd_idr_rate
                    prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (R3)"
            else:
                tipe = "BREAKDOWN"
                if curr['close'] <= s3:
                    target_idr = s4 * usd_idr_rate
                    prediksi = f"⚠️ Sudah jebol S3! Perkiraan target lanjut: Rp {target_idr:,.0f} (S4)"
                else:
                    target_idr = s3 * usd_idr_rate
                    prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (S3)"
                
            pesan = (
                f"{mode_scan}\n"
                f"✅ *KONFIRMASI {tipe} {symbol}*\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"_(Body & Vol Spike Terpenuhi!)_\n"
                f"Aktivitas: {status_aktivitas}"
                f"{struktur_pasar}\n\n"
                f"{prediksi}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif is_spike_body and is_spike_vol:
            pesan = (
                f"{mode_scan}\n"
                f"⚡ *SIAGA {symbol}*\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"_(Body & Volume Spike Terdeteksi!)_\n"
                f"Aktivitas: {status_aktivitas}"
                f"{struktur_pasar}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif (is_price_break or is_price_breakdown) and is_spike_body:
            if is_price_break:
                tipe = "BREAKOUT"
                if curr['close'] >= r3:
                    target_idr = r4 * usd_idr_rate
                    prediksi = f"⚠️ Sudah tembus R3! Perkiraan target lanjut: Rp {target_idr:,.0f} (R4)"
                else:
                    target_idr = r3 * usd_idr_rate
                    prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (R3)"
            else:
                tipe = "BREAKDOWN"
                if curr['close'] <= s3:
                    target_idr = s4 * usd_idr_rate
                    prediksi = f"⚠️ Sudah jebol S3! Perkiraan target lanjut: Rp {target_idr:,.0f} (S4)"
                else:
                    target_idr = s3 * usd_idr_rate
                    prediksi = f"Perkiraan target selanjutnya Rp {target_idr:,.0f} (S3)"

            pesan = (
                f"{mode_scan}\n"
                f"🔍 *AWAL {tipe} {symbol}*\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"_(Body Spike Terdeteksi, Kekurangan Volume!)_\n"
                f"Aktivitas: {status_aktivitas}"
                f"{struktur_pasar}\n\n"
                f"{prediksi}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        # 6. Sinyal EMA CROSS
        slope_ema9 = abs(df['ema9'].iloc[curr_idx] - df['ema9'].iloc[prev_idx]) / df['ema9'].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.3  
        
        golden = (df['ema9'].iloc[prev_idx] < df['ema21'].iloc[prev_idx]) and (df['ema9'].iloc[curr_idx] > df['ema21'].iloc[curr_idx])
        dead = (df['ema9'].iloc[prev_idx] > df['ema21'].iloc[prev_idx]) and (df['ema9'].iloc[curr_idx] < df['ema21'].iloc[prev_idx])
        
        if golden and is_spike_vol and is_sudut_tajam:
            pesan = (
                f"{mode_scan}\n"
                f"🔔 *GOLDEN CROSS VALID {symbol}*\n"
                f"🚀 _Didukung Volume Spike & Sudut Mendongak!_\n"
                f"Aktivitas: {status_aktivitas}"
                f"{struktur_pasar}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        if dead and is_spike_vol and is_sudut_tajam:
            pesan = (
                f"{mode_scan}\n"
                f"🔔 *DEAD CROSS VALID {symbol}*\n"
                f"📉 _Didukung Volume Spike & Sudut Menukik!_\n"
                f"Aktivitas: {status_aktivitas}"
                f"{struktur_pasar}"
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
        print(f"DEBUG: Sedang mencheck {symbol}")
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 
    
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        print("DEBUG: Mengirim laporan portofolio...")
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
