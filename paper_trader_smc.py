"""
========================================================
   KRIPTO BOT — Smart Money Concept (SMC) Paper Trader
   Strategi: Liquidity Sweep (1H) + Swing Structure (4H)
   Target  : Khusus ETH-IDR (Pasar Spot / Buy Only)
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
TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE          = "paper_trading_smc.json"
TARGET_SYMBOL       = "ETH/USDT"
TARGET_PAIR_NAME    = "ETH-IDR"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68 (0.13% / 0.0013 x 10 = 0.013 per siklus roundtrip)

# --- MANAJEMEN STATE PAPER TRADING SMC ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "status": "IDLE",
        "balance": INITIAL_CAPITAL_IDR,
        "buy_price": 0,
        "buy_time": "",
        "tp": 0,
        "sl": 0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def get_usd_to_idr():
    try:
        response = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(response.json()['ticker']['last'])
    except Exception:
        return 18000.0

# --- FUNGSI DETEKSI SWING 4H ---
def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> dict:
    """
    Mencari titik terendah (Swing Low) dan tertinggi (Swing High)
    dari N candle 4H terakhir untuk batas pertahanan Spot.
    """
    # Mengabaikan candle running (-1) agar swing tidak berubah-ubah di tengah candle
    swing_low = df_4h['low'].iloc[-window-1:-1].min()
    swing_high = df_4h['high'].iloc[-window-1:-1].max()
    return {'swing_high': swing_high, 'swing_low': swing_low}

class SMCIndependentTrader:
    def __init__(self):
        self.state = load_state()

    def process_signal(self, signal_type, current_price, current_time, sl_price, tp_price):
        msg = None
        
        # 1. CEK EXIT POSISI SPOT (TP / SL / Sinyal Bearish Emergency)
        if self.state["status"] == "IN_POSITION":
            buy_p = self.state["buy_price"]
            tp    = self.state["tp"]
            sl    = self.state["sl"]

            is_tp = current_price >= tp
            is_sl = current_price <= sl
            is_bear_signal = signal_type == "BEAR_SWEEP"

            if is_tp or is_sl or is_bear_signal:
                gross_pct = ((current_price - buy_p) / buy_p)
                net_pct = gross_pct - FEE_TAX_RATE
                
                pnl_rp = self.state["balance"] * net_pct
                self.state["balance"] += pnl_rp
                self.state["status"] = "IDLE"
                self.state["total_trades"] += 1

                if pnl_rp > 0:
                    self.state["wins"] += 1
                    status_title = "🟢 TAKE PROFIT (SWING 4H)"
                else:
                    self.state["losses"] += 1
                    status_title = "🔴 EXIT / STOP LOSS (SWING 4H)"

                reason = "Target TP (Swing High 4H)" if is_tp else ("Stop Loss (Swing Low 4H)" if is_sl else "Sinyal BEAR_SWEEP")
                win_rate = (self.state["wins"] / self.state["total_trades"]) * 100 if self.state["total_trades"] > 0 else 0

                msg = (
                    f"🧪 *[PAPER TRADING - SMC SWING]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Status    : {status_title}\n"
                    f"• Alasan  : {reason}\n"
                    f"• Harga In: Rp {buy_p:,.0f}\n"
                    f"• Harga Out: Rp {current_price:,.0f}\n"
                    f"• P/L     : {net_pct*100:+.2f}% (Rp {pnl_rp:+,.0f})\n"
                    f"• Saldo   : Rp {self.state['balance']:,.0f}\n"
                    f"• Win Rate: {win_rate:.1f}% ({self.state['wins']}/{self.state['total_trades']} Trade)"
                )
                save_state(self.state)
                return msg

        # 2. CEK ENTRY POSISI SPOT (Hanya Beli saat Bull Sweep)
        elif self.state["status"] == "IDLE":
            if signal_type == "BULL_SWEEP":
                self.state["status"] = "IN_POSITION"
                self.state["buy_price"] = current_price
                self.state["buy_time"] = str(current_time)
                self.state["tp"] = tp_price
                self.state["sl"] = sl_price
                save_state(self.state)

                msg = (
                    f"🧪 *[PAPER TRADING - SMC SWING]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Strategi  : Sweep 1H + Swing 4H (Spot)\n"
                    f"Modal In  : Rp {self.state['balance']:,.0f}\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"Target TP : Rp {tp_price:,.0f} (Swing High 4H)\n"
                    f"Batas SL  : Rp {sl_price:,.0f} (Swing Low 4H)"
                )
                return msg

        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader SMC Swing 4H (ETH-IDR Spot)...")
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
    usd_idr = get_usd_to_idr()
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    
    trader = SMCIndependentTrader()
    
    try:
        # Pull Data Multi-Timeframe: 1H (Trigger) & 4H (Structure)
        bars_1h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='4h', limit=30)
        
        if len(bars_1h) < 40 or len(bars_4h) < 15:
            return
            
        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Kalkulasi ATR 1H (Sebagai Buffer)
        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()

        # Deteksi Signal Sweep di 1H
        avg_vol = df_1h['volume'].iloc[-21:-1].median()
        curr_idx = -2
        prev_idx = -3
        c = df_1h.iloc[curr_idx]
        p = df_1h.iloc[prev_idx]
        
        candle_range = c['high'] - c['low']
        lower_wick = min(c['close'], c['open']) - c['low']
        upper_wick = c['high'] - max(c['close'], c['open'])
        vol_spike = c['volume'] > avg_vol * 1.5

        bull_sweep = (lower_wick > candle_range * 0.35) and vol_spike and (c['close'] >= p['low'])
        bear_sweep = (upper_wick > candle_range * 0.35) and vol_spike and (c['close'] <= p['high'])

        signal_type = None
        if bull_sweep:
            signal_type = "BULL_SWEEP"
        elif bear_sweep:
            signal_type = "BEAR_SWEEP"

        harga_idr = c['close'] * usd_idr
        atr_idr   = df_1h['atr'].iloc[curr_idx] * usd_idr
        
        # Kalkulasi Swing High & Swing Low 4H
        swing = deteksi_swing_4h(df_4h, window=7)
        swing_high_idr = swing['swing_high'] * usd_idr
        swing_low_idr  = swing['swing_low'] * usd_idr
        
        # LOGIKA PERBAIKAN SL & TP SWING (SPOT FRIENDLY)
        # SL = Berada di bawah Swing Low 4H + buffer 0.5 ATR
        sl_bullish = swing_low_idr - (0.5 * atr_idr)
        # TP = Mengincar Puncak Swing High 4H
        tp_bullish = swing_high_idr

        # FILTER PENGAMAN (Emergency Fallback)
        # Jika Swing High 4H terlalu dekat/di bawah harga beli saat ini
        if tp_bullish <= harga_idr * 1.015:
            tp_bullish = harga_idr + (3.5 * atr_idr)
            
        # Jika Swing Low 4H terlalu dekat/di atas harga beli saat ini
        if sl_bullish >= harga_idr * 0.985:
            sl_bullish = harga_idr - (1.8 * atr_idr)
        
        now_w_ib_str = now_wib.strftime('%Y-%m-%d %H:%M:%S')
        pt_msg = trader.process_signal(
            signal_type=signal_type,
            current_price=harga_idr,
            current_time=now_w_ib_str,
            sl_price=sl_bullish,
            tp_price=tp_bullish
        )
        
        if pt_msg:
            await bot.send_message(chat_id=CHAT_ID, text=pt_msg, parse_mode='Markdown')
            print("  🧪 Notif Simulasi SMC Swing ETH-IDR Terkirim")
        else:
            print("  — SMC Swing ETH-IDR: Tidak ada aksi (Posisi aktif / Sinyal nihil)")

    except Exception as e:
        print(f"Error pada paper trader SMC: {e}")

if __name__ == '__main__':
    asyncio.run(main())
