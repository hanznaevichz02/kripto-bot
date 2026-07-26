"""
========================================================
   KRIPTO BOT — Technical Pure Edition (Paper Trading)
   Strategi: Golden Cross (EMA 9/21) + Volume Spike + ATR
   Target  : Khusus ETH-IDR (Fair Head-to-Head vs SMC)
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

VOL_MULTIPLIER      = 2.0
INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE          = "paper_trading_tech.json"
TARGET_SYMBOL       = "ETH/USDT"
TARGET_PAIR_NAME    = "ETH-IDR"

# --- MANAJEMEN STATE PAPER TRADING (FAIR HEAD-TO-HEAD) ---
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
def get_usd_to_idr():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return response.json()['rates']['IDR']
    except Exception:
        return 18000 

# --- KELAS PAPER TRADER TEKNIKAL ---
class TechnicalPaperTrader:
    def __init__(self):
        self.state = load_state()

    def process(self, signal_type, current_price, current_time, sl_price, tp_price):
        # 1. Cek Posisi Aktif (Exit Logic: TP / SL)
        pos = self.state.get("active_position")
        if pos:
            entry_p = pos["entry_price_idr"]
            amount  = pos["amount"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            
            is_win  = current_price >= tp
            is_loss = current_price <= sl
            
            if is_win or is_loss:
                exit_reason = "TAKE PROFIT 🎯" if is_win else "STOP LOSS 🛑"
                final_val   = current_price * amount
                modal_val   = entry_p * amount
                pnl_val     = final_val - modal_val
                pnl_pct     = (pnl_val / modal_val) * 100
                status      = "WIN" if is_win else "LOSS"
                
                # Update kas (sisa kas + nilai akhir posisi)
                self.state["cash_idr"] += final_val
                
                # Rekap history
                trade_record = {
                    "pair": TARGET_PAIR_NAME,
                    "strategy": "TECHNICAL (Golden Cross)",
                    "entry_price": entry_p,
                    "exit_price": current_price,
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
                
                # Susun pesan notifikasi rekap total
                stats    = self.state["stats"]
                win_rate = (stats["wins"] / stats["total_trades"]) * 100 if stats["total_trades"] > 0 else 0
                
                msg = (
                    f"🧪 *[PAPER TRADING - TEKNIKAL EXIT]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Status    : {exit_reason} ({status})\n"
                    f"Harga In  : Rp {entry_p:,.0f}\n"
                    f"Harga Out : Rp {current_price:,.0f}\n"
                    f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
                    f"📊 *REKAP TOTAL TEKNIKAL*:\n"
                    f"• Total Trade : {stats['total_trades']}x\n"
                    f"• Win / Loss  : {stats['wins']} Win / {stats['losses']} Loss\n"
                    f"• Win Rate    : {win_rate:.1f}%\n"
                    f"• Total P/L   : Rp {stats['total_pnl_idr']:+,.0f}\n"
                    f"• Sisa Kas    : Rp {self.state['cash_idr']:,.0f}"
                )
                return msg
            return None

        # 2. Jika Tidak Ada Posisi Aktif, Cari Sinyal Masuk (Entry Logic)
        if not pos and signal_type == "BELI":
            available_cash = self.state["cash_idr"]
            if available_cash >= 100_000: # Batas minimal alokasi
                amount = available_cash / current_price
                self.state["active_position"] = {
                    "entry_price_idr": current_price,
                    "amount": amount,
                    "sl": sl_price,
                    "tp": tp_price,
                    "type": "BELI",
                    "entry_time": current_time
                }
                # Kurangi kas karena uangnya diputar ke posisi beli
                self.state["cash_idr"] = 0.0 
                save_state(self.state)
                
                msg = (
                    f"🧪 *[PAPER TRADING - TEKNIKAL ENTRY]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Strategi  : Golden Cross EMA 9/21 + Vol Spike\n"
                    f"Modal In  : Rp {available_cash:,.0f} (All-in)\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"Target TP : Rp {tp_price:,.0f} (+2 ATR)\n"
                    f"Batas SL  : Rp {sl_price:,.0f} (-1.5 ATR)"
                )
                return msg
        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader Teknikal (ETH-IDR)...")
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

    bot        = Bot(token=TOKEN)
    usd_idr    = get_usd_to_idr()
    now_wib    = datetime.now(timezone.utc) + timedelta(hours=7)
    
    pt_tech = TechnicalPaperTrader()
    
    try:
        bars = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='1h', limit=50)
        if len(bars) < 40:
            return
            
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. Indikator EMA & ATR
        df['ema9']  = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        tr0 = df['high'] - df['low']
        tr1 = (df['high'] - df['close'].shift(1)).abs()
        tr2 = (df['low']  - df['close'].shift(1)).abs()
        df['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df['atr'] = df['tr'].rolling(window=14).mean()
        
        df['avg_vol'] = df['volume'].rolling(window=3).mean().shift(1)
        
        curr_idx = -2
        prev_idx = -3
        curr = df.iloc[curr_idx]
        prev = df.iloc[prev_idx]
        
        is_spike_vol = curr['volume'] > (df['avg_vol'].iloc[curr_idx] * VOL_MULTIPLIER)
        
        harga_idr = curr['close'] * usd_idr
        atr_idr   = curr['atr'] * usd_idr
        
        sl_bullish = harga_idr - (1.5 * atr_idr)
        tp_bullish = harga_idr + (2.0 * atr_idr)
        
        # 2. Kondisi Golden Cross & Sudut Kemiringan EMA
        slope_ema9     = abs(df['ema9'].iloc[curr_idx] - df['ema9'].iloc[prev_idx]) / df['ema9'].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.25 
        
        ema9_now  = df['ema9'].iloc[curr_idx]
        ema9_prev = df['ema9'].iloc[prev_idx]
        ema21_now = df['ema21'].iloc[curr_idx]
        ema21_prev= df['ema21'].iloc[prev_idx]
        
        golden = (ema9_prev < ema21_prev) and (ema9_now > ema21_now)
        
        signal_type = None
        if golden and is_spike_vol and is_sudut_tajam:
            signal_type = "BELI"
            
        # 3. Proses Evaluasi Paper Trading (Keluar/Masuk Posisi)
        pt_msg = pt_tech.process(
            signal_type=signal_type,
            current_price=harga_idr,
            current_time=now_wib.strftime('%Y-%m-%d %H:%M:%S'),
            sl_price=sl_bullish,
            tp_price=tp_bullish
        )
        
        if pt_msg:
            await bot.send_message(chat_id=CHAT_ID, text=pt_msg, parse_mode='Markdown')
            print("  🧪 Notif Simulasi Teknikal ETH-IDR Terkirim")
        else:
            print("  — Teknikal ETH-IDR: Tidak ada aksi (Posisi sedang aktif / Sinyal nihil)")

    except Exception as e:
        print(f"Error pada paper trader teknikal: {e}")

if __name__ == '__main__':
    asyncio.run(main())
