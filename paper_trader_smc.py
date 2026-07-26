"""
========================================================
   KRIPTO BOT — Smart Money Concept (SMC) Paper Trader
   Strategi: Liquidity Sweep + Order Block + ATR TP/SL
   Target  : Khusus ETH-IDR (Fair Head-to-Head vs Teknikal)
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
STATE_FILE          = "paper_trading_smc.json"  # Dipisah biar tidak bentrok
TARGET_SYMBOL       = "ETH/USDT"
TARGET_PAIR_NAME    = "ETH-IDR"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68

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
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 18000.0

class SMCIndependentTrader:
    def __init__(self):
        self.state = load_state()

    def process_signal(self, signal_type, current_price, current_time, sl_price, tp_price):
        msg = None
        
        # 1. Cek Exit (TP / SL / Bear Sweep)
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
                    status_title = "🟢 TAKE PROFIT (VIRTUAL SMC)"
                else:
                    self.state["losses"] += 1
                    status_title = "🔴 EXIT / STOP LOSS (VIRTUAL SMC)"

                reason = "Target TP (+2 ATR)" if is_tp else ("Stop Loss (-1.5 ATR)" if is_sl else "Sinyal BEAR_SWEEP")
                win_rate = (self.state["wins"] / self.state["total_trades"]) * 100 if self.state["total_trades"] > 0 else 0

                msg = (
                    f"🧪 *[PAPER TRADING - SMC]* {TARGET_PAIR_NAME}\n"
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

        # 2. Cek Entry (Bull Sweep)
        elif self.state["status"] == "IDLE":
            if signal_type == "BULL_SWEEP":
                self.state["status"] = "IN_POSITION"
                self.state["buy_price"] = current_price
                self.state["buy_time"] = str(current_time)
                self.state["tp"] = tp_price
                self.state["sl"] = sl_price
                save_state(self.state)

                msg = (
                    f"🧪 *[PAPER TRADING - SMC]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Strategi  : Liquidity Sweep + ATR\n"
                    f"Modal In  : Rp {self.state['balance']:,.0f}\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"Target TP : Rp {tp_price:,.0f} (+2 ATR)\n"
                    f"Batas SL  : Rp {sl_price:,.0f} (-1.5 ATR)"
                )
                return msg

        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader SMC ATR (ETH-IDR)...")
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
        bars = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='1h', limit=50)
        if len(bars) < 40:
            return
            
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Kalkulasi ATR (14)
        tr0 = df['high'] - df['low']
        tr1 = (df['high'] - df['close'].shift(1)).abs()
        tr2 = (df['low']  - df['close'].shift(1)).abs()
        df['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df['atr'] = df['tr'].rolling(window=14).mean()

        avg_vol = df['volume'].iloc[-21:-1].median()
        curr_idx = -2
        prev_idx = -3
        c = df.iloc[curr_idx]
        p = df.iloc[prev_idx]
        
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
        atr_idr   = df['atr'].iloc[curr_idx] * usd_idr
        
        sl_bullish = harga_idr - (1.5 * atr_idr)
        tp_bullish = harga_idr + (2.0 * atr_idr)
        
        pt_msg = trader.process_signal(
            signal_type=signal_type,
            current_price=harga_idr,
            current_time=now_wib.strftime('%Y-%m-%d %H:%M:%S'),
            sl_price=sl_bullish,
            tp_price=tp_bullish
        )
        
        if pt_msg:
            await bot.send_message(chat_id=CHAT_ID, text=pt_msg, parse_mode='Markdown')
            print("  🧪 Notif Simulasi SMC ATR ETH-IDR Terkirim")
        else:
            print("  — SMC ATR ETH-IDR: Tidak ada aksi (Posisi aktif / Sinyal nihil)")

### 3. Ringkasan Perubahan Utama:
* **ATR Ditambahkan:** Sekarang bot SMC menghitung indikator ATR (14 periode) yang diselaraskan dengan skrip teknikal.
* **TP/SL Dinamis:** Target TP dipasang di `+2.0 ATR` dan Stop Loss di `-1.5 ATR`.
* **State File Dipisah:** Menggunakan `paper_trading_smc.json` agar file penyimpanannya independen dan tidak bentrok dengan data bot teknikal.
