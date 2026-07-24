import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta, timezone

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
    'LINK/USDT', 'BCH/USDT', 'GRAM/USDC'
]

# --- FUNGSI HELPER ---
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 18000 

def cek_aktivitas_transaksi(exchange, symbol):
    try:
        trades = exchange.fetch_trades(symbol, limit=100)
        
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
        header = "📊 *LAPORAN PORTOFOLIO*\n"
        laporan_items = []

        for symbol, p in PORTFOLIO.items():
            try:
                ticker = exchange.fetch_ticker(symbol)
                curr_price_idr = ticker["last"] * usd_idr_rate

                modal_idr = p["buy_price_idr"] * p["amount"]
                current_value_idr = curr_price_idr * p["amount"]
                pnl_val = current_value_idr - modal_idr
                pnl_pct = (pnl_val / modal_idr) * 100
                status = "🟢 PROFIT" if pnl_pct >= 0 else "🔴 LOSS"

                status_aktivitas = cek_aktivitas_transaksi(exchange, symbol)

                # Format teks item dibuat rata kiri rapi
                item_text = f"""*{symbol}*
Status: {status}
{status_aktivitas}

Beli: Rp {p['buy_price_idr']:,.0f}
Skrg: Rp {curr_price_idr:,.0f}
P/L: {pnl_pct:.2f}% (Rp {pnl_val:,.0f})"""

                laporan_items.append(item_text)
            except Exception as e:
                print(f"Gagal report {symbol}: {e}")

        # Gabungkan item dengan garis pembatas jika ada data
        if laporan_items:
            garis_pembatas = "\n\n───────────────\n\n"
            pesan_lengkap = header + "\n" + garis_pembatas.join(laporan_items)
            
            await bot.send_message(
                chat_id=CHAT_ID, text=pesan_lengkap, parse_mode="Markdown"
            )

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
        
        # --- PERHITUNGAN ATR 14 ---
        tr0 = df['high'] - df['low']
        tr1 = (df['high'] - df['close'].shift(1)).abs()
        tr2 = (df['low'] - df['close'].shift(1)).abs()
        df['tr'] = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df['atr'] = df['tr'].rolling(window=14).mean()
        
        # 2. Spike Detector Base
        df['body'] = abs(df['close'] - df['open'])
        df['avg_body'] = df['body'].rolling(window=3).mean().shift(1)
        df['avg_vol'] = df['volume'].rolling(window=3).mean().shift(1)
        
        # --- SISTEM DETEKSI WAKTU BERTINGKAT ---
        now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
        menit_sekarang = now_wib.minute
        
        if menit_sekarang < 30:
            curr_idx = -2
            prev_idx = -3
            mode_scan = "🎯 *[Candle Close]*"
            pengali_vol_live = 1.0 
        else:
            curr_idx = -1
            prev_idx = -2
            mode_scan = "⚠️ *[Candle Running]*"
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
        atr_idr = curr['atr'] * usd_idr_rate
        status_aktivitas = cek_aktivitas_transaksi(exchange, symbol)
        
        # --- HITUNG SL & TP KHUSUS SPOT MARKET ---
        sl_bullish = harga_idr - (1.5 * atr_idr)
        tp_bullish = harga_idr + (2.0 * atr_idr)
        
        saran_atr_bull = f"🎯 *Rekomendasi:*\n- Sinyal NAIK...!\n- SL: Rp {sl_bullish:,.0f}\n- TP: Rp {tp_bullish:,.0f}"
        saran_atr_bear = f"🎯 *Rekomendasi:*\n- Sinyal TURUN...!\n- Pertimbangkan Cut Loss jika hold."

        # --- 4. LOGIKA TAMBAHAN: HIGHER HIGH (HH) & HIGHER LOW (HL) ---
        recent_window = df.iloc[-14:]
        recent_high = recent_window['high'].max()
        recent_low = recent_window['low'].min()
        
        prev_window = df.iloc[-14:-7]
        prev_high = prev_window['high'].max()
        prev_low = prev_window['low'].min()
        
        is_higher_high = recent_high > prev_high
        is_higher_low = recent_low > prev_low
        
        struktur_pasar = ""
        if is_higher_high and is_higher_low:
            struktur_pasar = "\n📈 *Pola NAIK Kuat (HH HL Muncul)*"
        elif is_higher_high:
            struktur_pasar = "\n↗️ *Potensi Puncak Baru*"

        pesan_sinyal = None

        # 5. Logika Sinyal BELI / JUAL & SPIKE
        if (is_price_break or is_price_breakdown) and is_spike_body and is_spike_vol:
            if is_price_break:
                tipe = "BELI"
                if curr['close'] >= r3:
                    target_idr = r4 * usd_idr_rate
                    prediksi = f"⚠️ Tembus Atap-3! NAIK ke: Rp {target_idr:,.0f} (R4)"
                else:
                    target_idr = r3 * usd_idr_rate
                    prediksi = f"NAIK ke Rp {target_idr:,.0f} (R3)"
                info_atr = saran_atr_bull
            else:
                tipe = "JUAL"
                if curr['close'] <= s3:
                    target_idr = s4 * usd_idr_rate
                    prediksi = f"⚠️ Jebol Lantai-3!\n\n"
                    TURUN ke: Rp {target_idr:,.0f} (S4)"
                else:
                    target_idr = s3 * usd_idr_rate
                    prediksi = f"TURUN ke Rp {target_idr:,.0f} (S3)"
                info_atr = saran_atr_bear
                
            pesan_sinyal = (
                f"{mode_scan}\n"
                f"✅ *KONFIRMASI {tipe} {symbol}*\n\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"Aktivitas: {status_aktivitas}\n"
                f"Candle OK & Vol OK...!\n\n"
                f"{struktur_pasar}\n\n"
                f"{prediksi}\n\n"
                f"{info_atr}"
            )
            
        elif is_spike_body and is_spike_vol:
            pesan_sinyal = (
                f"{mode_scan}\n"
                f"⚡ *SIAGA BELI {symbol}*\n\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"Aktivitas: {status_aktivitas}\n"
                f"Candle OK & Spike OK...!\n\n"
                f"{struktur_pasar}\n\n"
                f"{saran_atr_bull}"
            )
            
        elif (is_price_break or is_price_breakdown) and is_spike_body:
            if is_price_break:
                tipe = "BELI"
                if curr['close'] >= r3:
                    target_idr = r4 * usd_idr_rate
                    prediksi = f"⚠️ Tembus Atap-3! \n\n"
                    Perkiraan NAIK ke: Rp {target_idr:,.0f} (R4)"
                else:
                    target_idr = r3 * usd_idr_rate
                    prediksi = f"Perkiraan NAIK ke Rp {target_idr:,.0f} (R3)"
                info_atr = saran_atr_bull
            else:
                tipe = "JUAL"
                if curr['close'] <= s3:
                    target_idr = s4 * usd_idr_rate
                    prediksi = f"⚠️ Jebol Lantai-3!\nPerkiraan TURUN ke: Rp {target_idr:,.0f} (S4)"
                else:
                    target_idr = s3 * usd_idr_rate
                    prediksi = f"Perkiraan TURUN ke Rp {target_idr:,.0f} (S3)"
                info_atr = saran_atr_bear

            pesan_sinyal = (
                f"{mode_scan}\n"
                f"🔍 *AWAL SINYAL {tipe} {symbol}*\n\n"
                f"Harga: Rp {harga_idr:,.0f}\n"
                f"Aktivitas: {status_aktivitas}\n"
                f"_(Spike Ok, Vol Kurang!)_\n"
                f"{struktur_pasar}\n\n"
                f"{prediksi}\n\n"
                f"{info_atr}"
            )

        # 6. Sinyal EMA CROSS & PRA-GOLDEN CROSS
        if not pesan_sinyal:
            slope_ema9 = abs(df['ema9'].iloc[curr_idx] - df['ema9'].iloc[prev_idx]) / df['ema9'].iloc[prev_idx] * 100
            is_sudut_tajam = slope_ema9 > 0.25  
            
            ema9_now = df['ema9'].iloc[curr_idx]
            ema9_prev = df['ema9'].iloc[prev_idx]
            ema21_now = df['ema21'].iloc[curr_idx]
            ema21_prev = df['ema21'].iloc[prev_idx]
            
            golden = (ema9_prev < ema21_prev) and (ema9_now > ema21_now)
            dead = (ema9_prev > ema21_prev) and (ema9_now < ema21_now)
            
            belum_cross = ema9_now < ema21_now
            jarak_sekarang = ema21_now - ema9_now
            jarak_sebelumnya = ema21_prev - ema9_prev
            jarak_menyempit = jarak_sekarang < jarak_sebelumnya
            ema9_menukik_naik = ema9_now > ema9_prev
            
            is_pra_golden_cross = belum_cross and jarak_menyempit and ema9_menukik_naik
            
            if is_pra_golden_cross and is_sudut_tajam:
                pesan_sinyal = (
                    f"{mode_scan}\n"
                    f"🪝 *POTENSI BELI (PRA-GOLDEN CROSS) {symbol}*\n"
                    f"⚡ _EMA 9 Melengkung Naik Mendekati EMA 21!_\n\n"
                    f"Aktivitas: {status_aktivitas}"
                    f"{struktur_pasar}\n\n"
                    f"{saran_atr_bull}"
                )
            elif golden and is_spike_vol and is_sudut_tajam:
                pesan_sinyal = (
                    f"{mode_scan}\n"
                    f"🔔 *SINYAL BELI VALID (GOLDEN CROSS) {symbol}*\n"
                    f"🚀 _Didukung Volume Spike & Sudut Mendongak!_\n\n"
                    f"Aktivitas: {status_aktivitas}"
                    f"{struktur_pasar}\n\n"
                    f"{saran_atr_bull}"
                )
            elif dead and is_spike_vol and is_sudut_tajam:
                pesan_sinyal = (
                    f"{mode_scan}\n"
                    f"🔔 *SINYAL JUAL VALID (DEAD CROSS) {symbol}*\n"
                    f"📉 _Didukung Volume Spike & Sudut Menukik!_\n\n"
                    f"Aktivitas: {status_aktivitas}"
                    f"{struktur_pasar}\n\n"
                    f"{saran_atr_bear}"
                )

        if pesan_sinyal:
            await bot.send_message(chat_id=CHAT_ID, text=pesan_sinyal, parse_mode='Markdown')

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
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    
    for symbol in ASSET_LIST:
        print(f"DEBUG: Sedang mencheck {symbol}")
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(2) 
    
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        print("DEBUG: Mengirim laporan portofolio...")
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)

if __name__ == '__main__':
    asyncio.run(main())
