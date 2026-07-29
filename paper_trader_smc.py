"""
========================================================
   KRIPTO BOT — SMC Swing Edition (Multi-Asset Paper Trading)
   Strategi: Smart Money Concepts (CHoCH, BOS, Order Block, Swing 4H)
   Market  : MURNI SPOT 100% (Multi-Asset Watchlist)
   Versi   : 4.2.1 (Layout Presisi Sejajar Bot AGR)
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
SIGNAL_FILE         = "signal_smc.json"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68 (1.3% per siklus roundtrip)
MIN_SCORE_ENTRY     = 70   # Batas minimal skor kelayakan entry SMC

# Watchlist Multi-Asset Spot
WATCHLIST = [
    {"symbol": "BTC/USDT", "pair": "BTC-IDR"},
    {"symbol": "ETH/USDT", "pair": "ETH-IDR"},
    {"symbol": "SOL/USDT", "pair": "SOL-IDR"},
    {"symbol": "ADA/USDT", "pair": "ADA-IDR"},
    {"symbol": "XRP/USDT", "pair": "XRP-IDR"}
]

# --- MANAJEMEN STATE ---
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
                if "cash_idr" not in state: state["cash_idr"] = INITIAL_CAPITAL_IDR
                if "active_position" not in state: state["active_position"] = None
                if "history" not in state: state["history"] = []
                if "stats" not in state: state["stats"] = {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_idr": 0.0}
                return state
        except Exception:
            pass
    return default_state

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Gagal menyimpan state {STATE_FILE}: {e}")

def save_signal(signal_data):
    try:
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f, indent=4)
    except Exception as e:
        print(f"Gagal menyimpan signal_smc.json: {e}")

def get_usd_idr():
    try:
        response = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
        return float(response.json()['ticker']['last'])
    except Exception:
        return 18000.0

def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> dict:
    swing_low = float(df_4h['low'].iloc[-window-1:-1].min())
    swing_high = float(df_4h['high'].iloc[-window-1:-1].max())
    return {'swing_high': swing_high, 'swing_low': swing_low}

def hitung_skor_smc(choch, bos, mitigation, rrr, volume_spike):
    score = 0
    breakdown = []
    
    if choch:
        score += 35
        breakdown.append("• Konfirmasi CHoCH Valid (+35)")
    else:
        breakdown.append("• Tanpa CHoCH (+0)")
        
    if bos:
        score += 25
        breakdown.append("• Struktur BOS Terbentuk (+25)")
    else:
        breakdown.append("• Tanpa BOS (+0)")
        
    if mitigation:
        score += 20
        breakdown.append("• Area Mitigasi / Order Block Tersentuh (+20)")
    else:
        breakdown.append("• Belum Menyentuh OB (+0)")
        
    if volume_spike:
        score += 10
        breakdown.append("• Lonjakan Volume Konfirmasi (+10)")
    else:
        breakdown.append("• Volume Standar (+0)")
        
    if rrr >= 2.0:
        score += 10
        breakdown.append(f"• RRR Ideal ({rrr:.2f} >= 2.0) (+10)")
    else:
        breakdown.append(f"• RRR Cukup ({rrr:.2f} < 2.0) (+0)")
        
    return min(score, 100), breakdown

class SmcPaperTrader:
    def __init__(self):
        self.state = load_state()

    def process(self, target_pair_name, signal_type, current_price, high_price, low_price, 
                current_time, sl_price, tp_price, score_info=None):
        pos = self.state.get("active_position")

        if pos:
            active_pair = pos.get("pair", "ETH-IDR")
            if active_pair != target_pair_name:
                return None  # Skip jika koin beda dari yang di-hold

            entry_p = pos["entry_price_idr"]
            amount  = pos["amount"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            strat   = pos.get("strategy", "SMC")
            
            is_loss = low_price <= sl
            is_win  = high_price >= tp
            
            if is_loss or is_win:
                exit_reason = "STOP LOSS 🛑" if is_loss else "TAKE PROFIT 🎯"
                exit_price  = sl if is_loss else tp

                gross_val = exit_price * amount
                fee_tax   = gross_val * FEE_TAX_RATE
                net_val   = gross_val - fee_tax
                
                modal_val = entry_p * amount
                pnl_val   = net_val - modal_val
                pnl_pct   = (pnl_val / modal_val) * 100
                status    = "WIN" if pnl_val > 0 else "LOSS"
                
                # Update State
                self.state["cash_idr"] += float(net_val)
                self.state["history"].append({
                    "pair": active_pair,
                    "strategy": strat,
                    "entry_price": float(entry_p),
                    "exit_price": float(exit_price),
                    "pnl_pct": round(float(pnl_pct), 2),
                    "pnl_idr": round(float(pnl_val), 2),
                    "status": status,
                    "entry_time": pos["entry_time"],
                    "exit_time": current_time
                })
                
                self.state["stats"]["total_trades"] += 1
                if status == "WIN": 
                    self.state["stats"]["wins"] += 1
                else: 
                    self.state["stats"]["losses"] += 1
                
                self.state["stats"]["total_pnl_idr"] = round(float(self.state["stats"]["total_pnl_idr"] + pnl_val), 2)
                
                # Reset posisi aktif & simpan state
                self.state["active_position"] = None
                save_state(self.state)
                
                # Ambil statistik untuk notifikasi
                stats = self.state["stats"]
                total_trades = stats["total_trades"]
                wins = stats["wins"]
                losses = stats["losses"]
                total_pnl = stats["total_pnl_idr"]
                win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
                sisa_kas = self.state["cash_idr"]
                
                # TAMPILAN NOTIFIKASI SEJAJAR DENGAN AGR
                return (
                    f"🧪 *[PAPER TRADING - SMC EXIT]* {active_pair}\n"
                    f"──────────────────────────────\n"
                    f"Alasan    : {exit_reason}\n"
                    f"Harga In  : Rp {entry_p:,.0f}\n"
                    f"Harga Out : Rp {exit_price:,.0f}\n"
                    f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
                    f"📊 *REKAP TOTAL SMC:*\n"
                    f"• Total Trade : {total_trades}x\n"
                    f"• Win / Loss  : {wins} Win / {losses} Loss (WR: {win_rate:.1f}%)\n"
                    f"• Total P/L   : Rp {total_pnl:+,.0f}\n"
                    f"• Sisa Kas    : Rp {sisa_kas:,.0f}"
                )
            return None

        if signal_type == "BELI" and not pos:
            cash = self.state["cash_idr"]
            if cash >= 100_000:
                amount = cash / current_price
                self.state["active_position"] = {
                    "pair": target_pair_name,
                    "entry_price_idr": float(current_price),
                    "amount": float(amount),
                    "sl": float(sl_price),
                    "tp": float(tp_price),
                    "strategy": "SMC Order Block + Swing 4H",
                    "entry_time": current_time
                }
                self.state["cash_idr"] = 0.0
                save_state(self.state)
                
                score_str = ""
                if score_info:
                    sc, breakdown = score_info
                    score_str = f"Skor Setup: `{sc}/100`\n" + "\n".join(breakdown) + "\n"

                return (
                    f"🧪 *[PAPER TRADING - SMC ENTRY]* {target_pair_name}\n"
                    f"──────────────────────────────\n"
                    f"{score_str}"
                    f"Modal In  : Rp {cash:,.0f}\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"TP Target : Rp {tp_price:,.0f}\n"
                    f"SL Batas  : Rp {sl_price:,.0f}"
                )
        return None

async def main():
    print("DEBUG: Menjalankan Paper Trader SMC Multi-Asset Scanner...")
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market: {e}")
        return

    bot = Bot(token=TOKEN)
    usd_idr = get_usd_idr()
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    now_str = now_wib.strftime('%Y-%m-%d %H:%M:%S')
    
    pt_smc = SmcPaperTrader()
    candidates = []

    for item in WATCHLIST:
        symbol = item["symbol"]
        pair_name = item["pair"]
        try:
            bars_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
            bars_4h = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=30)
            if len(bars_1h) < 40 or len(bars_4h) < 15:
                continue

            df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
            df_4h = pd.DataFrame(bars_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
            
            df_1h['atr'] = (df_1h['high'] - df_1h['low']).rolling(14).mean()
            df_1h['avg_vol'] = df_1h['volume'].rolling(20).mean().shift(1)
            
            curr = df_1h.iloc[-2]
            latest = df_1h.iloc[-1]
            
            harga_idr = float(curr['close'] * usd_idr)
            high_idr  = float(max(curr['high'], latest['high']) * usd_idr)
            low_idr   = float(min(curr['low'], latest['low']) * usd_idr)
            atr_idr   = float(curr['atr'] * usd_idr)
            
            swing = deteksi_swing_4h(df_4h, window=7)
            sl_price = float(swing['swing_low'] * usd_idr - (0.5 * atr_idr))
            tp_price = float(swing['swing_high'] * usd_idr)
            
            if tp_price <= harga_idr * 1.015: tp_price = float(harga_idr + (3.5 * atr_idr))
            if sl_price >= harga_idr * 0.985: sl_price = float(harga_idr - (1.8 * atr_idr))

            risk = harga_idr - sl_price
            reward = tp_price - harga_idr
            rrr = (reward / risk) if risk > 0 else 0

            # Deteksi Sederhana SMC
            choch = bool(curr['close'] > curr['open'] and curr['volume'] > (df_1h['avg_vol'].iloc[-2] * 1.5))
            bos = bool(curr['close'] > df_1h['high'].iloc[-5:-2].max())
            mitigation = bool(low_idr <= (swing['swing_low'] * usd_idr * 1.01))
            vol_spike = bool(curr['volume'] > (df_1h['avg_vol'].iloc[-2] * 1.8))

            score, breakdown = hitung_skor_smc(choch, bos, mitigation, rrr, vol_spike)
            
            if score >= MIN_SCORE_ENTRY:
                candidates.append({
                    "pair": pair_name,
                    "symbol": symbol,
                    "signal": "BELI",
                    "score": score,
                    "price": harga_idr,
                    "high": high_idr,
                    "low": low_idr,
                    "sl": sl_price,
                    "tp": tp_price,
                    "breakdown": breakdown
                })
        except Exception as ex:
            print(f"Error scan {symbol}: {ex}")

    # Urutkan berdasarkan skor tertinggi
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # Proses posisi aktif atau ambil kandidat terbaik juara #1
    active_pos = pt_smc.state.get("active_position")
    notif_sent = False

    if active_pos:
        # Cek exit untuk koin yang sedang di-hold
        active_pair = active_pos["pair"]
        for item in WATCHLIST:
            if item["pair"] == active_pair:
                bars_1h = exchange.fetch_ohlcv(item["symbol"], timeframe='1h', limit=10)
                df_1h = pd.DataFrame(bars_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).astype(float)
                curr = df_1h.iloc[-2]
                latest = df_1h.iloc[-1]
                h_idr = float(max(curr['high'], latest['high']) * usd_idr)
                l_idr = float(min(curr['low'], latest['low']) * usd_idr)
                c_idr = float(curr['close'] * usd_idr)
                
                msg = pt_smc.process(active_pair, "CHECK_EXIT", c_idr, h_idr, l_idr, now_str, 0, 0)
                if msg:
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    notif_sent = True
                break
    elif candidates:
        top = candidates[0]
        save_signal({"timestamp": now_str, **top})
        msg = pt_smc.process(top["pair"], top["signal"], top["price"], top["high"], top["low"], now_str, top["sl"], top["tp"], (top["score"], top["breakdown"]))
        if msg:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
            print(f"   🧪 Notif Entry SMC Terkirim untuk Juara #1 ({top['pair']})")
            notif_sent = True

    if not notif_sent:
        print("   — SMC Scanner: Tidak ada posisi aktif atau sinyal valid yang memenuhi skor.")

if __name__ == '__main__':
    asyncio.run(main())
