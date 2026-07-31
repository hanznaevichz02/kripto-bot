"""
========================================================
    KRIPTO BOT — Analisa Mendalam Per-Koin (v6.3.7 Final Sync & Tri-State)
    Fungsi : Trigger manual, input simbol bebas.
             Orientasi SPOT dengan advice khusus fee Pluang.
    Fixes  : - Clean Dead Code (SL/TP 1H & Pivots 1H Dihapus)
             - Dynamic Fee Buffer (Min Profit Kotor 3.0%)
             - Simetrisasi Tren KOREKSI vs REBOUND
             - Clean NBSP Character Encoding Bug
             - Fix RSI String Interpolation (Missing f-string)
             - Fix Directional Trend Consistency (Adaptif Bullish/Bearish)
             - Fix Logic Drift & Sideways Contradiction (Tri-State Machine)
========================================================
"""

import asyncio
from datetime import datetime, timezone
import os
import sys
import ccxt.async_support as ccxt
import pandas as pd
import httpx
from telegram import Bot

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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

async def get_usd_idr() -> float:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://indodax.com/api/ticker/usdtidr", timeout=5.0)
            raw_idr = float(r.json()["ticker"]["last"])
            PLUANG_MARGIN = 1.0052
            return raw_idr * PLUANG_MARGIN
    except Exception:
        return 18000.0 * 1.0052

def is_candle_running(timeframe: str) -> tuple[bool, int]:
    """Menghitung durasi candle yang sedang berjalan dalam menit."""
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

def hitung_skor_smc(choch, bos, closed_confirmed, trend_consistency_score, mitigation, fvg, rrr, volume_spike, ema9_break_bull, ema9_break_bear, label_arah_tren):
    score = 0
    breakdown = []
    
    if choch:
        score += 20
        breakdown.append("- Konfirmasi CHoCH (20 Candle) (+20)")
    else:
        breakdown.append("- Tanpa CHoCH (+0)")
        
    if bos:
        score += 15
        breakdown.append("- Breakout BOS (20 Candle) (+15)")
    else:
        breakdown.append("- Tanpa BOS (+0)")

    if closed_confirmed:
        score += 15
        breakdown.append("- Candle Tertutup Mengonfirmasi (+15)")
    else:
        breakdown.append("- Belum Ada Konfirmasi Candle Tertutup (+0)")

    if trend_consistency_score >= 0.7:
        score += 10
        breakdown.append(f"- Tren 14-Candle Konsisten Searah ({int(trend_consistency_score*100)}% {label_arah_tren}) (+10)")
    elif trend_consistency_score <= 0.3:
        breakdown.append(f"- Tren 14-Candle Berlawanan ({int(trend_consistency_score*100)}%) (+0)")
    else:
        breakdown.append("- Tren 14-Candle Netral/Sideways (+5)")
        score += 5

    if mitigation:
        score += 10
        breakdown.append("- Area Mitigasi OB Tersentuh (+10)")
        
    if fvg:
        score += 10
        breakdown.append("- Area FVG Valid (+10)")
        
    if volume_spike:
        score += 10
        breakdown.append("- Lonjakan Vol vs Rata2 20 Candle (+10)")
        
    if ema9_break_bull or ema9_break_bear:
        score += 5
        breakdown.append("- Patahan EMA 9 Terdeteksi (+5)")
        
    if rrr >= 2.0:
        score += 5
        breakdown.append(f"- RRR Ideal ({rrr:.2f} >= 2.0) (+5)")

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
    print(f"DEBUG: Analisa {SYMBOL_SPOT}...")
    bot = Bot(token=TOKEN)

    exchange_spot = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    exchange_swap = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 30000})

    try:
        await exchange_spot.load_markets()
    except Exception as e:
        await kirim_pesan(bot, f"⚠️ *Gagal Memuat Market*\nSymbol: `{SYMBOL_SPOT}`\nError: `{str(e)[:150]}`")
        await exchange_spot.close()
        await exchange_swap.close()
        return

    usd_idr = await get_usd_idr()

    # --- HITUNG DURASI CANDLE RUNNING ---
    _, menit_1h = is_candle_running("1h")
    if menit_1h < 15:
        info_running_1h = f"{menit_1h} mnt (⚠️ Awal Candle, Rawan Fakeout!)"
    elif menit_1h >= 45:
        info_running_1h = f"{menit_1h} mnt (⏳ Mendekati Closing)"
    else:
        info_running_1h = f"{menit_1h} mnt (⏱️ Running)"

    # --- FUNDING RATE ---
    fr_tersedia = True
    try:
        await exchange_swap.load_markets()
        fr_data = await exchange_swap.fetch_funding_rate(SYMBOL_SWAP)
        funding_rate = float(fr_data.get('fundingRate', 0.0) or 0.0)
    except Exception:
        fr_tersedia = False
        funding_rate = 0.0

    fr_persen = funding_rate * 100
    if not fr_tersedia:
        status_fr = "N/A (Pair tidak di Futures)"
    elif fr_persen > 0.02:
        status_fr = f"{fr_persen:.4f}% (Long Dominan 🔥 - Waspada!)"
    elif fr_persen < -0.01:
        status_fr = f"{fr_persen:.4f}% (Short Dominan 💧 - Potensi Rebound)"
    else:
        status_fr = f"{fr_persen:.4f}% (Seimbang ⚖️)"

    try:
        # --- DATA OHLCV ---
        bars_1h_task = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='1h', limit=60)
        bars_4h_task = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='4h', limit=60)
        bars_1d_task = exchange_spot.fetch_ohlcv(SYMBOL_SPOT, timeframe='1d', limit=40)
        bars_1h, bars_4h, bars_1d = await asyncio.gather(bars_1h_task, bars_4h_task, bars_1d_task)

        if len(bars_1h) < 30 or len(bars_4h) < 30:
            await kirim_pesan(bot, f"⚠️ *Data Tidak Cukup*\nSymbol: `{SYMBOL_SPOT}`")
            await exchange_spot.close()
            await exchange_swap.close()
            return

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_1d = pd.DataFrame(bars_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)

        harga_sekarang = float(df_1h['close'].iloc[-1] * usd_idr)

        # --- INDIKATOR RIWAYAT (14-20 CANDLE) ---
        df_1h['atr'] = hitung_true_range_atr(df_1h, 14)
        df_4h['atr'] = hitung_true_range_atr(df_4h, 14)
        df_1d['atr'] = hitung_true_range_atr(df_1d, 14)
        df_1h['avg_vol'] = df_1h['volume'].rolling(20).mean().shift(1)

        atr_4h_idr = float(df_4h['atr'].iloc[-1] * usd_idr)

        # Pivot Points
        def pivot_levels(df):
            h, l, c = df['high'].iloc[-1], df['low'].iloc[-1], df['close'].iloc[-1]
            p = (h + l + c) / 3
            return {'p': p, 'r1': (2 * p) - l, 'r2': p + (h - l), 's1': (2 * p) - h, 's2': p - (h - l)}

        piv_4h, piv_1d = pivot_levels(df_4h), pivot_levels(df_1d)
        r1_4h_idr, r2_4h_idr = float(piv_4h['r1'] * usd_idr), float(piv_4h['r2'] * usd_idr)
        s1_4h_idr, s2_4h_idr = float(piv_4h['s1'] * usd_idr), float(piv_4h['s2'] * usd_idr)
        r1_1d_idr, r2_1d_idr = float(piv_1d['r1'] * usd_idr), float(piv_1d['r2'] * usd_idr)
        s1_1d_idr, s2_1d_idr = float(piv_1d['s1'] * usd_idr), float(piv_1d['s2'] * usd_idr)

        # EMA Trend
        for df in (df_1h, df_4h, df_1d):
            df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
            df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

        def baca_momentum_state(df):
            c = df['close'].iloc[-1]
            e9 = df['ema9'].iloc[-1]
            e21 = df['ema21'].iloc[-1]
            if c > e9 and e9 > e21: return "BULLISH", "NAIK KOKOH 🟢"
            elif c > e9 and e9 <= e21: return "BULLISH", "REBOUND ↗️"
            elif c < e9 and e9 < e21: return "BEARISH", "TURUN 🔴"
            elif c < e9 and e9 >= e21: return "BEARISH", "KOREKSI ↘️"
            else: return "SIDEWAYS", "SIDEWAYS ⚪"

        state_4h, tren_4h_teks = baca_momentum_state(df_4h)
        state_1h, tren_1h_teks = baca_momentum_state(df_1h)
        state_1d, tren_1d_teks = baca_momentum_state(df_1d)

        # ============================================================
        # TRI-STATE UNIFIED STATE & SYNCHRONIZED HEALTH CLASSIFICATION
        # ============================================================
        is_bullish_4h = (state_4h == "BULLISH")
        is_sideways_4h = (state_4h == "SIDEWAYS")
        is_bearish_4h = (state_4h == "BEARISH")

        is_bullish_1h = (state_1h in ["BULLISH"])
        is_bearish_1h = (state_1h in ["BEARISH"])

        # Koin Hancur: Kedua TF utama berada di zona bearish murni
        koin_hancur = is_bearish_4h and is_bearish_1h

        # Koin Sehat: 4H Bullish atau Sideways, didukung 1H yang Bullish/Rebound (Bebas Drift)
        koin_sehat = (is_bullish_4h or is_sideways_4h) and is_bullish_1h

        # --- KONSISTENSI TREN 14 CANDLE (DIRECTIONAL / ADAPTIF) ---
        if is_bearish_4h:
            candle_aligned_14 = (df_1h['close'].iloc[-15:-1] < df_1h['ema9'].iloc[-15:-1]).sum()
            label_arah_tren = "Bearish"
        else:
            candle_aligned_14 = (df_1h['close'].iloc[-15:-1] > df_1h['ema9'].iloc[-15:-1]).sum()
            label_arah_tren = "Bullish/Range"

        trend_consistency_score = candle_aligned_14 / 14.0

        # Deteksi Patahan EMA 9 (1H)
        ema9_break_bull = bool((df_1h['close'].iloc[-2] <= df_1h['ema9'].iloc[-2]) and (df_1h['close'].iloc[-1] > df_1h['ema9'].iloc[-1]))
        ema9_break_bear = bool((df_1h['close'].iloc[-2] >= df_1h['ema9'].iloc[-2]) and (df_1h['close'].iloc[-1] < df_1h['ema9'].iloc[-1]))

        patahan_ema9_teks = "🚀 Bullish (Jebol EMA9 Atas)" if ema9_break_bull else ("⚠️ Bearish (Tembus EMA9 Bawah)" if ema9_break_bear else "Tidak Ada Patahan Baru")

        # Swing High/Low 20 Candle
        swing_high_20 = df_1h['high'].iloc[-21:-1].max()
        swing_low_20 = df_1h['low'].iloc[-21:-1].min()

        # --- INISIALISASI STATUS TRENS (Tambahkan di sini) ---
        is_bearish_4h = c_4h < e9 and e9 < e21
        is_sideways_4h = not trend_4h_bull and not is_bearish_4h # Sesuaikan logika sideways Anda
    
        # Jika belum ada DataFrame 1D khusus, bisa diturunkan dari kondisi 4H/EMA50
        is_bearish_1d = not trend_4h_bull
    
        # Target SL/TP & Text Level Berdasarkan State Mode (Bullish/Sideways vs Bearish)
        if is_bearish_4h:
            level_4h_teks = f"  Lantai 1    : Rp {s1_4h_idr:,.0f}\n  Lantai 2    : Rp {s2_4h_idr:,.0f}"
            sl_4h_idr, tp_4h_idr = s2_4h_idr - (0.5 * atr_4h_idr), r1_4h_idr
        elif is_sideways_4h:
            level_4h_teks = f"  Atap 1      : Rp {r1_4h_idr:,.0f}\n  Lantai 1    : Rp {s1_4h_idr:,.0f}"
            sl_4h_idr, tp_4h_idr = s1_4h_idr - (0.5 * atr_4h_idr), r1_4h_idr
        else:
            level_4h_teks = f"  Atap 1      : Rp {r1_4h_idr:,.0f}\n  Atap 2      : Rp {r2_4h_idr:,.0f}"
            sl_4h_idr, tp_4h_idr = s1_4h_idr - (0.5 * atr_4h_idr), r2_4h_idr

        if is_bearish_1d:
            level_1d_teks = f"  Lantai 1    : Rp {s1_1d_idr:,.0f}\n  Lantai 2    : Rp {s2_1d_idr:,.0f}"
        else:
            level_1d_teks = f"  Atap 1      : Rp {r1_1d_idr:,.0f}\n  Atap 2      : Rp {r2_1d_idr:,.0f}"

        risk_4h = abs(harga_sekarang - sl_4h_idr)
        reward_4h = abs(tp_4h_idr - harga_sekarang)
        rrr_4h = (reward_4h / risk_4h) if risk_4h > 0 else 0.0

        # RSI & Divergence
        delta = df_4h['close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        ema_up, ema_down = up.ewm(com=13, adjust=False).mean(), down.ewm(com=13, adjust=False).mean()
        rs = ema_up / ema_down
        df_4h['rsi'] = 100 - (100 / (1 + rs))
        rsi_4h = df_4h['rsi'].iloc[-1]

        status_rsi = (
            f"Overbought ({rsi_4h:.0f})" if rsi_4h >= 70 else
            f"Oversold ({rsi_4h:.0f})" if rsi_4h <= 30 else
            f"Normal ({rsi_4h:.0f})")

        price_hh = df_4h['close'].iloc[-1] > df_4h['close'].iloc[-11:-1].max()
        rsi_lh = df_4h['rsi'].iloc[-1] < df_4h['rsi'].iloc[-11:-1].max()
        price_ll = df_4h['close'].iloc[-1] < df_4h['close'].iloc[-11:-1].min()
        rsi_hl = df_4h['rsi'].iloc[-1] > df_4h['rsi'].iloc[-11:-1].min()

        divergence_teks = "⚠️ Bearish" if (price_hh and rsi_lh) else ("✅ Bullish" if (price_ll and rsi_hl) else "Tidak Terdeteksi")

        # Evaluasi BOS/CHoCH Sesuai State Mode (Bullish/Sideways vs Bearish)
        curr_1h_live = df_1h.iloc[-1]
        prev_1h_closed = df_1h.iloc[-2]
        vol_spike = bool(curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.8))

        if is_bearish_4h:
            choch = bool(curr_1h_live['close'] < curr_1h_live['open'] and curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.5))
            bos = bool(curr_1h_live['close'] < swing_low_20)
            closed_confirmed = bool(prev_1h_closed['close'] < df_1h['ema9'].iloc[-2])
            mitigation = bool((df_4h['high'].iloc[-1] * usd_idr) >= (r1_4h_idr * 0.995))
        else:
            # Bullish atau Sideways menggunakan struktur Support-Bounce (Buy on Dip)
            choch = bool(curr_1h_live['close'] > curr_1h_live['open'] and curr_1h_live['volume'] > (df_1h['avg_vol'].iloc[-1] * 1.5))
            bos = bool(curr_1h_live['close'] > swing_high_20)
            closed_confirmed = bool(prev_1h_closed['close'] > df_1h['ema9'].iloc[-2])
            mitigation = bool((df_4h['low'].iloc[-1] * usd_idr) <= (s1_4h_idr * 1.005))

        fvg_type, fvg_min, fvg_max = cek_fvg(df_1h, usd_idr)
        if fvg_type == "Bullish":
            fvg_active, fvg_teks_status = True, f"BULLISH 🟢 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        elif fvg_type == "Bearish":
            fvg_active, fvg_teks_status = True, f"BEARISH 🔴 (Rp {fvg_min:,.0f} - Rp {fvg_max:,.0f})"
        else:
            fvg_active, fvg_teks_status = False, "Tidak Ada / Termitigasi ❌"

        # Skor Setup
        skor_smc, breakdown_skor = hitung_skor_smc(
            choch, bos, closed_confirmed, trend_consistency_score,
            mitigation, fvg_active, rrr_4h, vol_spike, ema9_break_bull, ema9_break_bear, label_arah_tren
        )

        label_skor = "🔥 SANGAT KUAT" if skor_smc >= 70 else ("✅ POTENSIAL" if skor_smc >= 50 else "⚠️ STANDAR")
        breakdown_str = "\n".join(breakdown_skor)

        # ============================================================
        # ADVICE ENGINE (DYNAMIC FEE & BUFFER ADJUSTMENT)
        # ============================================================
        FEE_PLUANG = 0.013          # Fee total (Beli + Jual) Pluang = 1.3%
        NET_MARGIN_MIN = 0.017      # Target margin untung bersih minimal = 1.7%
        
        MIN_PROFIT_BUFFER = FEE_PLUANG + NET_MARGIN_MIN 

        profit_kotor_persen = (reward_4h / harga_sekarang) if harga_sekarang > 0 else 0
        
        kesimpulan_advice = ""
        alasan_skip = []

        if rrr_4h < 1.2:
            alasan_skip.append("RRR terlalu sempit (< 1:1.2)")
        if skor_smc < 45:
            alasan_skip.append(f"Skor Keseluruhan Lemah ({skor_smc}/100)")
        if profit_kotor_persen < MIN_PROFIT_BUFFER:
            alasan_skip.append(
                f"Profit kotor tipis ({(profit_kotor_persen*100):.1f}% < {MIN_PROFIT_BUFFER*100:.1f}%), "
                f"net profit < {NET_MARGIN_MIN*100:.1f}% setelah Fee Pluang ({FEE_PLUANG*100:.1f}%)"
            )

        if len(alasan_skip) > 0 and koin_hancur:
            kesimpulan_advice = f"🔴 SKIP / SANGAT BERISIKO\n- Alasan: Koin longsor & {', '.join(alasan_skip)}."
        elif len(alasan_skip) > 0 and not koin_hancur:
            kesimpulan_advice = f"🟡 KURANG IDEAL TAPI BISA PANTAU\n- Alasan: {', '.join(alasan_skip)}."
        else:
            if koin_hancur:
                if ema9_break_bull or "Bullish" in divergence_teks:
                    kesimpulan_advice = "🟢 ENTRY (BOTTOM FISHING)\n- Sinyal: Ada Reversal Valid. Cocok Scalping (Max 2 hari). SL Ketat!"
                else:
                    kesimpulan_advice = "🔴 SKIP (PISAU JATUH)\n- Sinyal: Tren turun kuat tanpa konfirmasi reversal."
            elif koin_sehat:
                if "Oversold" in status_rsi or closed_confirmed:
                    kesimpulan_advice = "🟢 ENTRY (BUY ON DIP / RANGE SWING)\n- Sinyal: Tren sehat/sideways range dikonfirmasi support bounce (Short Swing max 3 hari)."
                else:
                    kesimpulan_advice = "🟡 WAIT (RAWAN PUCUK)\n- Sinyal: Harga berada di tengah range. Tunggu antre di Support / Lantai."
            else:
                kesimpulan_advice = "🟢 ENTRY (RANGE TRADING)" if (rrr_4h >= 2.0 and skor_smc >= 50) else "🟡 WAIT & SEE"

        # --- FORMAT PESAN ---
        msg = (
            f"```text\n"
            f"🔍 {PAIR_NAME}\n"
            f"----------------------------------\n"
            f"• Harga     : Rp {harga_sekarang:,.0f}\n"
            f"• Candle 1H : {info_running_1h}\n"
            f"----------------------------------\n"
            f"• Future    : {status_fr}\n"
            f"• FVG       : {fvg_teks_status}\n"
            f"• RSI       : {status_rsi}\n"
            f"• Divergence: {divergence_teks}\n"
            f"• Patahan 9 : {patahan_ema9_teks}\n"
            f"• Konsistensi: {int(trend_consistency_score*100)}% {label_arah_tren} (14 Candle)\n"
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
            f"💡 ADVICE & STRATEGI\n"
            f"{kesimpulan_advice}\n"
            f"----------------------------------\n"
            f"📋 SKOR SETUP — {skor_smc}/100\n"
            f"{breakdown_str}\n"
            f"```"
        )

        await kirim_pesan(bot, msg)
        print(f"Sukses menganalisa {PAIR_NAME}")

    except Exception as e:
        print(f"Error analisa: {e}")
        await kirim_pesan(bot, f"⚠️ *Gagal Analisa* `{PAIR_NAME}`\nError: `{str(e)[:200]}`")

    finally:
        await exchange_spot.close()
        await exchange_swap.close()

def run_analysis():
    asyncio.run(main_async())

if __name__ == "__main__":
    run_analysis()
