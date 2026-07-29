"""
========================================================
    KRIPTO BOT — Analisa Simpel, Dinamis & Presisi Layout (v5.2 FVG & Funding Rate)
    Fungsi  : Integrasi Skor SMC Kuantitatif, ATR Buffer, Volume Spike, FVG, Funding Rate & Presisi Risk Mgt
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
TOKEN   = os.getenv("TELEGRAM_TOKEN")
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
    """Memotong teks panjang agar ter-indentasi rapi tepat di bawah titik dua label"""
    indent_spasi = " " * len(label)
    return textwrap.fill(teks, width=width, initial_indent=label, subsequent_indent=indent_spasi)

def cek_fvg(df, usd_idr):
    """Mendeteksi Fair Value Gap (FVG) beserta arah dan rentang harganya dalam Rupiah"""
    for i in range(len(df) - 1, 2, -1):
        # Bullish FVG: Low candle saat ini > High candle 2 periode sebelumnya
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            p_bawah = float(df['high'].iloc[i-2] * usd_idr)
            p_atas = float(df['low'].iloc[i] * usd_idr)
            return "Bullish", p_bawah, p_atas
            
        # Bearish FVG: High candle saat ini < Low candle 2 periode sebelumnya
        if df['high'].iloc[i] < df['low'].iloc[i-2]:
            p_bawah = float(df['high'].iloc[i] * usd_idr)
            p_atas = float(df['low'].iloc[i-2] * usd_idr)
            return "Bearish", p_bawah, p_atas
            
    return None, 0, 0

def hitung_skor_smc(choch: bool, bos: bool, mitigation: bool, fvg: bool, rrr: float, volume_spike: bool):
    """Menghitung Skor Setup SMC kuantitatif (0-100) dengan tambahan FVG"""
    score = 0
    breakdown = []
    
    if choch:
        score += 30
        breakdown.append("• Konfirmasi CHoCH Valid (+30)")
    else:
        breakdown.append("• Tanpa CHoCH (+0)")
        
    if bos:
        score += 20
        breakdown.append("• Struktur BOS Terbentuk (+20)")
    else:
        breakdown.append("• Tanpa BOS (+0)")
        
    if mitigation:
        score += 15
        breakdown.append("• Area Mitigasi OB Tersentuh (+15)")
    else:
        breakdown.append("• Belum Menyentuh OB (+0)")

    if fvg:
        score += 15
        breakdown.append("• Area FVG Valid Terbentuk (+15)")
    else:
        breakdown.append("• Tanpa FVG Aktif (+0)")
        
    if volume_spike:
        score += 10
        breakdown.append("• Lonjakan Volume (+10)")
    else:
        breakdown.append("• Volume Standar (+0)")
        
    if rrr >= 2.0:
        score += 10
        breakdown.append(f"• RRR Ideal ({rrr:.2f} >= 2.0) (+10)")
    else:
        breakdown.append(f"• RRR Cukup ({rrr:.2f} < 2.0) (+0)")
        
    return min(score, 100), breakdown

def run_analysis():
    print(f"DEBUG: Memulai analisa hybrid SMC futures untuk {SYMBOL}...")
    # Menggunakan defaultType 'swap' untuk akses data Futures & Funding Rate
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 30000})
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market: {e}")
        return

    usd_idr = get_usd_idr()

    # --- AMBIL FUNDING RATE FUTURES ---
    funding_rate = 0.0
    try:
        target_swap_symbol = SYMBOL if ':' in SYMBOL else f"{SYMBOL}:USDT"
        fr_data = exchange.fetch_funding_rate(target_swap_symbol)
        funding_rate = float(fr_data.get('fundingRate', 0.0) or 0.0)
    except Exception:
        try:
            fr_data = exchange.fetch_funding_rate(SYMBOL)
            funding_rate = float(fr_data.get('fundingRate', 0.0) or 0.0)
        except Exception:
            funding_rate = 0.0

    fr_persen = funding_rate * 100
    if fr_persen > 0.02:
        status_fr = f"{fr_persen:.4f}% (Long Overcrowded 🔥)"
    elif fr_persen < -0.01:
        status_fr = f"{fr_persen:.4f}% (Short Overcrowded 💧)"
    else:
        status_fr = f"{fr_persen:.4f}% (Normal / Seimbang ⚖️)"

    try:
        # Menarik data 1H, 4H, dan 1D
        bars_1h = exchange.fetch_ohlcv(SYMBOL, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(SYMBOL, timeframe='4h', limit=50)
        bars_1d = exchange.fetch_ohlcv(SYMBOL, timeframe='1d', limit=30)

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)

        # Harga saat ini (Update realtime)
        harga_sekarang = float(df_4h['close'].iloc[-1] * usd_idr)

        # --- HITUNG ATR 1H UNTUK BUFFER SL/TP DINAMIS ---
        df_1h['atr'] = (df_1h['high'] - df_1h['low']).rolling(14).mean()
        df_1h['avg_vol'] = df_1h['volume'].rolling(20).mean().shift(1)
        
        curr_1h = df_1h.iloc[-2]
        atr_idr = float(curr_1h['atr'] * usd_idr)

        # --- PIVOT POINTS 4H ---
        curr_4h = -2
        high_4h, low_4h, close_4h = df_4h['high'].iloc[curr_4h], df_4h['low'].iloc[curr_4h], df_4h['close'].iloc[curr_4h]
        pivot_4h = (high_4h + low_4h + close_4h) / 3
        r1_4h_idr = float(((2 * pivot_4h) - low_4h) * usd_idr)
        r2_4h_idr = float((pivot_4h + (high_4h - low_4h)) * usd_idr)
        s1_4h_idr = float(((2 * pivot_4h) - high_4h) * usd_idr)
        s2_4h_idr = float((pivot_4h - (high_4h - low_4h)) * usd_idr)

        # --- PIVOT POINTS 1D ---
        curr_1d = -2 
        high_1d, low_1d, close_1d = df_1d['high'].iloc[curr_1d], df_1d['low'].iloc[curr_1d], df_1d['close'].iloc[curr_1d]
        pivot_1d = (high_1d + low_1d + close_1d) / 3
        r1_1d_idr = float(((2 * pivot_1d) - low_1d) * usd_idr)
        r2_1d_idr = float((pivot_1d + (high_1d - low_1d)) * usd_idr)
        s1_1d_idr = float(((2 * pivot_1d) - high_1d) * usd_idr)
        s2_1d_idr = float((pivot_1d - (high_1d - low_1d)) * usd_idr)

        # --- INDIKATOR TREN (EMA 9 & 21) ---
        df_4h['ema9'] = df_4h['close'].ewm(span=9, adjust=False).mean()
        df_4h['ema21'] = df_4h['close'].ewm(span=21, adjust=False).mean()
        df_1d['ema9'] = df_1d['close'].ewm(span=9, adjust=False).mean()
        df_1d['ema21'] = df_1d['close'].ewm(span=21, adjust=False).mean()

        is_bullish_4h = df_4h['ema9'].iloc[-1] > df_4h['ema21'].iloc[-1]
        is_bullish_1d = df_1d['ema9'].iloc[-1] > df_1d['ema21'].iloc[-1]

        tren_4h_teks = "NAIK 🟢" if is_bullish_4h else "TURUN 🔴"
        tren_1d_teks = "NAIK 🟢" if is_bullish_1d else "TURUN 🔴"

        # Tampilan Level Presisi
        if is_bullish_4h:
            level_4h_teks = f"  Atap 1      : Rp {r1_4h_idr:,.0f}\n  Atap 2      : Rp {r2_4h_idr:,.0f}"
            sl_4h_idr = s1_4h_idr - (0.5 * atr_idr)
            tp_4h_idr = r2_4h_idr
        else:
            level_4h_teks = f"  Lantai 1    : Rp {s1_4h_idr:,.0f}\n  Lantai 2    : Rp {s2_4h_idr:,.0f}"
            sl_4h_idr = s2_4h_idr - (0.5 * atr_idr)
            tp_4h_idr = r1_4h_idr

        if is_bullish_1d:
            level_1d_teks = f"  Atap 1      : Rp {r1_1d_idr:,.0f}\n  Atap 2      : Rp {r2_1d_idr:,.0f}"
            sl_1d_idr = s1_1d_idr - (1.0 * atr_idr)
            tp_1d_idr = r2_1d_idr
        else:
            level_1d_teks = f"  Lantai 1    : Rp {s1_1d_idr:,.0f}\n  Lantai 2    : Rp {s2_1d_idr:,.0f}"
            sl_1d_idr = s2_1d_idr - (1.0 * atr_idr)
            tp_1d_idr = r1_1d_idr

        # --- HITUNG ESTIMASI RRR ---
        risk_4h = abs(harga_sekarang - sl_4h_idr)
        reward_4h = abs(tp_4h_idr - harga_sekarang)
        rrr_4h = (reward_4h / risk_4h) if risk_4h > 0 else 0.0

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

        # --- PENDETEKSI LOGIKA KUANTITATIF SMC & FVG ---
        choch = bool(curr_1h['close'] > curr_1h['open'] and curr_1h['volume'] > (df_1h['avg_vol'].iloc[-2] * 1.5))
        bos = bool(curr_1h['close'] > df_1h['high'].iloc[-5:-2].max())
        mitigation = bool((df_4h['low'].iloc[-1] * usd_idr) <= (s1_4h_idr * 1.005))
        vol_spike = bool(curr_1h['volume'] > (df_1h['avg_vol'].iloc[-2] * 1.8))
        
        fvg_type, fvg_min, fvg_max = cek_fvg(df_1h, usd_idr)
        if fvg_type == "Bullish":
            fvg_active = True
            fvg_teks_status = f"Bullish 🟢 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        elif fvg_type == "Bearish":
            fvg_active = True
            fvg_teks_status = f"Bearish 🔴 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        else:
            fvg_active = False
            fvg_teks_status = "Tidak Ada / Tertutup ❌"

        skor_smc, breakdown_skor = hitung_skor_smc(choch, bos, mitigation, fvg_active, rrr_4h, vol_spike)
        label_skor = "🔥 HIGH" if skor_smc >= 80 else ("🎯 POTENSIAL" if skor_smc >= 60 else "⚠️ STANDAR")

        # --- LOGIKA TEKS SMC 4H ---
        if is_bullish_4h:
            smc_4h_k = f"Tren 4H NAIK. Skor Setup ({skor_smc}/100) mengonfirmasi dorongan."
            smc_4h_r = "Beli bertahap saat koreksi tipis di area Lantai 1 4H."
        else:
            smc_4h_k = f"Tren 4H TURUN. Tekanan jual terasa, Skor Setup ({skor_smc}/100)."
            smc_4h_r = "Wait & See dulu. Tunggu pantulan aman dekat Lantai 1 4H."

        # --- LOGIKA TEKS SMC 1D ---
        if is_bullish_1d:
            smc_1d_k = "Tren makro 1D NAIK kuat. Bandar makro menjaga harga."
            smc_1d_r = "Bagus untuk posisi Swing. Struktur makro sangat sehat."
        else:
            smc_1d_k = "Tren makro 1D TURUN. Bandar makro cenderung distribusi/jual."
            smc_1d_r = "Hindari hold terlalu lama. Utamakan quick trade saja."

        # --- FORMATTING PARAGRAF RAPI (TITIK DUA SEJAJAR DI KARAKTER 13) ---
        smc_4h_k_fmt  = rapihkan_teks("• Kondisi   : ", smc_4h_k)
        smc_4h_r_fmt  = rapihkan_teks("• Rekom     : ", smc_4h_r)
        smc_4h_sl_fmt = rapihkan_teks("• Target SL : ", f"Rp {sl_4h_idr:,.0f}")
        smc_4h_tp_fmt = rapihkan_teks("• Target TP : ", f"Rp {tp_4h_idr:,.0f} (RRR 1:{rrr_4h:.2f})")

        smc_1d_k_fmt  = rapihkan_teks("• Kondisi   : ", smc_1d_k)
        smc_1d_r_fmt  = rapihkan_teks("• Rekom     : ", smc_1d_r)
        smc_1d_sl_fmt = rapihkan_teks("• Target SL : ", f"Rp {sl_1d_idr:,.0f}")
        smc_1d_tp_fmt = rapihkan_teks("• Target TP : ", f"Rp {tp_1d_idr:,.0f}")

        breakdown_str = "\n".join(breakdown_skor)

        # --- FORMAT PESAN TELEGRAM ---
        bot = Bot(token=TOKEN)
        msg = (
            f"```text\n"
            f"🔍 [ANALISA PASAR] — {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Harga       : Rp {harga_sekarang:,.0f}\n"
            f"• Funding Rate: {status_fr}\n"
            f"• Kondisi FVG : {fvg_teks_status}\n"
            f"• Kondisi RSI : {status_rsi}\n"
            f"• Skor Setup  : {skor_smc}/100 ({label_skor})\n"
            f"• Est. RRR    : 1 : {rrr_4h:.2f}\n"
            f"----------------------------------\n"
            f"• Tren (4H)   : {tren_4h_teks}\n"
            f"{level_4h_teks}\n"
            f"• Tren (1D)   : {tren_1d_teks}\n"
            f"{level_1d_teks}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF SMC 4H (JANGKA PENDEK)\n"
            f"{smc_4h_k_fmt}\n"
            f"{smc_4h_r_fmt}\n"
            f"{smc_4h_sl_fmt}\n"
            f"{smc_4h_tp_fmt}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF SMC 1D (JANGKA PANJANG)\n"
            f"{smc_1d_k_fmt}\n"
            f"{smc_1d_r_fmt}\n"
            f"{smc_1d_sl_fmt}\n"
            f"{smc_1d_tp_fmt}\n"
            f"----------------------------------\n"
            f"📋 RINCIAN SKOR SETUP (SMC + FVG)\n"
            f"{breakdown_str}\n"
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
