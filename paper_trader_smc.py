"""
========================================================
   KRIPTO BOT — Smart Money Concept (SMC) Paper Trader
   Strategi: Liquidity Sweep (1H) + Swing Structure (4H)
   Target  : Khusus ETH-IDR (Pasar Spot / Buy Only)
   Versi   : 4.1.0 (Fixed High-Precision Exit & JSON Safety)
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

INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE          = "paper_trading_smc.json"
SIGNAL_FILE         = "signal_smc.json"  # File sinyal untuk dibaca bot AGR
TARGET_SYMBOL       = "ETH/USDT"
TARGET_PAIR_NAME    = "ETH-IDR"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68 (1.3% per siklus roundtrip)

# Parameter Skoring SMC
VOL_MULTIPLIER_SMC  = 1.5
MIN_SCORE_ENTRY     = 70   # Batas minimal skor kelayakan entry SMC (0 - 100)

# --- MANAJEMEN STATE PAPER TRADING SMC ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                if "position_capital" not in state:
                    state["position_capital"] = 0.0
                return state
        except Exception:
            pass
    return {
        "status": "IDLE",
        "balance": INITIAL_CAPITAL_IDR,
        "position_capital": 0.0,
        "buy_price": 0.0,
        "buy_time": "",
        "tp": 0.0,
        "sl": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def save_signal(signal_data):
    """Menulis sinyal SMC ke JSON agar bisa dibaca oleh Bot AGR"""
    try:
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f, indent=4)
    except Exception as e:
        print(f"Gagal menyimpan signal SMC: {e}")

def get_usd_idr() -> float:
    try:
        response = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(response.json()['ticker']['last'])
    except Exception:
        return 18000.0

# --- FUNGSI DETEKSI SWING 4H ---
def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> dict:
    swing_low = float(df_4h['low'].iloc[-window-1:-1].min())
    swing_high = float(df_4h['high'].iloc[-window-1:-1].max())
    return {'swing_high': swing_high, 'swing_low': swing_low}

# --- FUNGSI SKORING KUALITAS SMC ---
def hitung_skor_smc(c, avg_vol, ema9_now, ema21_now, harga_idr, sl_price, tp_price):
    score = 0
    breakdown = []

    # 1. Kualitas Liquidity Sweep / Lower Wick Ratio (30 Poin)
    candle_range = c['high'] - c['low']
    lower_wick = min(c['close'], c['open']) - c['low']
    wick_ratio = (lower_wick / candle_range) if candle_range > 0 else 0

    if wick_ratio >= 0.50:
        score += 30
        breakdown.append(f"• Liquidity Sweep Sangat Kuat (Wick {wick_ratio*100:.0f}%) (+30)")
    elif wick_ratio >= 0.35:
        score += 20
        breakdown.append(f"• Liquidity Sweep Valid (Wick {wick_ratio*100:.0f}%) (+20)")

    # 2. Kekuatan Volume Spike (25 Poin)
    vol_ratio = (c['volume'] / avg_vol) if avg_vol > 0 else 0
    if vol_ratio >= 2.0:
        score += 25
        breakdown.append(f"• Volume Spike Sangat Tinggi ({vol_ratio:.1f}x) (+25)")
    elif vol_ratio >= 1.5:
        score += 15
        breakdown.append(f"• Volume Spike Valid ({vol_ratio:.1f}x) (+15)")

    # 3. Keselarasan Tren EMA 1H (20 Poin)
    if ema9_now > ema21_now:
        score += 20
        breakdown.append("• Tren EMA 1H Bullish (+20)")
    elif c['close'] > ema21_now:
        score += 10
        breakdown.append("• Harga di atas EMA21 1H (+10)")

    # 4. Risk-to-Reward Ratio / RRR (25 Poin)
    risk = harga_idr - sl_price
    reward = tp_price - harga_idr
    rrr = (reward / risk) if risk > 0 else 0.0

    if rrr >= 2.0:
        score += 25
        breakdown.append(f"• RRR Sangat Ideal ({rrr:.2f} >= 2.0) (+25)")
    elif rrr >= 1.5:
        score += 15
        breakdown.append(f"• RRR Cukup Ideal ({rrr:.2f} >= 1.5) (+15)")
    else:
        breakdown.append(f"• RRR Kurang Ideal ({rrr:.2f} < 1.5) (+0)")

    final_score = min(score, 100)
    return final_score, rrr, breakdown

# --- KELAS TRADER INDEPENDEN SMC ---
class SMCIndependentTrader:
    def __init__(self):
        self.state = load_state()

    def process_signal(self, raw_bear_sweep, is_bull_entry_valid, current_price, high_price, low_price, 
                       current_time, sl_price, tp_price, score_info=None):
        msg = None
        
        # =========================================================
        # 1. EVALUASI EXIT POSISI SPOT (IN_POSITION)
        # =========================================================
        if self.state["status"] == "IN_POSITION":
            buy_p = self.state["buy_price"]
            tp    = self.state["tp"]
            sl    = self.state["sl"]

            position_cap = self.state.get("position_capital", self.state["balance"])

            is_tp          = high_price >= tp
            is_sl          = low_price <= sl
            is_bear_signal = raw_bear_sweep  # Emergency Exit dari Raw Bear Sweep

            if is_tp or is_sl or is_bear_signal:
                if is_sl:
                    status_title = "🔴 STOP LOSS (SWING 4H)"
                    reason       = "Stop Loss (Swing Low 4H)"
                    exit_price   = sl
                elif is_tp:
                    status_title = "🟢 TAKE PROFIT (SWING 4H)"
                    reason       = "Target TP (Swing High 4H)"
                    exit_price   = tp
                else:
                    status_title = "⚠️ EMERGENCY EXIT (SMC)"
                    reason       = "Sinyal BEAR_SWEEP Terdeteksi"
                    exit_price   = current_price

                gross_pct = (exit_price - buy_p) / buy_p
                net_pct   = gross_pct - FEE_TAX_RATE
                pnl_rp    = position_cap * net_pct
                
                self.state["balance"]          = float(position_cap + pnl_rp)
                self.state["position_capital"] = 0.0
                self.state["status"]           = "IDLE"
                self.state["buy_price"]        = 0.0
                self.state["tp"]               = 0.0
                self.state["sl"]               = 0.0
                self.state["total_trades"]    += 1

                if pnl_rp > 0:
                    self.state["wins"] += 1
                else:
                    self.state["losses"] += 1

                win_rate = (self.state["wins"] / self.state["total_trades"]) * 100 if self.state["total_trades"] > 0 else 0

                msg = (
                    f"🧪 *[PAPER TRADING - SMC SWING]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Status    : {status_title}\n"
                    f"• Alasan  : {reason}\n"
                    f"• Harga In: Rp {buy_p:,.0f}\n"
                    f"• Harga Out: Rp {exit_price:,.0f}\n"
                    f"• P/L     : {net_pct*100:+.2f}% (Rp {pnl_rp:+,.0f})\n"
                    f"• Saldo   : Rp {self.state['balance']:,.0f}\n"
                    f"• Win Rate: {win_rate:.1f}% ({self.state['wins']}/{self.state['total_trades']} Trade)"
                )
                save_state(self.state)
                return msg

            if is_bull_entry_valid:
                print(f"    🔒 [GUARD] Sinyal BULL_SWEEP diabaikan! Posisi SMC {TARGET_PAIR_NAME} masih IN_POSITION.")

            return None

        # =========================================================
        # 2. EVALUASI ENTRY POSISI SPOT (IDLE)
        # =========================================================
        elif self.state["status"] == "IDLE":
            if is_bull_entry_valid:
                self.state["status"]           = "IN_POSITION"
                self.state["position_capital"] = float(self.state["balance"])
                self.state["buy_price"]        = float(current_price)
                self.state["buy_time"]         = str(current_time)
                self.state["tp"]               = float(tp_price)
                self.state["sl"]               = float(sl_price)
                save_state(self.state)

                score_str = ""
                if score_info:
                    score, rrr, breakdown = score_info
                    score_str = (
                        f"📊 *SKOR ENTRY SMC*: `{score}/100` (Min: {MIN_SCORE_ENTRY})\n"
                        f"*Rincian Skoring*:\n" + "\n".join(breakdown) + "\n"
                        f"──────────────────────────────\n"
                    )

                msg = (
                    f"🧪 *[PAPER TRADING - SMC SWING]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Strategi  : Sweep 1H + Swing 4H (Spot)\n"
                    f"{score_str}"
                    f"Modal In  : Rp {self.state['position_capital']:,.0f}\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"Target TP : Rp {tp_price:,.0f} (Swing High 4H)\n"
                    f"Batas SL  : Rp {sl_price:,.0f} (Swing Low 4H)"
                )
                return msg

        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader SMC Swing 4H + Scoring System (ETH-IDR Spot)...")
    exchange = ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'timeout': 30000
    })
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market: {e}")
        return

    bot     = Bot(token=TOKEN)
    usd_idr = get_usd_idr()
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    
    trader = SMCIndependentTrader()
    
    try:
        # Pull Data Multi-Timeframe: 1H & 4H
        bars_1h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='4h', limit=30)
        
        if len(bars_1h) < 40 or len(bars_4h) < 15:
            return
            
        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        
        # Indikator Tambahan 1H (EMA & ATR)
        df_1h['ema9']  = df_1h['close'].ewm(span=9, adjust=False).mean()
        df_1h['ema21'] = df_1h['close'].ewm(span=21, adjust=False).mean()

        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()

        # Deteksi Signal Sweep di 1H
        avg_vol  = float(df_1h['volume'].iloc[-21:-1].median())
        curr_idx = -2
        prev_idx = -3
        c        = df_1h.iloc[curr_idx]
        p        = df_1h.iloc[prev_idx]
        latest_c = df_1h.iloc[-1]  # Candle running terbaru
        
        candle_range   = c['high'] - c['low']
        lower_wick     = min(c['close'], c['open']) - c['low']
        upper_wick     = c['high'] - max(c['close'], c['open'])
        vol_spike_smc  = c['volume'] > (avg_vol * VOL_MULTIPLIER_SMC)

        raw_bull_sweep = bool((lower_wick > candle_range * 0.35) and vol_spike_smc and (c['close'] >= p['low']))
        raw_bear_sweep = bool((upper_wick > candle_range * 0.35) and vol_spike_smc and (c['close'] <= p['high']))

        harga_idr = float(c['close'] * usd_idr)
        high_idr  = float(max(c['high'], latest_c['high']) * usd_idr) # Pakai high tertinggi dari closed & running candle
        low_idr   = float(min(c['low'], latest_c['low']) * usd_idr)   # Pakai low terendah dari closed & running candle
        atr_idr   = float(df_1h['atr'].iloc[curr_idx] * usd_idr)
        
        # Kalkulasi Swing High & Swing Low 4H
        swing          = deteksi_swing_4h(df_4h, window=7)
        swing_high_idr = swing['swing_high'] * usd_idr
        swing_low_idr  = swing['swing_low'] * usd_idr
        
        # Perhitungan SL & TP Swing
        sl_bullish = float(swing_low_idr - (0.5 * atr_idr))
        tp_bullish = float(swing_high_idr)

        # Filter Pengaman
        if tp_bullish <= harga_idr * 1.015:
            tp_bullish = float(harga_idr + (3.5 * atr_idr))
            
        if sl_bullish >= harga_idr * 0.985:
            sl_bullish = float(harga_idr - (1.8 * atr_idr))
        
        # EVALUASI SINYAL DENGAN SCORING SYSTEM
        is_bull_entry_valid = False
        score_info          = None
        ema9_now            = float(df_1h['ema9'].iloc[curr_idx])
        ema21_now           = float(df_1h['ema21'].iloc[curr_idx])

        if raw_bull_sweep:
            score, rrr, breakdown = hitung_skor_smc(
                c, avg_vol, ema9_now, ema21_now, harga_idr, sl_bullish, tp_bullish
            )
            score_info = (score, rrr, breakdown)

            if score >= MIN_SCORE_ENTRY:
                is_bull_entry_valid = True
                print(f"    🎯 [SMC SKOR PASS] Entry Disetujui! Skor: {score}/{MIN_SCORE_ENTRY}")
            else:
                print(f"    ⚠️ [SMC SKOR FAIL] Sinyal Bull Sweep Terdeteksi Tapi Skor ({score}) < {MIN_SCORE_ENTRY}. Dibatalkan.")

        now_w_ib_str = now_wib.strftime('%Y-%m-%d %H:%M:%S')

        # EXPORT SIGNAL UNTUK BOT AGR (HANYA JIKA ADA SINYAL AKTIF)
        signal_type_export = "BULL_SWEEP" if is_bull_entry_valid else ("BEAR_SWEEP" if raw_bear_sweep else None)
        
        if signal_type_export:
            signal_payload = {
                "timestamp": now_w_ib_str,
                "symbol": TARGET_SYMBOL,
                "signal_type": signal_type_export,
                "score": score_info[0] if score_info else 0,
                "rrr": score_info[1] if score_info else 0.0,
                "current_price": harga_idr,
                "high_price": high_idr,
                "low_price": low_idr,
                "sl_price": sl_bullish,
                "tp_price": tp_bullish
            }
            save_signal(signal_payload)

        # PROSES SIMULASI TRADING
        pt_msg = trader.process_signal(
            raw_bear_sweep=raw_bear_sweep,
            is_bull_entry_valid=is_bull_entry_valid,
            current_price=harga_idr,
            high_price=high_idr,
            low_price=low_idr,
            current_time=now_w_ib_str,
            sl_price=sl_bullish,
            tp_price=tp_bullish,
            score_info=score_info
        )
        
        if pt_msg:
            await bot.send_message(chat_id=CHAT_ID, text=pt_msg, parse_mode='Markdown')
            print("    🧪 Notif Simulasi SMC Swing ETH-IDR Terkirim")
        else:
            print("    — SMC Swing ETH-IDR: Tidak ada aksi (Posisi aktif / Sinyal nihil / Skor tidak memenuhi)")

    except Exception as e:
        print(f"Error pada paper trader SMC: {e}")

if __name__ == '__main__':
    asyncio.run(main())
