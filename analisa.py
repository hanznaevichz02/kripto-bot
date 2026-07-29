"""
========================================================
    KRIPTO BOT — Analisa Simpel, Dinamis & Rapi
    Fungsi  : Menganalisa 4H & 1D dengan Formatting Presisi
========================================================
"""

import os
import ccxt
import pandas as pd
import requests
from telegram import Bot
import asyncio
import textwrap

# --- KONFIGURASI ---
TOKEN  = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Menangkap input simbol dari GitHub Actions (default ke BTC/USDT)
raw_symbol = os.getenv("INPUT_SYMBOL", "BTC/USDT").upper().strip()
if "/" not in raw_symbol:
    SYMBOL = f"{raw_symbol}/USDT"
else:
    SYMBOL = raw_symbol

PAIR_NAME = SYMBOL.replace('/', '-').replace('USDT', 'IDR')

def get_usd_idr() -> float:
    try:
        r = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(r.json()['ticker']['last'])
    except Exception:
        return 18000.0

def rapihkan_teks(label: str, teks: str, width: int = 35) -> str:
    """Memotong teks panjang agar ter-indentasi rapi di bawah label"""
    indent_spasi = " " * len(label)
    return textwrap.fill(teks, width=width, initial_indent=label, subsequent_indent=indent_spasi)

def run_analysis():
    print(f"DEBUG: Memulai analisa manual untuk {SYMBOL}...")
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market: {e}")
        return

    usd_idr = get_usd_idr()

    try:
        # Menarik data 4H dan 1D
        bars_4h = exchange.fetch_ohlcv(SYMBOL, timeframe='4h', limit=50)
        bars_1d = exchange.fetch_ohlcv(SYMBOL, timeframe='1d', limit=30)

        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)

        # Harga saat ini (Update realtime dari candle terakhir)
        harga_sekarang = float(df_4h['close'].iloc[-1] * usd_idr)

        # --- RUMUS PIVOT POINTS KLASIK (Dari Candle 1D Kemarin) ---
        curr_1d = -2 
        high_1d = df_1d['high'].iloc[curr_1d]
        low_1d = df_1d['low'].iloc[curr_1d]
        close_1d = df_1d['close'].iloc[curr_1d]

        pivot = (high_1d + low_1d + close_1d) / 3
        r1 = (2 * pivot) - low_1d
        r2 = pivot + (high_1d - low_1d)
        s1 = (2 * pivot) - high_1d
        s2 = pivot - (high_1d - low_1d)

        # Konversi level ke IDR
        s1_idr, s2_idr = float(s1 * usd_idr), float(s2 * usd_idr)
        r1_idr, r2_idr = float(r1 * usd_idr), float(r2 * usd_idr)

        # --- INDIKATOR TREN (EMA 9 & 21) ---
        df_4h['ema9'] = df_4h['close'].ewm(span=9, adjust=False).mean()
        df_4h['ema21'] = df_4h['close'].ewm(span=21, adjust=False).mean()
        df_1d['ema9'] = df_1d['close'].ewm(span=9, adjust=False).mean()
        df_1d['ema21'] = df_1d['close'].ewm(span=21, adjust=False).mean()

        is_bullish_4h = df_4h['ema9'].iloc[-1] > df_4h['ema21'].iloc[-1]
        is_bullish_1d = df_1d['ema9'].iloc[-1] > df_1d['ema21'].iloc[-1]

        tren_4h_teks = "NAIK 🟢" if is_bullish_4h else "TURUN 🔴"
        tren_1d_teks = "NAIK 🟢" if is_bullish_1d else "TURUN 🔴"

        # --- LOGIKA DINAMIS TAMPILAN LANTAI / ATAP ---
        if is_bullish_4h:
            level_4h_teks = f"              Atap 1  : Rp {r1_idr:,.0f}\n              Atap 2  : Rp {r2_idr:,.0f}"
        else:
            level_4h_teks = f"              Lantai 1: Rp {s1_idr:,.0f}\n              Lantai 2: Rp {s2_idr:,.0f}"

        if is_bullish_1d:
            level_1d_teks = f"              Atap 1  : Rp {r1_idr:,.0f}\n              Atap 2  : Rp {r2_idr:,.0f}"
        else:
            level_1d_teks = f"              Lantai 1: Rp {s1_idr:,.0f}\n              Lantai 2: Rp {s2_idr:,.0f}"

        # --- RSI 4H ---
        delta = df_4h['close'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        ema_up = up.ewm(com=13, adjust=False).mean()
        ema_down = down.ewm(com=13, adjust=False).mean()
        rs = ema_up / ema_down
        df_4h['rsi'] = 100 - (100 / (1 + rs))
        rsi_4h = df_4h['rsi'].iloc[-1]

        if rsi_4h >= 70:
            status_rsi = f"Kekenyangan ({rsi_4h:.0f}) - Rawan Turun"
        elif rsi_4h <= 30:
            status_rsi = f"Kebanting ({rsi_4h:.0f}) - Potensi Mantul"
        else:
            status_rsi = f"Wajar/Normal ({rsi_4h:.0f})"

        # --- LOGIKA BAHASA SIMPEL (SMC & TECH) ---
        if is_bullish_1d and is_bullish_4h:
            smc_kondisi = "Tren besar & kecil kompak NAIK. Bandar lagi dorong harga ke atas."
            smc_rekomendasi = "Sabar, tunggu harga agak diskon dikit turun dulu baru ikutan Beli."
            tech_kondisi = "Kondisi pasar lagi bagus dan stabil (Uptrend kuat)."
            tech_rekomendasi = "Aman buat Beli. Kalau tembus Atap 1, potensi lanjut naik tinggi."
            
        elif is_bullish_1d and not is_bullish_4h:
            smc_kondisi = "Tren besar masih NAIK, tapi jangka pendek lagi TURUN buat cari tenaga baru."
            smc_rekomendasi = "Jangan buru-buru! Tunggu ada tanda-tanda harga berhenti turun dan mulai mantul."
            tech_kondisi = "Harga lagi koreksi sehat (turun sementara uji ketahanan)."
            tech_rekomendasi = "Momen pas buat cicil Beli bertahap dekat area Lantai 1 / Lantai 2."
            
        elif not is_bullish_1d and not is_bullish_4h:
            smc_kondisi = "Pasar lagi lesu/rusak. Bandar masih cenderung jualan."
            smc_rekomendasi = "Jangan coba-coba melawan arus. Tahan diri dulu dari posisi Beli."
            tech_kondisi = "Tren TURUN dominan. Tekanan jual masih lumayan tinggi."
            tech_rekomendasi = "Wait & See (Nonton dulu). Hanya spekulasi beli kalau harga sudah murah banget."
            
        else: # 1D Bearish, 4H Bullish
            smc_kondisi = "Harga naik cuma buat 'napas' sebentar sebelum potensi lanjut turun lagi."
            smc_rekomendasi = "Waspada Jebakan Naik (Bull Trap)! Jangan tergiur beli di pucuk."
            tech_kondisi = "Pantulan harga sementara di tengah tren turun besar."
            tech_rekomendasi = "Kalau punya barang, manfaatkan kenaikan mendekati Atap 1 buat Take Profit / Jualan."

        # --- FORMATTING TEKS PARAGRAF AGAR PRESISI ---
        smc_k_formatted = rapihkan_teks("• Kondisi  : ", smc_kondisi)
        smc_r_formatted = rapihkan_teks("• Rekom    : ", smc_rekomendasi)
        
        tech_k_formatted = rapihkan_teks("• Kondisi  : ", tech_kondisi)
        tech_r_formatted = rapihkan_teks("• Rekom    : ", tech_rekomendasi)

        # --- FORMAT PESAN TELEGRAM ---
        bot = Bot(token=TOKEN)
        msg = (
            f"```text\n"
            f"🔍 [ANALISA PASAR] — {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Harga       : Rp {harga_sekarang:,.0f}\n"
            f"• Kondisi RSI : {status_rsi}\n"
            f"• Tren (4H)   : {tren_4h_teks}\n"
            f"{level_4h_teks}\n"
            f"• Tren (1D)   : {tren_1d_teks}\n"
            f"{level_1d_teks}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF BANDAR (SMC)\n"
            f"{smc_k_formatted}\n"
            f"{smc_r_formatted}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF TEKNIKAL (TECH)\n"
            f"{tech_k_formatted}\n"
            f"{tech_r_formatted}\n"
            f"```"
        )
        
        async def send():
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        
        asyncio.run(send())
        print(f"Sukses mengirim analisa {PAIR_NAME} ke Telegram.")

    except Exception as e:
        print(f"Error saat analisa {SYMBOL}: {e}")

if __name__ == '__main__':
    run_analysis()
