"""
========================================================
   KRIPTO BOT — Technical Pure Edition (Paper Trading)
   Strategi: Golden Cross & Pullback Bounce + Swing 4H
   Target  : Khusus ETH-IDR (Fair Head-to-Head vs SMC)
   Market  : MURNI SPOT 100%
   Versi   : 3.8.0 (Fixed Vol Window, State Safety & Signal Export)
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

VOL_MULTIPLIER      = 2.0
INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE          = "paper_trading_tech.json"
SIGNAL_FILE         = "signal_tech.json"  # File sinyal untuk dibaca bot AGR / Monitoring
TARGET_SYMBOL       = "ETH/USDT"
TARGET_PAIR_NAME    = "ETH-IDR"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68 (1.3% per siklus roundtrip)

# --- MANAJEMEN STATE PAPER TRADING ---
def load_state():
    default_state = {
        "cash_idr": INITIAL_CAPITAL_IDR,
        "active_position": None, 
        "history": [],
        "stats": {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_idr": 0.0}
    }
    
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                # FIX BUG 2: Proteksi key yang hilang jika membaca file lama
                if "cash_idr" not in state:
                    state["cash_idr"] = INITIAL_CAPITAL_IDR
                if "active_position" not in state:
                    state["active_position"] = None
                if "history" not in state:
                    state["history"] = []
                if "stats" not in state:
                    state["stats"] = {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_idr": 0.0}
                return state
        except Exception:
            pass
    return default_state

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def save_signal(signal_data):
    """Menulis sinyal Teknikal ke JSON untuk integrasi monitoring / Bot AGR"""
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal_data, f, indent=4)

# --- FUNGSI HELPER ---
def get_usd_idr():
    try:
        response = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(response.json()['ticker']['last'])
    except Exception:
        return 18000.0

def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> dict:
    """
    Mencari Swing Low & Swing High dari N candle 4H terakhir.
    """
    swing_low = float(df_4h['low'].iloc[-window-1:-1].min())
    swing_high = float(df_4h['high'].iloc[-window-1:-1].max())
    return {'swing_high': swing_high, 'swing_low': swing_low}

# --- KELAS PAPER TRADER TEKNIKAL ---
class TechnicalPaperTrader:
    def __init__(self):
        self.state = load_state()

    def process(self, signal_type, current_price, high_price, low_price, current_time, sl_price, tp_price):
        pos = self.state.get("active_position")

        # =========================================================
        # 1. JIKA ADA POSISI AKTIF -> EVALUASI EXIT
        # =========================================================
        if pos:
            entry_p    = pos["entry_price_idr"]
            amount     = pos["amount"]
            sl         = pos["sl"]
            tp         = pos["tp"]
            strat_name = pos.get("strategy", "TECHNICAL")
            
            # Deteksi presisi menggunakan High/Low candle untuk eksekusi wick/jarum
            is_win        = high_price >= tp
            is_loss       = low_price <= sl
            is_death_exit = signal_type == "JUAL"  # Death Cross Emergency Exit
            
            if is_win or is_loss or is_death_exit:
                # Evaluasi Stop Loss terlebih dahulu untuk manajemen risiko konservatif
                if is_loss:
                    exit_reason = "STOP LOSS (SWING 4H) 🛑"
                    exit_price  = sl
                elif is_win:
                    exit_reason = "TAKE PROFIT (SWING 4H) 🎯"
                    exit_price  = tp
                else:
                    exit_reason = "DEATH CROSS EXIT ⚠️"
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
                    f"🧪 *[PAPER TRADING - TEKNIKAL EXIT]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Alasan    : {exit_reason}\n"
                    f"Strategi  : {strat_name}\n"
                    f"Harga In  : Rp {entry_p:,.0f}\n"
                    f"Harga Out : Rp {exit_price:,.0f}\n"
                    f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
                    f"📊 *REKAP TOTAL TEKNIKAL*:\n"
                    f"• Total Trade : {stats['total_trades']}x\n"
                    f"• Win / Loss  : {stats['wins']} Win / {stats['losses']} Loss\n"
                    f"• Win Rate    : {win_rate:.1f}%\n"
                    f"• Total P/L   : Rp {stats['total_pnl_idr']:+,.0f}\n"
                    f"• Sisa Kas    : Rp {self.state['cash_idr']:,.0f}"
                )
                return msg

            # PROTEKSI: Jika masih ada posisi ACTIVE & ada sinyal Beli baru, abaikan
            if signal_type in ["BELI", "BELI_PULLBACK"]:
                print(f"   🔒 [GUARD] Sinyal {signal_type} diabaikan! Posisi TEKNIKAL {TARGET_PAIR_NAME} masih ACTIVE.")

            return None

        # =========================================================
        # 2. JIKA TIDAK ADA POSISI AKTIF -> BISA ENTRY BELI
        # =========================================================
        if signal_type in ["BELI", "BELI_PULLBACK"]:
            available_cash = self.state["cash_idr"]
            if available_cash >= 100_000:  # Batas minimal alokasi
                amount = available_cash / current_price
                
                # Identifikasi nama strategi untuk dicatat
                strat_name = "Golden Cross" if signal_type == "BELI" else "Pullback Bounce"
                
                self.state["active_position"] = {
                    "entry_price_idr": current_price,
                    "amount": amount,
                    "sl": sl_price,
                    "tp": tp_price,
                    "type": signal_type,
                    "strategy": f"{strat_name} + Swing 4H",
                    "entry_time": current_time
                }
                self.state["cash_idr"] = 0.0  # Diputar ke posisi beli (Spot Murni)
                save_state(self.state)
                
                msg = (
                    f"🧪 *[PAPER TRADING - TEKNIKAL ENTRY]* {TARGET_PAIR_NAME}\n"
                    f"──────────────────────────────\n"
                    f"Strategi  : {strat_name} + Swing 4H Structure\n"
                    f"Modal In  : Rp {available_cash:,.0f} (All-in Spot)\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"Target TP : Rp {tp_price:,.0f} (Swing High 4H)\n"
                    f"Batas SL  : Rp {sl_price:,.0f} (Swing Low 4H)"
                )
                return msg
        return None

# --- MAIN EXECUTOR ---
async def main():
    print("DEBUG: Menjalankan Paper Trader Teknikal (ETH-IDR Spot)...")
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
    
    pt_tech = TechnicalPaperTrader()
    
    try:
        # Pull Data Multi-Timeframe: 1H (Trigger) & 4H (Structure)
        bars_1h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='1h', limit=50)
        bars_4h = exchange.fetch_ohlcv(TARGET_SYMBOL, timeframe='4h', limit=30)
        
        if len(bars_1h) < 40 or len(bars_4h) < 15:
            return
            
        # FIX BUG 3: Tipe data homogen float
        df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
        
        # 1. Indikator EMA & ATR di 1H
        df_1h['ema9']  = df_1h['close'].ewm(span=9, adjust=False).mean()
        df_1h['ema21'] = df_1h['close'].ewm(span=21, adjust=False).mean()
        
        tr0 = df_1h['high'] - df_1h['low']
        tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
        tr2 = (df_1h['low']  - df_1h['close'].shift(1)).abs()
        df_1h['tr']  = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        df_1h['atr'] = df_1h['tr'].rolling(window=14).mean()
        
        # FIX BUG 1: Window MA Volume diperbaiki dari 3 menjadi 20
        df_1h['avg_vol'] = df_1h['volume'].rolling(window=20).mean().shift(1)
        
        curr_idx = -2
        prev_idx = -3
        curr = df_1h.iloc[curr_idx]
        
        is_spike_vol = curr['volume'] > (df_1h['avg_vol'].iloc[curr_idx] * VOL_MULTIPLIER)
        
        harga_idr = curr['close'] * usd_idr
        high_idr  = curr['high'] * usd_idr
        low_idr   = curr['low'] * usd_idr
        atr_idr   = curr['atr'] * usd_idr
        
        # 2. Kalkulasi Swing High & Swing Low 4H
        swing = deteksi_swing_4h(df_4h, window=7)
        swing_high_idr = swing['swing_high'] * usd_idr
        swing_low_idr  = swing['swing_low'] * usd_idr
        
        sl_bullish = swing_low_idr - (0.5 * atr_idr)
        tp_bullish = swing_high_idr
        
        # Filter Pengaman / Emergency Fallback
        if tp_bullish <= harga_idr * 1.015:
            tp_bullish = harga_idr + (3.5 * atr_idr)
            
        if sl_bullish >= harga_idr * 0.985:
            sl_bullish = harga_idr - (1.8 * atr_idr)
        
        # 3. Condition Golden Cross & Death Cross & Pullback Bounce
        slope_ema9     = abs(df_1h['ema9'].iloc[curr_idx] - df_1h['ema9'].iloc[prev_idx]) / df_1h['ema9'].iloc[prev_idx] * 100
        is_sudut_tajam = slope_ema9 > 0.25 
        
        ema9_now   = df_1h['ema9'].iloc[curr_idx]
        ema9_prev  = df_1h['ema9'].iloc[prev_idx]
        ema21_now  = df_1h['ema21'].iloc[curr_idx]
        ema21_prev = df_1h['ema21'].iloc[prev_idx]
        
        golden = (ema9_prev < ema21_prev) and (ema9_now > ema21_now)
        death  = (ema9_prev > ema21_prev) and (ema9_now < ema21_now)
        
        # --- KONDISI TAMBAHAN: PULLBACK BOUNCE ---
        tren_bullish    = ema9_now > ema21_now
        sentuh_ema21    = df_1h['low'].iloc[curr_idx] <= (ema21_now * 1.002) # Toleransi 0.2%
        tutup_hijau     = df_1h['close'].iloc[curr_idx] > df_1h['open'].iloc[curr_idx]
        tutup_atas_ema9 = df_1h['close'].iloc[curr_idx] > ema9_now
        vol_oke         = df_1h['volume'].iloc[curr_idx] > df_1h['avg_vol'].iloc[curr_idx]

        pullback_bounce = tren_bullish and sentuh_ema21 and tutup_hijau and tutup_atas_ema9 and vol_oke
        # -----------------------------------------
        
        signal_type = None
        if golden and is_spike_vol and is_sudut_tajam:
            signal_type = "BELI"
        elif pullback_bounce:
            signal_type = "BELI_PULLBACK"
        elif death:
            signal_type = "JUAL"  # Sinyal exit darurat
            
        now_w_ib_str = now_wib.strftime('%Y-%m-%d %H:%M:%S')

        # EXPORT SIGNAL TEKNIKAL
        signal_payload = {
            "timestamp": now_w_ib_str,
            "symbol": TARGET_SYMBOL,
            "signal_type": signal_type,
            "current_price": harga_idr,
            "high_price": high_idr,
            "low_price": low_idr,
            "sl_price": sl_bullish,
            "tp_price": tp_bullish
        }
        save_signal(signal_payload)

        # 4. Evaluasi Paper Trading
        pt_msg = pt_tech.process(
            signal_type=signal_type,
            current_price=harga_idr,
            high_price=high_idr,
            low_price=low_idr,
            current_time=now_w_ib_str,
            sl_price=sl_bullish,
            tp_price=tp_bullish
        )
        
        if pt_msg:
            await bot.send_message(chat_id=CHAT_ID, text=pt_msg, parse_mode='Markdown')
            print("   🧪 Notif Simulasi Teknikal ETH-IDR Terkirim")
        else:
            print("   — Teknikal ETH-IDR: Tidak ada aksi (Posisi sedang aktif / Sinyal nihil)")

    except Exception as e:
        print(f"Error pada paper trader teknikal: {e}")

if __name__ == '__main__':
    asyncio.run(main())
