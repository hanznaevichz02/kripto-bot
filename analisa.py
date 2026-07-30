"""
========================================================
    KRIPTO BOT — Analisa Mendalam Per-Koin (v6.1 Spot-Oriented + 1H EMA9 Break)
    Fungsi : Trigger manual, input simbol bebas.
             Funding Rate & data futures HANYA untuk konteks
             analisa (bukan untuk eksekusi) — orientasi SPOT.
    Fix    : Symbol swap konsisten, ATR per-timeframe,
             CHoCH/BOS directional, FVG mitigasi,
             notif error, candle running detection,
             Momentum 4 fase (Rebound/Koreksi) + Deteksi Patahan EMA 9 1H.
========================================================
"""

import asyncio
from datetime import datetime, timezone
import os
import sys
import ccxt
import pandas as pd
import requests
from telegram import Bot
import textwrap

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Validasi Token di awal
if not TOKEN or not CHAT_ID:
    print("FATAL ERROR: TELEGRAM_TOKEN atau TELEGRAM_CHAT_ID tidak ditemukan.")
    sys.exit(1)

raw_symbol = os.getenv("INPUT_SYMBOL", "BTC/USDT").upper().strip()
SYMBOL_SPOT = raw_symbol if "/" in raw_symbol else f"{raw_symbol}/USDT"
SYMBOL_SWAP = SYMBOL_SPOT if ":" in SYMBOL_SPOT else f"{SYMBOL_SPOT}:USDT"

PAIR_NAME = SYMBOL_SPOT.replace("/", "-").replace("USDT", "IDR")

# ============================================================
# HELPER
# ============================================================

def get_usd_idr() -> float:
    try:
        r = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        raw_idr = float(r.json()["ticker"]["last"])
        
        # Kalibrasi spread Pluang agar hampir mendekati harga Pluang
        PLUANG_MARGIN = 1.0052
        
        return raw_idr * PLUANG_MARGIN
    except Exception:
        return 18000.0 * 1.0052

def rapihkan_teks(label: str, teks: str, width: int = 35) -> str:
    indent_spasi = " " * len(label)
    return textwrap.fill(teks, width=width, initial_indent=label, subsequent_indent=indent_spasi)

def is_candle_running(timeframe: str) -> tuple[bool, int]:
    now = datetime.now(timezone.utc)
    if timeframe == "1h":
        return True, now.minute
    if timeframe == "4h":
        jam_ke = now.hour % 4
        return True, jam_ke * 60 + now.minute
    if timeframe == "1d":
        return True, now.hour * 60 + now.minute
    return False, 0

def hitung_true_range_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=window).mean()

def cek_fvg(df: pd.DataFrame, usd_idr: float):
    n = len(df)
    for i in range(n - 2, 2, -1):
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            gap_bawah = float(df["high"].iloc[i - 2])
            gap_atas = float(df["low"].iloc[i])
            sisa = df["low"].iloc[i + 1:]
            sudah_terisi = (sisa <= gap_bawah).any() if len(sisa) > 0 else False
            if not sudah_terisi:
                return "Bullish", gap_bawah * usd_idr, gap_atas * usd_idr
                
        if df["high"].iloc[i] < df["low"].iloc[i - 2]:
            gap_bawah = float(df["high"].iloc[i])
            gap_atas = float(df["low"].iloc[i - 2])
            sisa = df["high"].iloc[i + 1:]
            sudah_terisi = (sisa >= gap_atas).any() if len(sisa) > 0 else False
            if not sudah_terisi:
                return "Bearish", gap_bawah * usd_idr, gap_atas * usd_idr
    return None, 0, 0

def hitung_skor_smc(choch, bos, mitigation, fvg, rrr, volume_spike, ema9_break_bull, ema9_break_bear):
    score = 0
    breakdown = []
    if choch:
        score += 25
        breakdown.append("- Konfirmasi CHoCH Valid (+25)")
    else:
        breakdown.append("- Tanpa CHoCH (+0)")
        
    if bos:
        score += 20
        breakdown.append("- Struktur BOS Terbentuk (+20)")
    else:
        breakdown.append("- Tanpa BOS (+0)")
        
    if mitigation:
        score += 15
        breakdown.append("- Area Mitigasi OB Tersentuh (+15)")
    else:
        breakdown.append("- Belum Menyentuh OB (+0)")
        
    if fvg:
        score += 15
        breakdown.append("- Area FVG Valid Terbentuk (+15)")
    else:
        breakdown.append("- Tanpa FVG Aktif (+0)")
        
    if volume_spike:
        score += 10
        breakdown.append("- Lonjakan Volume (+10)")
    else:
        breakdown.append("- Volume Standar (+0)")
        
    if ema9_break_bull or ema9_break_bear:
        score += 10
        breakdown.append("- Patahan EMA 9 (1H) Terdeteksi (+10)")
    else:
        breakdown.append("- Tidak Ada Patahan EMA 9 (+0)")
        
    if rrr >= 2.0:
        score += 5
        breakdown.append(f"- RRR Ideal ({rrr:.2f} >= 2.0) (+5)")
    else:
        breakdown.append(f"- RRR Cukup ({rrr:.2f} < 2.0) (+0)")
        
    return min(score, 100), breakdown

async def kirim_pesan(bot: Bot, pesan: str):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode="Markdown")
    except Exception as e:
        print(f"Gagal kirim notif: {e}")

# ============================================================
# MAIN ANALYSIS
# ============================================================

async def main_async():
    print(f"DEBUG: Analisa {SYMBOL_SPOT} (spot) | konteks futures: {SYMBOL_SWAP}")
    bot = Bot(token=TOKEN)

    exchange_spot = ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'timeout': 30000
    })
    exchange_swap = ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
        'timeout': 30000
    })

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
        status_fr = f"{fr_persen:.4f}% (Long Membludak 🔥 — waspada koreksi)"
    elif fr_persen < -0.01:
        status_fr = f"{fr_persen:.4f}% (Short Membludak 💧 — potensi rebound)"
    else:
        status_fr = f"{fr_persen:.4f}% (Seimbang ⚖️)"

    try:
        # --- DATA SPOT ---
        bars_1h = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='1h', limit=50)
        bars_4h = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='4h', limit=50)
        bars_1d = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='1d', limit=30)

        if len(bars_1h) < 25 or len(bars_4h) < 25 or len(bars_1d) < 20:
            await kirim_pesan(bot, f"⚠️ *Data Tidak Cukup*\nSymbol: `{SYMBOL_SPOT}`\nKemungkinan koin baru listing.")
            return

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)

        harga_sekarang = float(df_1h['close'].iloc[-1] * usd_idr)

        # --- ATR & VOLUME (Menggunakan True Range) ---
        df_1h['atr'] = hitung_true_range_atr(df_1h, 14)
        df_4h['atr'] = hitung_true_range_atr(df_4h, 14)
        df_1d['atr'] = hitung_true_range_atr(df_1d, 14)
        df_1h['avg_vol'] = df_1h['volume'].rolling(20).mean().shift(1)

        atr_1h_idr = float(df_1h['atr'].iloc[-1] * usd_idr)
        atr_4h_idr = float(df_4h['atr'].iloc[-1] * usd_idr)
        atr_1d_idr = float(df_1d['atr'].iloc[-1] * usd_idr)

        # --- PIVOT POINTS ---
        def pivot_levels(df):
            h, l, c = df['high'].iloc[-1], df['low'].iloc[-1], df['close'].iloc[-1]
            p = (h + l + c) / 3
            return {
                'p': p,
                'r1': (2 * p) - l,
                'r2': p + (h - l),
                's1': (2 * p) - h,
                's2': p - (h - l)
            }

        piv_1h, piv_4h, piv_1d = pivot_levels(df_1h), pivot_levels(df_4h), pivot_levels(df_1d)
        r1_1h_idr, s1_1h_idr = float(piv_1h['r1'] * usd_idr), float(piv_1h['s1'] * usd_idr)
        r1_4h_idr, r2_4h_idr = float(piv_4h['r1'] * usd_idr), float(piv_4h['r2'] * usd_idr)
        s1_4h_idr, s2_4h_idr = float(piv_4h['s1'] * usd_idr), float(piv_4h['s2'] * usd_idr)
        r1_1d_idr, r2_1d_idr = float(piv_1d['r1'] * usd_idr), float(piv_1d['r2'] * usd_idr)
        s1_1d_idr, s2_1d_idr = float(piv_1d['s1'] * usd_idr), float(piv_1d['s2'] * usd_idr)

        # --- TREND & INDIKATOR ---
        for df in (df_1h, df_4h, df_1d):
            df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
            df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

        def baca_momentum(df):
            c = df['close'].iloc[-1]
            e9 = df['ema9'].iloc[-1]
            e21 = df['ema21'].iloc[-1]

            if c > e9 and e9 > e21:
                return True, "NAIK 🟢"
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

        # --- DETEKSI PATAHAN EMA 9 (1H) ---
        ema9_break_bull = bool(
            (df_1h['close'].iloc[-2] <= df_1h['ema9'].iloc[-2]) and 
            (df_1h['close'].iloc[-1] > df_1h['ema9'].iloc[-1])
        )
        ema9_break_bear = bool(
            (df_1h['close'].iloc[-2] >= df_1h['ema9'].iloc[-2]) and 
            (df_1h['close'].iloc[-1] < df_1h['ema9'].iloc[-1])
        )

        if ema9_break_bull:
            patahan_ema9_teks = "🚀 Patahan Bullish (Harga jebol EMA9 ke Atas)"
        elif ema9_break_bear:
            patahan_ema9_teks = "⚠️ Patahan Bearish (Harga tembus EMA9 ke Bawah)"
        else:
            patahan_ema9_teks = "Tidak Ada Patahan Baru (Posisi Stabil)"

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

        risk_4h = abs(harga_sekarang - sl_4h_idr)
        reward_4h = abs(tp_4h_idr - harga_sekarang)
        rrr_4h = (reward_4h / risk_4h) if risk_4h > 0 else 0.0

        # --- RSI 4H ---
        delta = df_4h['close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        ema_up, ema_down = up.ewm(com=13, adjust=False).mean(), down.ewm(com=13, adjust=False).mean()
        rs = ema_up / ema_down
        df_4h['rsi'] = 100 - (100 / (1 + rs))
        rsi_4h = df_4h['rsi'].iloc[-1]

        if rsi_4h >= 70:
            status_rsi = f"Normal ({rsi_4h:.0f}) - Rawan Turun"
        elif rsi_4h <= 30:
            status_rsi = f"Normal ({rsi_4h:.0f}) - Potensi Mantul"
        else:
            status_rsi = f"Normal ({rsi_4h:.0f})"

        # --- DIVERGENCE RSI 4H ---
        price_hh = df_4h['close'].iloc[-1] > df_4h['close'].iloc[-11:-1].max()
        rsi_lh = df_4h['rsi'].iloc[-1] < df_4h['rsi'].iloc[-11:-1].max()
        price_ll = df_4h['close'].iloc[-1] < df_4h['close'].iloc[-11:-1].min()
        rsi_hl = df_4h['rsi'].iloc[-1] > df_4h['rsi'].iloc[-11:-1].min()

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
            bos = bool(curr_1h_live['close'] > df_1h['high'].iloc[-6:-1].max())
            mitigation = bool((df_4h['low'].iloc[-1] * usd_idr) <= (s1_4h_idr * 1.005))
        else:
            choch = bool(curr_1h_live['close'] < curr_1h_live['open'] and curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.5))
            bos = bool(curr_1h_live['close'] < df_1h['low'].iloc[-6:-1].min())
            mitigation = bool((df_4h['high'].iloc[-1] * usd_idr) >= (r1_4h_idr * 0.995))

        fvg_type, fvg_min, fvg_max = cek_fvg(df_1h, usd_idr)
        if fvg_type == "Bullish":
            fvg_active, fvg_teks_status = True, f"BULLISH 🟢 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        elif fvg_type == "Bearish":
            fvg_active, fvg_teks_status = True, f"BEARISH 🔴 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        else:
            fvg_active, fvg_teks_status = False, "Tidak Ada / Sudah Termitigasi ❌"

        # --- HITUNG SKOR SMC & PENYESUAIAN FASE MOMENTUM ---
        skor_smc, breakdown_skor = hitung_skor_smc(
            choch, bos, mitigation, fvg_active, rrr_4h, vol_spike, ema9_break_bull, ema9_break_bear
        )

        fase_bonus = {
            "NAIK KOKOH 🟢": 5,
            "REBOUND ↗️": -5,
            "TURUN 🔴": 0,
            "KOREKSI ↘️": 0,
            "SIDEWAYS ⚪": -10
        }
        bonus_nilai = fase_bonus.get(tren_4h_teks, 0)
        if bonus_nilai != 0:
            skor_smc = max(0, min(skor_smc + bonus_nilai, 100))
            tanda = "+" if bonus_nilai > 0 else ""
            breakdown_skor.append(f"- Penyesuaian Fase ({tanda}{bonus_nilai})")

        label_skor = "🔥 SANGAT KUAT" if skor_smc >= 70 else ("✅ POTENSIAL" if skor_smc >= 50 else "⚠️ STANDAR")
        breakdown_str = "\n".join(breakdown_skor)

        # --- FORMAT UTAMA ---
        msg = (
            f"```text\n"
            f"🔍 {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Harga     : Rp {harga_sekarang:,.0f}\n"
            f"----------------------------------\n"
            f"• Future    : {status_fr}\n"
            f"• FVG       : {fvg_teks_status}\n"
            f"• RSI       : {status_rsi}\n"
            f"• Divergence: {divergence_teks}\n"
            f"• Patahan 9 : {patahan_ema9_teks}\n"
            f"• Setup     : {skor_smc}/100 ({label_skor})\n"
            f"• RRR(4H)   : 1 : {rrr_4h:.2f}\n"
            f"----------------------------------\n"
            f"• Tren (1H)   : {tren_1h_teks}\n"
            f"• Tren (4H)   : {tren_4h_teks}\n"
            f"{level_4h_teks}\n"
            f"• Tren (1D)   : {tren_1d_teks}\n"
            f"{level_1d_teks}\n"
            f"----------------------------------\n"
            f"• Target TP : Rp {tp_4h_idr:,.0f}\n"
            f"• Batas SL  : Rp {sl_4h_idr:,.0f}\n"
            f"----------------------------------\n"
            f"📋 Sinyal Terdeteksi\n"
            f"- Golden Cross + MA Inflection / Squeeze\n"
            f"----------------------------------\n"
            f"📋 SKOR SETUP — {skor_smc}/100\n"
            f"{breakdown_str}\n"
            f"```"
        )

        await kirim_pesan(bot, msg)
        print(f"Sukses mengirim analisa {PAIR_NAME} ke Telegram.")

    except Exception as e:
        print(f"Error saat analisa {SYMBOL_SPOT}: {e}")
        await kirim_pesan(bot, f"⚠️ *Gagal Analisa* `{PAIR_NAME}`\nError: `{str(e)[:200]}`")

def run_analysis():
    asyncio.run(main_async())

if __name__ == "__main__":
    run_analysis()
