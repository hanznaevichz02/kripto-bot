"""
========================================================
   KRIPTO BOT — Analisa Mendalam Per-Koin (v6.0 Spot-Oriented)
   Fungsi : Trigger manual, input simbol bebas.
            Funding Rate & data futures HANYA untuk konteks
            analisa (bukan untuk eksekusi) — orientasi SPOT.
   Fix    : Symbol swap konsisten, ATR per-timeframe,
            CHoCH/BOS directional, FVG mitigasi,
            notif error, candle running detection,
            Momentum 4 fase (Rebound/Koreksi).
========================================================
"""

import os
import sys
import ccxt
import pandas as pd
import requests
from telegram import Bot
import asyncio
import textwrap
from datetime import datetime, timezone

# --- KONFIGURASI ---
TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Validasi Token di awal
if not TOKEN or not CHAT_ID:
    print("FATAL ERROR: TELEGRAM_TOKEN atau TELEGRAM_CHAT_ID tidak ditemukan.")
    sys.exit(1)

raw_symbol = os.getenv("INPUT_SYMBOL", "BTC/USDT").upper().strip()
SYMBOL_SPOT = raw_symbol if "/" in raw_symbol else f"{raw_symbol}/USDT"
SYMBOL_SWAP = SYMBOL_SPOT if ':' in SYMBOL_SPOT else f"{SYMBOL_SPOT}:USDT"

PAIR_NAME = SYMBOL_SPOT.replace('/', '-').replace('USDT', 'IDR')

# ============================================================
# HELPER
# ============================================================

def get_usd_idr() -> float:
    try:
        r = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        raw_idr = float(r.json()['ticker']['last'])
        
        # Kalibrasi selisih harga (spread) Pluang sekitar +0.42%
        PLUANG_MARGIN = 1.00488
        
        return raw_idr * PLUANG_MARGIN
    except Exception:
        # Terapkan juga margin pada harga fallback jika API Indodax error
        return 18000.0 * 1.00488

def rapihkan_teks(label: str, teks: str, width: int = 35) -> str:
    indent_spasi = " " * len(label)
    return textwrap.fill(teks, width=width, initial_indent=label, subsequent_indent=indent_spasi)

def is_candle_running(timeframe: str) -> tuple[bool, int]:
    now = datetime.now(timezone.utc)
    if timeframe == '1h':
        return True, now.minute
    if timeframe == '4h':
        jam_ke = now.hour % 4
        return True, jam_ke * 60 + now.minute
    if timeframe == '1d':
        return True, now.hour * 60 + now.minute
    return False, 0

def cek_fvg(df: pd.DataFrame, usd_idr: float):
    n = len(df)
    for i in range(n - 2, 2, -1):
        if df['low'].iloc[i] > df['high'].iloc[i - 2]:
            gap_bawah = float(df['high'].iloc[i - 2])
            gap_atas  = float(df['low'].iloc[i])
            sisa = df['low'].iloc[i + 1:]
            sudah_terisi = (sisa <= gap_bawah).any() if len(sisa) > 0 else False
            if not sudah_terisi:
                return "Bullish", gap_bawah * usd_idr, gap_atas * usd_idr

        if df['high'].iloc[i] < df['low'].iloc[i - 2]:
            gap_bawah = float(df['high'].iloc[i])
            gap_atas  = float(df['low'].iloc[i - 2])
            sisa = df['high'].iloc[i + 1:]
            sudah_terisi = (sisa >= gap_atas).any() if len(sisa) > 0 else False
            if not sudah_terisi:
                return "Bearish", gap_bawah * usd_idr, gap_atas * usd_idr
    return None, 0, 0

def hitung_skor_smc(choch, bos, mitigation, fvg, rrr, volume_spike):
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

async def kirim_pesan(bot: Bot, pesan: str):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
    except Exception as e:
        print(f"Gagal kirim notif: {e}")

# ============================================================
# MAIN ANALYSIS
# ============================================================

async def main_async():
    print(f"DEBUG: Analisa {SYMBOL_SPOT} (spot) | konteks futures: {SYMBOL_SWAP}")
    bot = Bot(token=TOKEN)
    
    exchange_spot = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    exchange_swap = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 30000})

    try:
        exchange_spot.load_markets()
    except Exception as e:
        await kirim_pesan(bot, f"⚠️ *Gagal Memuat Market*\nSymbol: `{SYMBOL_SPOT}`\nError: `{str(e)[:150]}`")
        return

    usd_idr = get_usd_idr()

    # --- FUNDING RATE ---
    fr_tersedia = True
    try:
        exchange_swap.load_markets()
        fr_data = exchange_swap.fetch_funding_rate(SYMBOL_SWAP)
        funding_rate = float(fr_data.get('fundingRate', 0.0) or 0.0)
    except Exception:
        fr_tersedia = False
        funding_rate = 0.0

    fr_persen = funding_rate * 100
    if not fr_tersedia:
        status_fr = "N/A (futures pair tidak tersedia)"
    elif fr_persen > 0.02:
        status_fr = f"{fr_persen:.4f}% (Long Overcrowded 🔥 — waspada koreksi)"
    elif fr_persen < -0.01:
        status_fr = f"{fr_persen:.4f}% (Short Overcrowded 💧 — potensi rebound)"
    else:
        status_fr = f"{fr_persen:.4f}% (Normal / Seimbang ⚖️)"

    try:
        # --- DATA SPOT ---
        bars_1h = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='1h', limit=50)
        bars_4h = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='4h', limit=50)
        bars_1d = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='1d', limit=30)

        if len(bars_1h) < 25 or len(bars_4h) < 25 or len(bars_1d) < 20:
            await kirim_pesan(bot, f"⚠️ *Data Tidak Cukup*\nSymbol: `{SYMBOL_SPOT}`\nKemungkinan koin baru listing.")
            return

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp','open','high','low','close','volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp','open','high','low','close','volume']).astype(float)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp','open','high','low','close','volume']).astype(float)

        harga_sekarang = float(df_1h['close'].iloc[-1] * usd_idr)

        # --- CANDLE RUNNING ---
        run_1h, waktu_1h = is_candle_running('1h')
        run_4h, waktu_4h = is_candle_running('4h')
        run_1d, waktu_1d = is_candle_running('1d')
        info_running = (f"• Candle 1H : berjalan {waktu_1h} mnt\n"
                        f"• Candle 4H : berjalan {waktu_4h} mnt\n"
                        f"• Candle 1D : berjalan {waktu_1d} mnt")
        candle_1h_dini = waktu_1h < 15

        # --- ATR & VOLUME ---
        df_1h['atr'] = (df_1h['high'] - df_1h['low']).rolling(14).mean()
        df_4h['atr'] = (df_4h['high'] - df_4h['low']).rolling(14).mean()
        df_1d['atr'] = (df_1d['high'] - df_1d['low']).rolling(14).mean()
        df_1h['avg_vol'] = df_1h['volume'].rolling(20).mean().shift(1)

        atr_1h_idr = float(df_1h['atr'].iloc[-1] * usd_idr)
        atr_4h_idr = float(df_4h['atr'].iloc[-1] * usd_idr)
        atr_1d_idr = float(df_1d['atr'].iloc[-1] * usd_idr)

        # --- PIVOT POINTS ---
        def pivot_levels(df):
            h, l, c = df['high'].iloc[-1], df['low'].iloc[-1], df['close'].iloc[-1]
            p = (h + l + c) / 3
            return {'p': p, 'r1': (2*p) - l, 'r2': p + (h - l), 's1': (2*p) - h, 's2': p - (h - l)}

        piv_1h, piv_4h, piv_1d = pivot_levels(df_1h), pivot_levels(df_4h), pivot_levels(df_1d)
        r1_1h_idr, s1_1h_idr = float(piv_1h['r1'] * usd_idr), float(piv_1h['s1'] * usd_idr)
        r1_4h_idr, r2_4h_idr = float(piv_4h['r1'] * usd_idr), float(piv_4h['r2'] * usd_idr)
        s1_4h_idr, s2_4h_idr = float(piv_4h['s1'] * usd_idr), float(piv_4h['s2'] * usd_idr)
        r1_1d_idr, r2_1d_idr = float(piv_1d['r1'] * usd_idr), float(piv_1d['r2'] * usd_idr)
        s1_1d_idr, s2_1d_idr = float(piv_1d['s1'] * usd_idr), float(piv_1d['s2'] * usd_idr)

        # --- TREND & DETEKSI PEMBELOKAN (REBOUND/KOREKSI) ---
        for df in (df_1h, df_4h, df_1d):
            df['ema9']  = df['close'].ewm(span=9,  adjust=False).mean()
            df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

        def baca_momentum(df):
            c = df['close'].iloc[-1]
            e9 = df['ema9'].iloc[-1]
            e21 = df['ema21'].iloc[-1]
            
            if c > e9 and e9 > e21:
                return True, "NAIK KOKOH 🟢"
            elif c > e9 and e9 <= e21:
                return True, "REBOUND ↗️" 
            elif c < e9 and e9 < e21:
                return False, "TURUN 🔴"
            elif c < e9 and e9 >= e21:
                return False, "KOREKSI ↘️" 
            else:
                return False, "SIDEWAYS ⚪"

        is_bullish_1h, tren_1h_teks = baca_momentum(df_1h)
        is_bullish_4h, tren_4h_teks = baca_momentum(df_4h)
        is_bullish_1d, tren_1d_teks = baca_momentum(df_1d)

        # --- SL/TP ---
        if is_bullish_1h:
            sl_1h_idr, tp_1h_idr = s1_1h_idr - (0.3 * atr_1h_idr), r1_1h_idr
        else:
            sl_1h_idr, tp_1h_idr = r1_1h_idr + (0.3 * atr_1h_idr), s1_1h_idr

        if is_bullish_4h:
            level_4h_teks = f"  Atap 1      : Rp {r1_4h_idr:,.0f}\n  Atap 2      : Rp {r2_4h_idr:,.0f}"
            sl_4h_idr, tp_4h_idr = s1_4h_idr - (0.5 * atr_4h_idr), r2_4h_idr
        else:
            level_4h_teks = f"  Lantai 1    : Rp {s1_4h_idr:,.0f}\n  Lantai 2    : Rp {s2_4h_idr:,.0f}"
            sl_4h_idr, tp_4h_idr = s2_4h_idr - (0.5 * atr_4h_idr), r1_4h_idr

        if is_bullish_1d:
            level_1d_teks = f"  Atap 1      : Rp {r1_1d_idr:,.0f}\n  Atap 2      : Rp {r2_1d_idr:,.0f}"
            sl_1d_idr, tp_1d_idr = s1_1d_idr - (1.0 * atr_1d_idr), r2_1d_idr
        else:
            level_1d_teks = f"  Lantai 1    : Rp {s1_1d_idr:,.0f}\n  Lantai 2    : Rp {s2_1d_idr:,.0f}"
            sl_1d_idr, tp_1d_idr = s2_1d_idr - (1.0 * atr_1d_idr), r1_1d_idr

        risk_1h = abs(harga_sekarang - sl_1h_idr); reward_1h = abs(tp_1h_idr - harga_sekarang)
        rrr_1h = (reward_1h / risk_1h) if risk_1h > 0 else 0.0
        risk_4h = abs(harga_sekarang - sl_4h_idr); reward_4h = abs(tp_4h_idr - harga_sekarang)
        rrr_4h = (reward_4h / risk_4h) if risk_4h > 0 else 0.0

        # --- RSI 4H ---
        delta = df_4h['close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        ema_up, ema_down = up.ewm(com=13, adjust=False).mean(), down.ewm(com=13, adjust=False).mean()
        rs = ema_up / ema_down
        df_4h['rsi'] = 100 - (100 / (1 + rs))
        rsi_4h = df_4h['rsi'].iloc[-1]

        if rsi_4h >= 70:
            status_rsi = f"Kekenyangan ({rsi_4h:.0f}) - Rawan Turun"
        elif rsi_4h <= 30:
            status_rsi = f"Kebanting ({rsi_4h:.0f}) - Potensi Mantul"
        else:
            status_rsi = f"Wajar/Normal ({rsi_4h:.0f})"

        # --- DIVERGENCE RSI 4H ---
        price_hh = df_4h['close'].iloc[-1] > df_4h['close'].iloc[-11:-1].max()
        rsi_lh   = df_4h['rsi'].iloc[-1]  < df_4h['rsi'].iloc[-11:-1].max()
        price_ll = df_4h['close'].iloc[-1] < df_4h['close'].iloc[-11:-1].min()
        rsi_hl   = df_4h['rsi'].iloc[-1]  > df_4h['rsi'].iloc[-11:-1].min()

        divergence_teks = "Tidak Terdeteksi"
        if price_hh and rsi_lh:
            divergence_teks = "⚠️ Bearish (harga naik, RSI melemah)"
        elif price_ll and rsi_hl:
            divergence_teks = "✅ Bullish (harga turun, RSI menguat)"

        # --- CHoCH & BOS ---
        curr_1h_live = df_1h.iloc[-1]
        vol_spike = bool(curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.8))

        if is_bullish_4h:
            choch = bool(curr_1h_live['close'] > curr_1h_live['open'] and curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.5))
            bos   = bool(curr_1h_live['close'] > df_1h['high'].iloc[-6:-1].max())
            mitigation = bool((df_4h['low'].iloc[-1] * usd_idr) <= (s1_4h_idr * 1.005))
        else:
            choch = bool(curr_1h_live['close'] < curr_1h_live['open'] and curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.5))
            bos   = bool(curr_1h_live['close'] < df_1h['low'].iloc[-6:-1].min())
            mitigation = bool((df_4h['high'].iloc[-1] * usd_idr) >= (r1_4h_idr * 0.995))

        fvg_type, fvg_min, fvg_max = cek_fvg(df_1h, usd_idr)
        if fvg_type == "Bullish":
            fvg_active, fvg_teks_status = True, f"Bullish 🟢 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        elif fvg_type == "Bearish":
            fvg_active, fvg_teks_status = True, f"Bearish 🔴 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        else:
            fvg_active, fvg_teks_status = False, "Tidak Ada / Sudah Termitigasi ❌"

        skor_smc, breakdown_skor = hitung_skor_smc(choch, bos, mitigation, fvg_active, rrr_4h, vol_spike)
        label_skor = "🔥 HIGH" if skor_smc >= 80 else ("🎯 POTENSIAL" if skor_smc >= 60 else "⚠️ STANDAR")

        peringatan_dini = f"\n⏳ *Catatan:* Candle 1H baru berjalan {waktu_1h} menit — sinyal 1H masih bisa berubah.\n" if candle_1h_dini else ""

        # --- TEKS PERSPEKTIF ---
        smc_1h_k = f"Struktur mikro 1H {tren_1h_teks}, momentum scalping aktif." if is_bullish_1h else f"Tekanan jual 1H ({tren_1h_teks}) mendominasi area mikro."
        smc_1h_r = "Potensi dorongan cepat ke resistance terdekat." if is_bullish_1h else "Waspada koreksi cepat, utamakan scalping pendek."
        smc_4h_k = f"Tren 4H {tren_4h_teks}. Live Price merespons area support/resistance." if is_bullish_4h else f"Tren 4H {tren_4h_teks}. Tekanan jual terasa, Skor Setup ({skor_smc}/100)."
        smc_4h_r = "Lanjut dorongan naik bertahap menuju target TP." if is_bullish_4h else "Wait & See dulu. Tunggu pantulan aman dekat Lantai 1 4H."
        smc_1d_k = f"Tren makro 1D {tren_1d_teks}. Struktur makro sehat." if is_bullish_1d else f"Tren makro 1D {tren_1d_teks}. Bandar makro cenderung distribusi."
        smc_1d_r = "Bagus untuk posisi Swing (Spot)." if is_bullish_1d else "Hindari all-in. Cicil beli bertahap (DCA) lebih aman di spot."

        # Pemformatan
        smc_1h_k_fmt  = rapihkan_teks("• Kondisi   : ", smc_1h_k)
        smc_1h_r_fmt  = rapihkan_teks("• Rekom     : ", smc_1h_r)
        smc_1h_sl_fmt = rapihkan_teks("• Target SL : ", f"Rp {sl_1h_idr:,.0f}")
        smc_1h_tp_fmt = rapihkan_teks("• Target TP : ", f"Rp {tp_1h_idr:,.0f} (RRR 1:{rrr_1h:.2f})")
        smc_4h_k_fmt  = rapihkan_teks("• Kondisi   : ", smc_4h_k)
        smc_4h_r_fmt  = rapihkan_teks("• Rekom     : ", smc_4h_r)
        smc_4h_sl_fmt = rapihkan_teks("• Target SL : ", f"Rp {sl_4h_idr:,.0f}")
        smc_4h_tp_fmt = rapihkan_teks("• Target TP : ", f"Rp {tp_4h_idr:,.0f} (RRR 1:{rrr_4h:.2f})")
        smc_1d_k_fmt  = rapihkan_teks("• Kondisi   : ", smc_1d_k)
        smc_1d_r_fmt  = rapihkan_teks("• Rekom     : ", smc_1d_r)
        smc_1d_sl_fmt = rapihkan_teks("• Target SL : ", f"Rp {sl_1d_idr:,.0f}")
        smc_1d_tp_fmt = rapihkan_teks("• Target TP : ", f"Rp {tp_1d_idr:,.0f}")

        breakdown_str = "\n".join(breakdown_skor)

        msg = (
            f"```text\n"
            f"🔍 [ANALISA SPOT] — {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Harga       : Rp {harga_sekarang:,.0f}\n"
            f"• Funding*    : {status_fr}\n"
            f"• Kondisi FVG : {fvg_teks_status}\n"
            f"• Kondisi RSI : {status_rsi}\n"
            f"• Divergence  : {divergence_teks}\n"
            f"• Skor Setup  : {skor_smc}/100 ({label_skor})\n"
            f"• Est. RRR(4H): 1 : {rrr_4h:.2f}\n"
            f"----------------------------------\n"
            f"{info_running}\n"
            f"----------------------------------\n"
            f"• Tren (1H)   : {tren_1h_teks}\n"
            f"• Tren (4H)   : {tren_4h_teks}\n"
            f"{level_4h_teks}\n"
            f"• Tren (1D)   : {tren_1d_teks}\n"
            f"{level_1d_teks}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF 1H (SCALPING / MIKRO)\n"
            f"{smc_1h_k_fmt}\n"
            f"{smc_1h_r_fmt}\n"
            f"{smc_1h_sl_fmt}\n"
            f"{smc_1h_tp_fmt}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF 4H (JANGKA PENDEK)\n"
            f"{smc_4h_k_fmt}\n"
            f"{smc_4h_r_fmt}\n"
            f"{smc_4h_sl_fmt}\n"
            f"{smc_4h_tp_fmt}\n"
            f"----------------------------------\n"
            f"📋 PERSPEKTIF 1D (SWING / SPOT)\n"
            f"{smc_1d_k_fmt}\n"
            f"{smc_1d_r_fmt}\n"
            f"{smc_1d_sl_fmt}\n"
            f"{smc_1d_tp_fmt}\n"
            f"----------------------------------\n"
            f"📋 RINCIAN SKOR SETUP (SMC + FVG)\n"
            f"{breakdown_str}\n"
            f"```"
            f"{peringatan_dini}"
            f"\n_*Funding rate dari pasar Futures — hanya konteks sentimen,_\n"
            f"_tidak mempengaruhi keputusan (analisa murni Spot)._"
        )

        await kirim_pesan(bot, msg)
        print(f"Sukses mengirim analisa {PAIR_NAME} ke Telegram.")

    except Exception as e:
        print(f"Error saat analisa {SYMBOL_SPOT}: {e}")
        await kirim_pesan(bot, f"⚠️ *Gagal Analisa* `{PAIR_NAME}`\nError: `{str(e)[:200]}`")

def run_analysis():
    asyncio.run(main_async())

if __name__ == '__main__':
    run_analysis()
