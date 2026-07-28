"""
========================================================
    KRIPTO BOT — Hybrid Aggressive Edition (Paper Trading)
    Strategi: (Tech Golden Cross / Pullback) OR (SMC Sweep)
    Target  : Khusus ETH-IDR (Fair Head-to-Head Arena)
    Market  : MURNI SPOT 100%
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

# Pengali Volume Masing-masing Strategi
VOL_MULTIPLIER_TECH = 2.0  # Acuan Bot TECH (Mean 3 Candle)
VOL_MULTIPLIER_SMC  = 1.5  # Acuan Bot SMC (Median 20 Candle)

INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE          = "paper_trading_hybrid.json"
TARGET_SYMBOL       = "ETH/USDT"
TARGET_PAIR_NAME    = "ETH-IDR"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68 (1.3%)

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
    """
    Mencari Swing Low & Swing High dari N candle 4H terakhir.
    """
    swing_low = df_4h['low'].iloc[-window-1:-1].min()
    swing_high = df_4h['high'].iloc[-window-1:-1].max()
    return {'swing_high': swing_high, 'swing_low': swing_low}

# --- KELAS PAPER TRADER HYBRID ---
class HybridPaperTrader:
    def __init__(self):
        self.state = load_state()

    def process(self, signal_type, current_price, high_price, low_price, current_time, sl_price, tp_price, trigger_source):
        pos = self.state.get("active_position")

        # =========================================================
        # 1. JIKA ADA POSISI AKTIF (OPEN) -> HANYA EVALUASI EXIT
        # =========================================================
        if pos:
            entry_p = pos["entry_price_idr"]
            amount  = pos["amount"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            strat_name = pos.get("strategy", "HYBRID")

            # Deteksi presisi menggunakan High/Low candle
            is_win        = high_price >= tp
            is_loss       = low_price <= sl
            is_emerg_exit = signal_type == "EXIT_EMERGENCY"  # Death Cross / Bear Sweep

            if is_win or is_loss or is_emerg_exit:
                if is_win:
                    exit_reason = "TAKE PROFIT (SWING 4H) 🎯"
                    exit_price  = tp
                elif is_loss:
                    exit_reason = "STOP LOSS (SWING 4H) 🛑"
                    exit_price  = sl
                else:
                    exit_reason = f"EMERGENCY EXIT ({trigger_source}) ⚠️"
                    exit_price  = current_price

                gross_final_val = exit_price * amount
                fee_tax_amount  = gross_final_val * FEE_TAX_RATE
                net_final_val   = gross_final_val - fee_tax_amount

                modal_val       = entry_p * amount
                pnl_val         = net_final_val - modal_val
                pnl_pct         = (pnl_val / modal_val) * 100
                status          = "WIN" if pnl_val > 0 else "LOSS"

                # Update kas
                self.state["cash_idr"] += net_final_val

                # Rekap history
                trade_record = {
                    "pair": TARGET_PAIR_NAME,
                    "strategy": strat_name,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_idr": round(pnl_val, 2),
                    "status": status,
                    "entry_time": pos["entry_time"],
                    "exit_time": current_time
                }
                self.state["history"].append(trade_record)

                # Update stats
                self.state["stats"]["total_trades"] += 1
                if status == "WIN":
                    self.state["stats"]["wins"] += 1
                else:
                    self.state["stats"]["losses"] += 1
                self.state["stats"]["total_pnl_idr"] += round(pnl_val, 2)

                # Bersihkan posisi aktif
                self.state["active_position"] = None
                save_state(self.state)

                # Susun pesan notifikasi
                stats    = self.state["stats"]
                win_rate = (stats["wins"] / stats["total_trades"]) * 100 if stats["total_trades"] > 0 else 0

                msg = (
                    f"🧪 *[PAPER TRADING - HYBRID EXIT]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Alasan    : {exit_reason}\n"
                    f"Strategi  : {strat_name}\n"
                    f"Harga In  : Rp {entry_p:,.0f}\n"
                    f"Harga Out : Rp {exit_price:,.0f}\n"
                    f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
                    f"📊 *REKAP TOTAL HYBRID*:\n"
                    f"• Total Trade : {stats['total_trades']}x\n"
                    f"• Win / Loss  : {stats['wins']} Win / {stats['losses']} Loss\n"
                    f"• Win Rate    : {win_rate:.1f}%\n"
                    f"• Total P/L   : Rp {stats['total_pnl_idr']:+,.0f}\n"
                    f"• Sisa Kas    : Rp {self.state['cash_idr']:,.0f}"
                )
                return msg

            # PROTEKSI UTAMA: Jika posisi masih OPEN, abaikan sinyal Beli baru
            if signal_type == "BELI":
                print(f"  🔒 [GUARD] Sinyal BELI ({trigger_source}) diabaikan! Posisi {TARGET_PAIR_NAME} masih OPEN.")
            return None

        # =========================================================
        # 2. JIKA TIDAK ADA POSISI AKTIF -> BARU BISA ENTRY BELI
        # =========================================================
        if signal_type == "BELI":
            available_cash = self.state["cash_idr"]
            if available_cash >= 100_000:  # Batas minimal alokasi
                amount = available_cash / current_price

                self.state["active_position"] = {
                    "entry_price_idr": current_price,
                    "amount": amount,
                    "sl": sl_price,
                    "tp": tp_price,
                    "type": "BELI",
                    "strategy": f"Hybrid ({trigger_source}) + Swing 4H",
                    "entry_time": current_time
                }
                self.state["cash_idr"] = 0.0  # Spot Murni (All-in)
                save_state(self.state)

                msg = (
                    f"🧪 *[PAPER TRADING - HYBRID ENTRY]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Pemicu    : {trigger_source}\n"
                    f"Modal In  : Rp {available_cash:,.0f} (All-in Spot)\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"Target TP : Rp {tp_price:,.0f} (Swing High 4H)\n"
                    f"Batas SL  : Rp {sl_price:,.0f} (Swing Low 4H)"
                )
                return msg

        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader Hybrid Gercep (ETH-IDR Spot)...")
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

    pt_hybrid = HybridPaperTrader()

    try:
        # Pull Data Multi-Timeframe: 1H & 4H
        bars_1h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='4h', limit=30)

        if len(bars_1h) < 40 or len(bars_4h) < 15:
            return

        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        curr_idx = -2
        prev_idx = -3
        c = df_1h.iloc[curr_idx]
        p = df_1h.iloc[prev_idx]

        harga_idr = c['close'] * usd_idr
        high_idr  = c['high'] * usd_idr
        low_idr   = c['low'] * usd_idr

        # --- ATR 1H (Buffer SL) ---
        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()
        atr_idr      = df_1h['atr'].iloc[curr_idx] * usd_idr

        # =========================================================
        # 1. ANALISA SMC
        # =========================================================
        avg_vol_smc   = df_1h['volume'].iloc[-22:-2].median()  # Fix rentang 20 candle historis
        candle_range  = c['high'] - c['low']
        lower_wick    = min(c['close'], c['open']) - c['low']
        upper_wick    = c['high'] - max(c['close'], c['open'])
        vol_spike_smc = c['volume'] > (avg_vol_smc * VOL_MULTIPLIER_SMC)

        bull_sweep_smc = (lower_wick > candle_range * 0.35) and vol_spike_smc and (c['close'] >= p['low'])
        bear_sweep_smc = (upper_wick > candle_range * 0.35) and vol_spike_smc and (c['close'] <= p['high'])

        # =========================================================
        # 2. ANALISA TEKNIKAL
        # =========================================================
        df_1h['ema9']         = df_1h['close'].ewm(span=9, adjust=False).mean()
        df_1h['ema21']        = df_1h['close'].ewm(span=21, adjust=False).mean()
        df_1h['avg_vol_tech'] = df_1h['volume'].rolling(window=3).mean().shift(1)

        is_spike_vol_tech = c['volume'] > (df_1h['avg_vol_tech'].iloc[curr_idx] * VOL_MULTIPLIER_TECH)

        # Golden Cross & Death Cross
        slope_ema9     = abs(df_1h['ema9'].iloc[curr_idx] - df_1h['ema9'].iloc[prev_idx]) / df_1h['ema9'].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.25 
        ema9_now       = df_1h['ema9'].iloc[curr_idx]
        ema9_prev      = df_1h['ema9'].iloc[prev_idx]
        ema21_now      = df_1h['ema21'].iloc[curr_idx]
        ema21_prev     = df_1h['ema21'].iloc[prev_idx]

        golden_cross = (ema9_prev < ema21_prev) and (ema9_now > ema21_now) and is_spike_vol_tech and is_sudut_tajam
        death_cross  = (ema9_prev > ema21_prev) and (ema9_now < ema21_now)

        # Pullback Bounce
        tren_bullish    = ema9_now > ema21_now
        sentuh_ema21    = c['low'] <= (ema21_now * 1.002)
        tutup_hijau      = c['close'] > c['open']
        tutup_atas_ema9 = c['close'] > ema9_now
        vol_oke_tech    = c['volume'] > df_1h['avg_vol_tech'].iloc[curr_idx]
        pullback_bounce = tren_bullish and sentuh_ema21 and tutup_hijau and tutup_atas_ema9 and vol_oke_tech

        tech_entry_signal = golden_cross or pullback_bounce

        # =========================================================
        # 3. KALKULASI SWING 4H UNTUK SL & TP
        # =========================================================
        swing = deteksi_swing_4h(df_4h, window=7)
        swing_high_idr = swing['swing_high'] * usd_idr
        swing_low_idr  = swing['swing_low'] * usd_idr

        sl_bullish = swing_low_idr - (0.5 * atr_idr)
        tp_bullish = swing_high_idr

        if tp_bullish <= harga_idr * 1.015:
            tp_bullish = harga_idr + (3.5 * atr_idr)
        if sl_bullish >= harga_idr * 0.985:
            sl_bullish = harga_idr - (1.8 * atr_idr)

        # =========================================================
        # 4. LOGIKA HYBRID ENTRY & EXIT
        # =========================================================
        signal_type = None
        trigger_source = ""

        # Evaluasi Sinyal Entry
        if tech_entry_signal:
            signal_type = "BELI"
            trigger_source = "Golden Cross" if golden_cross else "Pullback Bounce"
        elif bull_sweep_smc:
            signal_type = "BELI"
            trigger_source = "SMC Bull Sweep"

        # Evaluasi Sinyal Exit Darurat
        elif death_cross or bear_sweep_smc:
            signal_type = "EXIT_EMERGENCY"
            trigger_source = "Death Cross" if death_cross else "SMC Bear Sweep"

        # =========================================================
        # 5. EVALUASI PAPER TRADING
        # =========================================================
        pt_msg = pt_hybrid.process(
            signal_type=signal_type,
            current_price=harga_idr,
            high_price=high_idr,
            low_price=low_idr,
            current_time=now_wib.strftime('%Y-%m-%d %H:%M:%S'),
            sl_price=sl_bullish,
            tp_price=tp_bullish,
            trigger_source=trigger_source
        )

        if pt_msg:
            await bot.send_message(chat_id=CHAT_ID, text=pt_msg, parse_mode='Markdown')
            print("   🧪 Notif Simulasi Hybrid ETH-IDR Terkirim")
        else:
            print("   — Hybrid ETH-IDR: Tidak ada aksi (Posisi aktif / Sinyal nihil)")

    except Exception as e:
        print(f"Error pada paper trader hybrid: {e}")

if __name__ == '__main__':
    asyncio.run(main())
