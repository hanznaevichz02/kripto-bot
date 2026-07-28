"""
========================================================
    KRIPTO BOT — Hybrid Aggressive Edition (Paper Trading)
    Mode    : MULTI-ASSET SCANNER + RANKING SYSTEM (16 Koin)
    Strategi: (Tech Golden Cross / Pullback) OR (SMC Sweep)
    Market  : MURNI SPOT 100% + HYBRID SCORING SYSTEM
========================================================
"""

import os
import asyncio
import json
from datetime import datetime, timedelta, timezone
import ccxt
import pandas as pd
import requests
from telegram import Bot

# --- KONFIGURASI ---
TOKEN               = os.getenv("TELEGRAM_TOKEN")
CHAT_ID             = os.getenv("TELEGRAM_CHAT_ID")

VOL_MULTIPLIER_TECH = 2.0   # Acuan Bot TECH (Mean 3 Candle)
VOL_MULTIPLIER_SMC  = 1.5   # Acuan Bot SMC (Median 20 Candle)
MIN_SCORE_ENTRY     = 70    # Batas minimal skor kelayakan (0 - 100)

INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE          = "paper_trading_hybrid.json"
FEE_TAX_RATE        = 0.013 # Fee + Pajak PMK 68 (1.3%)

ASSET_LIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'SUI/USDT',
    'XRP/USDT', 'LINK/USDT', 'AAVE/USDT', 'DOT/USDT', 'ONDO/USDT',
    'ARB/USDT', 'NEAR/USDT', 'ZEC/USDT', 'TAO/USDT', 'AVAX/USDT',
    'ADA/USDT'
]

# --- MANAJEMEN STATE PAPER TRADING ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "cash_idr": INITIAL_CAPITAL_IDR,
        "active_position": None, 
        "history": [],
        "stats": {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_idr": 0.0}
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

# --- FUNGSI HELPER ---
def get_usd_idr() -> float:
    try:
        r = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(r.json()['ticker']['last'])
    except Exception:
        return 18000.0

def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> dict:
    swing_low = df_4h['low'].iloc[-window-1:-1].min()
    swing_high = df_4h['high'].iloc[-window-1:-1].max()
    return {'swing_high': swing_high, 'swing_low': swing_low}

def hitung_skor_hybrid(ema9_now, ema21_now, is_spike_vol_tech, vol_spike_smc,
                       golden_cross, pullback_bounce, bull_sweep_smc,
                       harga_idr, sl_price, tp_price):
    score = 0
    breakdown = []

    # 1. Tren Utama EMA (20 Poin)
    if ema9_now > ema21_now:
        score += 20
        breakdown.append("• Tren EMA Bullish (+20)")

    # 2. Lonjakan Volume / Liquidity (25 Poin)
    if is_spike_vol_tech or vol_spike_smc:
        score += 25
        breakdown.append("• Volume Spike Konfirmasi (+25)")

    # 3. Kekuatan Sinyal Pemicu (30 Poin)
    if golden_cross:
        score += 30
        breakdown.append("• Golden Cross Momentum (+30)")
    elif pullback_bounce:
        score += 25
        breakdown.append("• Pullback Bounce Konfirmasi (+25)")

    if bull_sweep_smc:
        score += 30
        breakdown.append("• SMC Liquidity Sweep (+30)")

    # 4. Risk-to-Reward Ratio / RRR (25 Poin)
    risk = harga_idr - sl_price
    reward = tp_price - harga_idr
    rrr = (reward / risk) if risk > 0 else 0

    if rrr >= 2.0:
        score += 25
        breakdown.append(f"• RRR Sangat Baik ({rrr:.2f} >= 2.0) (+25)")
    elif rrr >= 1.5:
        score += 15
        breakdown.append(f"• RRR Cukup Baik ({rrr:.2f} >= 1.5) (+15)")

    final_score = min(score, 100)
    return final_score, rrr, breakdown

# --- ANALISA SINGLE KOIN ---
def analisa_koin_hybrid(exchange, symbol, usd_idr):
    try:
        bars_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=30)

        if len(bars_1h) < 40 or len(bars_4h) < 15:
            return None

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        curr_idx = -2
        prev_idx = -3
        c = df_1h.iloc[curr_idx]
        p = df_1h.iloc[prev_idx]

        harga_idr = c['close'] * usd_idr
        high_idr  = c['high'] * usd_idr
        low_idr   = c['low'] * usd_idr

        # ATR 1H
        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()
        atr_idr      = df_1h['atr'].iloc[curr_idx] * usd_idr

        # 1. SMC LOGIC
        avg_vol_smc   = df_1h['volume'].iloc[-22:-2].median()
        candle_range  = c['high'] - c['low']
        lower_wick    = min(c['close'], c['open']) - c['low']
        upper_wick    = c['high'] - max(c['close'], c['open'])
        vol_spike_smc = c['volume'] > (avg_vol_smc * VOL_MULTIPLIER_SMC)

        bull_sweep_smc = (lower_wick > candle_range * 0.35) and vol_spike_smc and (c['close'] >= p['low'])
        bear_sweep_smc = (upper_wick > candle_range * 0.35) and vol_spike_smc and (c['close'] <= p['high'])

        # 2. TEKNIKAL LOGIC
        df_1h['ema9']         = df_1h['close'].ewm(span=9, adjust=False).mean()
        df_1h['ema21']        = df_1h['close'].ewm(span=21, adjust=False).mean()
        df_1h['avg_vol_tech'] = df_1h['volume'].rolling(window=3).mean().shift(1)

        is_spike_vol_tech = c['volume'] > (df_1h['avg_vol_tech'].iloc[curr_idx] * VOL_MULTIPLIER_TECH)

        slope_ema9     = abs(df_1h['ema9'].iloc[curr_idx] - df_1h['ema9'].iloc[prev_idx]) / df_1h['ema9'].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.25
        ema9_now       = df_1h['ema9'].iloc[curr_idx]
        ema9_prev      = df_1h['ema9'].iloc[prev_idx]
        ema21_now      = df_1h['ema21'].iloc[curr_idx]
        ema21_prev     = df_1h['ema21'].iloc[prev_idx]

        golden_cross = (ema9_prev < ema21_prev) and (ema9_now > ema21_now) and is_spike_vol_tech and is_sudut_tajam
        death_cross  = (ema9_prev > ema21_prev) and (ema9_now < ema21_now)

        tren_bullish    = ema9_now > ema21_now
        sentuh_ema21    = c['low'] <= (ema21_now * 1.002)
        tutup_hijau      = c['close'] > c['open']
        tutup_atas_ema9 = c['close'] > ema9_now
        vol_oke_tech    = c['volume'] > df_1h['avg_vol_tech'].iloc[curr_idx]
        pullback_bounce = tren_bullish and sentuh_ema21 and tutup_hijau and tutup_atas_ema9 and vol_oke_tech

        tech_entry_signal = golden_cross or pullback_bounce

        # SWING 4H
        swing = deteksi_swing_4h(df_4h, window=7)
        swing_high_idr = swing['swing_high'] * usd_idr
        swing_low_idr  = swing['swing_low'] * usd_idr

        sl_bullish = swing_low_idr - (0.5 * atr_idr)
        tp_bullish = swing_high_idr

        if tp_bullish <= harga_idr * 1.015: tp_bullish = harga_idr + (3.5 * atr_idr)
        if sl_bullish >= harga_idr * 0.985: sl_bullish = harga_idr - (1.8 * atr_idr)

        # SKORING
        score, rrr, breakdown = hitung_skor_hybrid(
            ema9_now, ema21_now, is_spike_vol_tech, vol_spike_smc,
            golden_cross, pullback_bounce, bull_sweep_smc,
            harga_idr, sl_bullish, tp_bullish
        )

        pemicu_list = []
        if golden_cross: pemicu_list.append("Golden Cross")
        if pullback_bounce: pemicu_list.append("Pullback Bounce")
        if bull_sweep_smc: pemicu_list.append("SMC Bull Sweep")
        trigger_str = " + ".join(pemicu_list)

        return {
            "symbol": symbol,
            "pair_name": symbol.replace('/', '-').replace('USDT', 'IDR'),
            "harga_idr": harga_idr,
            "high_idr": high_idr,
            "low_idr": low_idr,
            "sl_price": sl_bullish,
            "tp_price": tp_bullish,
            "is_entry": (tech_entry_signal or bull_sweep_smc),
            "is_emergency_exit": (death_cross or bear_sweep_smc),
            "emerg_reason": "Death Cross" if death_cross else ("SMC Bear Sweep" if bear_sweep_smc else ""),
            "trigger_str": trigger_str,
            "score": score,
            "rrr": rrr,
            "breakdown": breakdown
        }
    except Exception as e:
        print(f"Error analisa Hybrid {symbol}: {e}")
        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader Hybrid Multi-Asset Scanner...")
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    try: exchange.load_markets()
    except Exception as e: return

    bot     = Bot(token=TOKEN)
    usd_idr = get_usd_idr()
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    state   = load_state()
    pos     = state.get("active_position")

    # =========================================================
    # 1. EKSKLUSIF EVALUASI EXIT (JIKA ADA POSISI OPEN)
    # =========================================================
    if pos:
        symbol = pos["symbol"]
        pair_name = pos["pair_name"]
        data = analisa_koin_hybrid(exchange, symbol, usd_idr)
        if not data: return

        entry_p, amount, sl, tp = pos["entry_price_idr"], pos["amount"], pos["sl"], pos["tp"]
        is_win = data["high_idr"] >= tp
        is_loss = data["low_idr"] <= sl
        is_emerg_exit = data["is_emergency_exit"]

        if is_win or is_loss or is_emerg_exit:
            if is_win:
                exit_reason = "TAKE PROFIT (SWING 4H) 🎯"
                exit_price  = tp
            elif is_loss:
                exit_reason = "STOP LOSS (SWING 4H) 🛑"
                exit_price  = sl
            else:
                exit_reason = f"EMERGENCY EXIT ({data['emerg_reason']}) ⚠️"
                exit_price  = data["harga_idr"]

            gross = exit_price * amount
            net   = gross - (gross * FEE_TAX_RATE)
            modal = entry_p * amount
            pnl_val = net - modal
            pnl_pct = (pnl_val / modal) * 100
            status  = "WIN" if pnl_val > 0 else "LOSS"

            state["cash_idr"] += net
            state["history"].append({
                "pair": pair_name, "pnl_pct": round(pnl_pct, 2), 
                "pnl_idr": round(pnl_val, 2), "status": status,
                "entry_time": pos["entry_time"], "exit_time": now_wib.strftime('%Y-%m-%d %H:%M:%S')
            })
            state["stats"]["total_trades"] += 1
            if status == "WIN": state["stats"]["wins"] += 1
            else: state["stats"]["losses"] += 1
            state["stats"]["total_pnl_idr"] += round(pnl_val, 2)
            state["active_position"] = None
            save_state(state)

            stats = state["stats"]
            wr = (stats["wins"] / stats["total_trades"]) * 100 if stats["total_trades"] > 0 else 0
            msg = (
                f"🧪 *[PAPER TRADING - HYBRID EXIT]* {pair_name}\n"
                f"──────────────────────────────\n"
                f"Alasan   : {exit_reason}\n"
                f"Harga In : Rp {entry_p:,.0f}\n"
                f"Harga Out: Rp {exit_price:,.0f}\n"
                f"P/L      : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
                f"📊 *REKAP TOTAL HYBRID*:\n"
                f"• Total Trade : {stats['total_trades']}x\n"
                f"• Win / Loss  : {stats['wins']} Win / {stats['losses']} Loss (WR: {wr:.1f}%)\n"
                f"• Sisa Kas    : Rp {state['cash_idr']:,.0f}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        return

    # =========================================================
    # 2. SCANNING & RANKING (JIKA KAS KOSONG / AKUN IDLE)
    # =========================================================
    candidates = []
    for symbol in ASSET_LIST:
        res = analisa_koin_hybrid(exchange, symbol, usd_idr)
        if res and res["is_entry"] and res["score"] >= MIN_SCORE_ENTRY:
            candidates.append(res)

    if not candidates:
        print("  — Hybrid Scanner: Tidak ada koin yang lolos kriteria / skor minim.")
        return

    # Sort berdasarkan skor tertinggi (#1)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    winner = candidates[0]

    available_cash = state["cash_idr"]
    if available_cash >= 100_000:
        amount = available_cash / winner["harga_idr"]
        state["active_position"] = {
            "symbol": winner["symbol"],
            "pair_name": winner["pair_name"],
            "entry_price_idr": winner["harga_idr"],
            "amount": amount,
            "sl": winner["sl_price"],
            "tp": winner["tp_price"],
            "entry_time": now_wib.strftime('%Y-%m-%d %H:%M:%S')
        }
        state["cash_idr"] = 0.0
        save_state(state)

        rincian_skor = "\n".join(winner["breakdown"])
        msg = (
            f"🧪 *[PAPER TRADING - HYBRID ENTRY]* {winner['pair_name']}\n"
            f"──────────────────────────────\n"
            f"Pemicu    : {winner['trigger_str']}\n"
            f"📊 *SKOR HYBRID JUARA #1*: `{winner['score']}/100`\n"
            f"*Rincian Skoring*:\n{rincian_skor}\n"
            f"──────────────────────────────\n"
            f"Modal In  : Rp {available_cash:,.0f}\n"
            f"Harga In  : Rp {winner['harga_idr']:,.0f}\n"
            f"Target TP : Rp {winner['tp_price']:,.0f}\n"
            f"Batas SL  : Rp {winner['sl_price']:,.0f}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')

if __name__ == '__main__':
    asyncio.run(main())
