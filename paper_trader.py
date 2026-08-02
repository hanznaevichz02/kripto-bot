"""
========================================================
    KRIPTO BOT — Hybrid Aggressive Edition (Paper Trading) v4.6.1
    Fungsi  : Integrasi Skor SMC Kuantitatif, ATR Buffer, Volume Spike, FVG, MA Inflection/Squeeze, & Smart TP Filter (Multi-Entry & Average Down) [Fully Async & Optimized]
========================================================
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
import ccxt.async_support as ccxt
import pandas as pd
import httpx
from telegram import Bot

# --- KONFIGURASI LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("CryptoBotPaper")

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

VOL_MULTIPLIER_TECH = 2.0  # Acuan Bot TECH
VOL_MULTIPLIER_SMC = 1.5   # Acuan Bot SMC
MIN_SCORE_ENTRY = 40       # Batas minimal skor kelayakan (0 - 100)

INITIAL_CAPITAL_IDR = 3_000_000.0
ENTRY_CAPITAL_IDR = 1_000_000.0
MAX_POSITIONS = 3

STATE_FILE = "paper_trading_hybrid.json"
SIGNAL_FILE = "signal_trader.json"
FEE_TAX_RATE = 0.013  # Fee + Pajak PMK 68 dipotong saat Sell/Exit (1.3%)

ASSET_LIST = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "SUI/USDT", "XRP/USDT", "LINK/USDT", "AAVE/USDT",
    "DOT/USDT", "ONDO/USDT", "ARB/USDT", "NEAR/USDT",
    "TAO/USDT", "AVAX/USDT", "ADA/USDT",
    "TRX/USDT", "UNI/USDT"
]

FUTURES_MAP = {
    "BTC/USDT": "XBTUSDTM", "ETH/USDT": "ETHUSDTM",
    "SOL/USDT": "SOLUSDTM", "BNB/USDT": "BNBUSDTM",
    "SUI/USDT": "SUIUSDTM", "XRP/USDT": "XRPUSDTM",
    "LINK/USDT": "LINKUSDTM", "AAVE/USDT": "AAVEUSDTM",
    "DOT/USDT": "DOTUSDTM", "ONDO/USDT": "ONDOUSDTM",
    "ARB/USDT": "ARBUSDTM", "NEAR/USDT": "NEARUSDTM",
    "ZEC/USDT": "ZECUSDTM", "TAO/USDT": "TAOUSDTM",
    "AVAX/USDT": "AVAXUSDTM", "ADA/USDT": "ADAUSDTM",
}


# --- MANAJEMEN STATE & SIGNAL ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                if "cash_idr" not in state:
                    state["cash_idr"] = INITIAL_CAPITAL_IDR
                
                if "active_positions" not in state:
                    if "active_position" in state and state["active_position"]:
                        state["active_positions"] = [state["active_position"]]
                    else:
                        state["active_positions"] = []
                if "active_position" in state:
                    del state["active_position"]

                if "history" not in state:
                    state["history"] = []
                if "stats" not in state:
                    state["stats"] = {
                        "total_trades": 0,
                        "wins": 0,
                        "losses": 0,
                        "total_pnl_idr": 0.0,
                    }
                return state
        except Exception:
            pass
    return {
        "cash_idr": INITIAL_CAPITAL_IDR,
        "active_positions": [],
        "history": [],
        "stats": {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_idr": 0.0},
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def save_signal(signal_data):
    with open(SIGNAL_FILE, "w") as f:
        json.dump(signal_data, f, indent=4)


# --- FUNGSI HELPER ASYNC ---
async def get_usd_idr() -> float:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://indodax.com/api/ticker/usdtidr", timeout=5.0)
            raw_idr = float(r.json()["ticker"]["last"])
            PLUANG_MARGIN = 1.0052
            return raw_idr * PLUANG_MARGIN
    except Exception:
        return 18000.0 * 1.0052


async def get_funding_rate(exchange_futures, futures_symbol):
    try:
        info = await exchange_futures.fetch_funding_rate(futures_symbol)
        return float(info.get("fundingRate", 0))
    except Exception as e:
        logger.warning(f"Gagal ambil funding rate {futures_symbol}: {e}")
        return None


def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 20) -> dict:
    swing_low = float(df_4h["low"].iloc[-window - 1 : -1].min())
    swing_high = float(df_4h["high"].iloc[-window - 1 : -1].max())
    return {"swing_high": swing_high, "swing_low": swing_low}


def hitung_skor_hybrid(
    ema9_now, ema21_now, is_spike_vol_tech, vol_spike_smc,
    golden_cross, pullback_bounce, inflection_entry, bull_sweep_smc,
    harga_idr, sl_price, tp_price, funding_rate=None
):
    score = 0
    breakdown = []

    if ema9_now > ema21_now:
        score += 20
        breakdown.append("• Tren EMA NAIK (+20)")

    if is_spike_vol_tech or vol_spike_smc:
        score += 25
        breakdown.append("• Volume Spike Konfirmasi (+25)")

    if golden_cross:
        score += 30
        breakdown.append("• Golden Cross Momentum (+30)")
    elif pullback_bounce:
        score += 25
        breakdown.append("• Pullback Bounce Konfirmasi (+25)")
    elif inflection_entry:
        score += 25
        breakdown.append("• MA Inflection / Squeeze (+25)")

    if bull_sweep_smc:
        score += 30
        breakdown.append("• SMC Liquidity Sweep (+30)")

    risk = harga_idr - sl_price
    reward = tp_price - harga_idr
    rrr = (reward / risk) if risk > 0 else 0.0

    if rrr >= 2.0:
        score += 25
        breakdown.append(f"• RRR Sangat Baik ({rrr:.2f} >= 2.0) (+25)")
    elif rrr >= 1.5:
        score += 15
        breakdown.append(f"• RRR Cukup Baik ({rrr:.2f} >= 1.5) (+15)")

    if funding_rate is not None:
        fr_pct = funding_rate * 100
        if funding_rate < -0.0005:
            score += 15
            breakdown.append(f"• Funding Rate Negatif Squeeze ({fr_pct:.4f}%) (+15)")
        elif funding_rate > 0.0005:
            score -= 15
            breakdown.append(f"• Funding Rate Overheated Long ({fr_pct:.4f}%) (-15)")
        else:
            breakdown.append(f"• Funding Rate Normal ({fr_pct:+.4f}%) (+0)")
    else:
        breakdown.append("• Funding Rate: N/A (+0)")

    final_score = max(0, min(score, 100))
    return final_score, rrr, breakdown


# --- ANALISA SINGLE KOIN (ASYNC) ---
async def analisa_koin_hybrid(exchange_spot, exchange_futures, symbol, usd_idr):
    try:
        bars_1h_task = exchange_spot.fetch_ohlcv(symbol, timeframe="1h", limit=50)
        bars_4h_task = exchange_spot.fetch_ohlcv(symbol, timeframe="4h", limit=30)
        bars_1h, bars_4h = await asyncio.gather(bars_1h_task, bars_4h_task)

        if len(bars_1h) < 40 or len(bars_4h) < 15:
            return None

        df_1h = pd.DataFrame(bars_1h, columns=["timestamp", "open", "high", "low", "close", "volume"]).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=["timestamp", "open", "high", "low", "close", "volume"]).astype(float)

        curr_idx = -2
        prev_idx = -3
        c = df_1h.iloc[curr_idx]
        p = df_1h.iloc[prev_idx]

        latest_c = df_1h.iloc[-1]
        harga_idr = float(latest_c["close"] * usd_idr)
        high_idr = float(latest_c["high"] * usd_idr)
        low_idr = float(latest_c["low"] * usd_idr)

        tr0 = df_1h["high"] - df_1h["low"]
        tr1 = (df_1h["high"] - df_1h["close"].shift(1)).abs()
        tr2 = (df_1h["low"] - df_1h["close"].shift(1)).abs()
        df_1h["tr"] = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h["atr"] = df_1h["tr"].rolling(window=14).mean()
        atr_idr = float(df_1h["atr"].iloc[curr_idx] * usd_idr)

        # SMC Logic
        avg_vol_smc = float(df_1h["volume"].iloc[-22:-2].median())
        candle_range = c["high"] - c["low"]
        lower_wick = min(c["close"], c["open"]) - c["low"]
        upper_wick = c["high"] - max(c["close"], c["open"])
        vol_spike_smc = c["volume"] > (avg_vol_smc * VOL_MULTIPLIER_SMC)

        tutup_hijau_smc = c["close"] > c["open"]
        tutup_merah_smc = c["close"] < c["open"]
        body_size = abs(c["close"] - c["open"])
        is_panic_dump = tutup_merah_smc and (body_size > candle_range * 0.5)

        bull_sweep_smc = bool(
            (lower_wick > candle_range * 0.35)
            and vol_spike_smc
            and tutup_hijau_smc
            and not is_panic_dump
            and (c["close"] > p["low"])
        )

        bear_sweep_smc = bool(
            (upper_wick > candle_range * 0.35)
            and vol_spike_smc
            and tutup_merah_smc
            and (c["close"] < p["high"])
        )

        # Technical Logic & MA Inflection / Squeeze
        df_1h["ema9"] = df_1h["close"].ewm(span=9, adjust=False).mean()
        df_1h["ema21"] = df_1h["close"].ewm(span=21, adjust=False).mean()
        df_1h["avg_vol_tech"] = df_1h["volume"].rolling(window=3).mean().shift(1)

        is_spike_vol_tech = bool(c["volume"] > (df_1h["avg_vol_tech"].iloc[curr_idx] * VOL_MULTIPLIER_TECH))

        slope_ema9 = abs(df_1h["ema9"].iloc[curr_idx] - df_1h["ema9"].iloc[prev_idx]) / df_1h["ema9"].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.25
        ema9_now = df_1h["ema9"].iloc[curr_idx]
        ema9_prev = df_1h["ema9"].iloc[prev_idx]
        ema21_now = df_1h["ema21"].iloc[curr_idx]
        ema21_prev = df_1h["ema21"].iloc[prev_idx]

        golden_cross = bool(
            (ema9_prev < ema21_prev)
            and (ema9_now > ema21_now)
            and is_spike_vol_tech
            and is_sudut_tajam
        )

        tren_bullish = ema9_now > ema21_now
        sentuh_ema21 = c["low"] <= (ema21_now * 1.002)
        tutup_hijau = c["close"] > c["open"]
        tutup_atas_ema9 = c["close"] > ema9_now
        vol_oke_tech = c["volume"] > df_1h["avg_vol_tech"].iloc[curr_idx]
        pullback_bounce = bool(
            tren_bullish and sentuh_ema21 and tutup_hijau and tutup_atas_ema9 and vol_oke_tech
        )

        df_1h["ema_spread"] = (df_1h["ema9"] - df_1h["ema21"]).abs()
        spread_now = df_1h["ema_spread"].iloc[curr_idx]
        spread_prev = df_1h["ema_spread"].iloc[prev_idx]
        spread_prev2 = df_1h["ema_spread"].iloc[prev_idx - 1]

        slope_now_val = df_1h["ema9"].iloc[curr_idx] - df_1h["ema9"].iloc[prev_idx]
        slope_prev_val = df_1h["ema9"].iloc[prev_idx] - df_1h["ema9"].iloc[prev_idx - 1]
        is_inflection_bottom = bool((slope_prev_val < 0) and (slope_now_val >= 0))
        is_ma_squeeze = bool(
            (spread_prev < spread_prev2)
            and (spread_now > spread_prev)
            and (spread_now < (df_1h["close"].iloc[curr_idx] * 0.003))
        )

        inflection_entry = bool(tren_bullish and (is_inflection_bottom or is_ma_squeeze) and vol_oke_tech)
        tech_entry_signal = golden_cross or pullback_bounce or inflection_entry

        # Swing 4H & TP/SL Filter
        swing = deteksi_swing_4h(df_4h, window=20)
        swing_high_idr = swing["swing_high"] * usd_idr
        swing_low_idr = swing["swing_low"] * usd_idr

        sl_bullish = swing_low_idr - (0.5 * atr_idr)

        MIN_GROSS_TP_PCT = 0.03
        min_tp_by_pct = harga_idr * (1.0 + MIN_GROSS_TP_PCT)
        min_tp_by_atr = harga_idr + (3.5 * atr_idr)

        target_dasar_tp = max(min_tp_by_pct, min_tp_by_atr)
        tp_bullish = max(target_dasar_tp, min(swing_high_idr, harga_idr + (5.0 * atr_idr)))

        if sl_bullish >= harga_idr * 0.985:
            sl_bullish = harga_idr - (1.8 * atr_idr)

        futures_symbol = FUTURES_MAP.get(symbol)
        funding_rate = await get_funding_rate(exchange_futures, futures_symbol) if futures_symbol else None

        score, rrr, breakdown = hitung_skor_hybrid(
            ema9_now, ema21_now, is_spike_vol_tech, vol_spike_smc,
            golden_cross, pullback_bounce, inflection_entry, bull_sweep_smc,
            harga_idr, sl_bullish, tp_bullish, funding_rate
        )

        pemicu_list = []
        if golden_cross: pemicu_list.append("Golden Cross")
        if pullback_bounce: pemicu_list.append("Pullback Bounce")
        if inflection_entry: pemicu_list.append("MA Inflection / Squeeze")
        if bull_sweep_smc: pemicu_list.append("SMC Bull Sweep")
        trigger_str = " + ".join(pemicu_list) if pemicu_list else "Monitoring"

        return {
            "symbol": symbol,
            "pair_name": symbol.replace("/", "-").replace("USDT", "IDR"),
            "harga_idr": float(harga_idr),
            "high_idr": float(high_idr),
            "low_idr": float(low_idr),
            "sl_price": float(sl_bullish),
            "tp_price": float(tp_bullish),
            "is_entry": (tech_entry_signal or bull_sweep_smc),
            "is_emergency_exit": bear_sweep_smc,
            "emerg_reason": "SMC Bear Sweep" if bear_sweep_smc else "",
            "trigger_str": trigger_str,
            "score": score,
            "rrr": rrr,
            "breakdown": breakdown,
        }
    except Exception as e:
        logger.error(f"Error analisa Hybrid {symbol}: {e}")
        return None


# --- MAIN EXECUTOR ---
async def main():
    logger.info("Menjalankan Paper Trader Hybrid Multi-Asset Scanner (v4.6.1 - Async Optimized)...")

    exchange_spot = ccxt.kucoin({"enableRateLimit": True, "options": {"defaultType": "spot"}, "timeout": 30000})
    exchange_futures = ccxt.kucoinfutures({"enableRateLimit": True, "timeout": 30000})
    bot = Bot(token=TOKEN)

    try:
        await exchange_spot.load_markets()
    except Exception as e:
        logger.error(f"Gagal memuat market: {e}")
        await exchange_spot.close()
        await exchange_futures.close()
        return

    usd_idr = await get_usd_idr()
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    logger.info(f"[{now_wib.strftime('%H:%M')} WIB] USD/IDR={usd_idr:,.0f}")

    state = load_state()

    # =========================================================
    # 1. EVALUASI EXIT (UNTUK SEMUA POSISI AKTIF)
    # =========================================================
    active_positions = state.get("active_positions", [])
    updated_positions = []

    for pos in active_positions:
        symbol = pos["symbol"]
        pair_name = pos["pair_name"]
        data = await analisa_koin_hybrid(exchange_spot, exchange_futures, symbol, usd_idr)

        if data:
            entry_p, amount, sl, tp = pos["entry_price_idr"], pos["amount"], pos["sl"], pos["tp"]
            is_win = data["high_idr"] >= tp
            is_loss = data["low_idr"] <= sl
            
            # Emergency Exit HANYA aktif jika terdeteksi Bear Sweep DAN 
            # harga saat ini sudah mendekati zona SL (misal jarak sisa kurang dari 1% dari SL) 
            # atau low harga sudah sangat tipis di atas SL.
            jarak_ke_sl_pct = (data["harga_idr"] - sl) / sl
            is_emerg_exit = data["is_emergency_exit"] and (jarak_ke_sl_pct <= 0.01)

            if is_win or is_loss or is_emerg_exit:
                if is_loss:
                    exit_reason = "STOP LOSS (SWING 4H) 🛑"
                    exit_price = sl
                elif is_win:
                    exit_reason = "TAKE PROFIT (SMART TARGET) 🎯"
                    exit_price = tp
                else:
                    exit_reason = f"EMERGENCY EXIT ({data['emerg_reason']} - Dekat SL) ⚠️"
                    # Keluar di harga SL atau harga pasar terendah saat itu jika sudah jebol tipis
                    exit_price = min(data["harga_idr"], sl)

                gross = exit_price * amount
                net = gross - (gross * FEE_TAX_RATE)
                modal = entry_p * amount
                pnl_val = net - modal
                pnl_pct = (pnl_val / modal) * 100
                status = "WIN" if pnl_val > 0 else "LOSS"

                state["cash_idr"] += net
                state["history"].append({
                    "pair": pair_name,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_idr": round(pnl_val, 2),
                    "status": status,
                    "entry_time": pos["entry_time"],
                    "exit_time": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
                })
                state["stats"]["total_trades"] += 1
                if status == "WIN":
                    state["stats"]["wins"] += 1
                else:
                    state["stats"]["losses"] += 1
                state["stats"]["total_pnl_idr"] += round(pnl_val, 2)
                save_state(state)

                stats = state["stats"]
                wr = (stats["wins"] / stats["total_trades"]) * 100 if stats["total_trades"] > 0 else 0

                msg = (
                    f"🧪 <b>[PAPER TRADING - HYBRID EXIT]</b> {pair_name}\n"
                    f"──────────────────────────────\n"
                    f"Alasan    : {exit_reason}\n"
                    f"Harga In  : Rp {entry_p:,.0f}\n"
                    f"Harga Out : Rp {exit_price:,.0f}\n"
                    f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
                    f"📊 <b>REKAP TOTAL HYBRID</b>:\n"
                    f"• Total Trade : {stats['total_trades']}x\n"
                    f"• Win / Loss  : {stats['wins']} Win / {stats['losses']} Loss (WR: {wr:.1f}%)\n"
                    f"• Total P/L   : Rp {stats['total_pnl_idr']:+,.0f}\n"
                    f"• Sisa Kas    : Rp {state['cash_idr']:,.0f}"
                )
                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
            else:
                updated_positions.append(pos)
        else:
            updated_positions.append(pos)

    state["active_positions"] = updated_positions
    save_state(state)

    # =========================================================
    # 2. SCANNING SECARA PARALEL (ASYNC GATHER) & RANKING
    # =========================================================
    semaphore = asyncio.Semaphore(5)
    async def scan_worker(sym):
        async with semaphore:
            return await analisa_koin_hybrid(exchange_spot, exchange_futures, sym, usd_idr)

    scan_tasks = [scan_worker(sym) for sym in ASSET_LIST]
    scan_results = await asyncio.gather(*scan_tasks)

    candidates = []
    scanned_summary = []

    for res in scan_results:
        if res:
            scanned_summary.append(res)
            if res["is_entry"] and res["score"] >= MIN_SCORE_ENTRY:
                candidates.append(res)
                logger.info(f"✅ {res['symbol']}: Sinyal VALID! Pemicu: {res['trigger_str']} | Skor: {res['score']}")
            elif res["is_entry"] and res["score"] < MIN_SCORE_ENTRY:
                logger.info(f"🚫 {res['symbol']}: Sinyal diabaikan (Skor {res['score']} < {MIN_SCORE_ENTRY}).")

    scanned_summary.sort(key=lambda x: x["score"], reverse=True)

    signal_payload = {
        "timestamp": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
        "top_signals": scanned_summary[:3],
        "all_scanned": scanned_summary,
    }
    save_signal(signal_payload)

    if len(state["active_positions"]) >= MAX_POSITIONS:
        logger.info("Kapasitas maksimum 3 posisi aktif tercapai. Mengabaikan entry baru.")
        await exchange_spot.close()
        await exchange_futures.close()
        return

    if state["cash_idr"] < ENTRY_CAPITAL_IDR:
        logger.info(f"Kas tidak mencukupi (Sisa Kas: Rp {state['cash_idr']:,.0f}).")
        await exchange_spot.close()
        await exchange_futures.close()
        return

    if not candidates:
        logger.info("— Tidak ada sinyal BELI valid pada siklus ini.")
        await exchange_spot.close()
        await exchange_futures.close()
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)

    held_coins = {p["symbol"]: p for p in state["active_positions"]}
    chosen_winner = None

    for cand in candidates:
        sym = cand["symbol"]
        if sym in held_coins:
            existing_entry_price = held_coins[sym]["entry_price_idr"]
            if cand["harga_idr"] < existing_entry_price:
                chosen_winner = cand
                break
        else:
            chosen_winner = cand
            break

    if not chosen_winner:
        logger.info("— Tidak ada kandidat valid untuk entry baru / average down.")
        await exchange_spot.close()
        await exchange_futures.close()
        return

    amount = ENTRY_CAPITAL_IDR / chosen_winner["harga_idr"]
    new_position = {
        "symbol": chosen_winner["symbol"],
        "pair_name": chosen_winner["pair_name"],
        "entry_price_idr": chosen_winner["harga_idr"],
        "amount": amount,
        "sl": chosen_winner["sl_price"],
        "tp": chosen_winner["tp_price"],
        "entry_time": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
    }

    state["active_positions"].append(new_position)
    state["cash_idr"] -= ENTRY_CAPITAL_IDR
    save_state(state)

    rincian_skor = "\n".join(chosen_winner["breakdown"])
    is_avg_down = chosen_winner["symbol"] in held_coins
    entry_type_label = "AVERAGE DOWN 📉" if is_avg_down else "NEW ENTRY 🚀"

    msg = (
        f"🧪 <b>[PAPER TRADING - HYBRID {entry_type_label}]</b> {chosen_winner['pair_name']}\n"
        f"──────────────────────────────\n"
        f"Pemicu    : {chosen_winner['trigger_str']}\n"
        f"📊 <b>SKOR HYBRID</b>: <code>{chosen_winner['score']}/100</code>\n"
        f"<b>Rincian Skoring</b>:\n{rincian_skor}\n"
        f"──────────────────────────────\n"
        f"Modal In  : Rp {ENTRY_CAPITAL_IDR:,.0f}\n"
        f"Harga In  : Rp {chosen_winner['harga_idr']:,.0f}\n"
        f"Target TP : Rp {chosen_winner['tp_price']:,.0f}\n"
        f"Batas SL  : Rp {chosen_winner['sl_price']:,.0f}\n"
        f"Total Posisi Aktif : {len(state['active_positions'])}/3"
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
    logger.info(f"Notif Entry Terkirim untuk {chosen_winner['pair_name']} ({entry_type_label})")

    await exchange_spot.close()
    await exchange_futures.close()


if __name__ == "__main__":
    asyncio.run(main())
