"""
========================================================
   KRIPTO BOT — Technical Pure Edition (Multi-Asset Paper Trading)
   Strategi: Golden Cross & Pullback Bounce + Swing 4H
   Market  : MURNI SPOT 100% (Multi-Asset Watchlist)
   Versi   : 4.2.1 (Multi-Asset Scanner & Persistent JSON Signal)
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
SIGNAL_FILE         = "signal_tech.json"
FEE_TAX_RATE        = 0.013  # Fee + Pajak PMK 68 (1.3% per siklus roundtrip)
MIN_SCORE_ENTRY     = 70   # Batas minimal skor kelayakan entry Teknikal

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
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def save_signal(signal_data):
    try:
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f, indent=4)
    except Exception as e:
        print(f"Gagal menyimpan signal_tech.json: {e}")

def save_signal_kosong(timestamp_str):
    default_signal = {
        "timestamp": timestamp_str,
        "symbol": "NONE",
        "signal_type": None,
        "current_price": 0.0,
        "high_price": 0.0,
        "low_price": 0.0,
        "sl_price": 0.0,
        "tp_price": 0.0,
        "rrr": 0.0
    }
    try:
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(default_signal, f, indent=4)
    except Exception as e:
        print(f"Gagal menyimpan signal_tech.json: {e}")

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

def hitung_skor_tech(c, avg_vol, slope_ema9, harga_idr, sl_price, tp_price, is_golden):
    score = 0
    breakdown = []

    if slope_ema9 >= 0.50:
        score += 25
        breakdown.append(f"• Slope EMA9 Sangat Tajam ({slope_ema9:.2f}%) (+25)")
    elif slope_ema9 >= 0.25:
        score += 15
        breakdown.append(f"• Slope EMA9 Tajam ({slope_ema9:.2f}%) (+15)")
    else:
        score += 10
        breakdown.append(f"• Slope EMA9 Moderat ({slope_ema9:.2f}%) (+10)")

    vol_ratio = (c['volume'] / avg_vol) if avg_vol > 0 else 0
    if vol_ratio >= 2.5:
        score += 25
        breakdown.append(f"• Volume Spike Sangat Tinggi ({vol_ratio:.1f}x) (+25)")
    elif vol_ratio >= 2.0:
        score += 20
        breakdown.append(f"• Volume Spike Tinggi ({vol_ratio:.1f}x) (+20)")
    elif vol_ratio >= 1.5:
        score += 15
        breakdown.append(f"• Volume Spike Valid ({vol_ratio:.1f}x) (+15)")

    candle_range = c['high'] - c['low']
    body_size = abs(c['close'] - c['open'])
    body_ratio = (body_size / candle_range) if candle_range > 0 else 0
    is_bullish = c['close'] > c['open']

    if is_bullish and body_ratio >= 0.60:
        score += 25
        breakdown.append(f"• Dominasi Candle Bullish Kuat ({body_ratio*100:.0f}%) (+25)")
    elif is_bullish:
        score += 15
        breakdown.append(f"• Dominasi Candle Bullish ({body_ratio*100:.0f}%) (+15)")

    if is_golden:
        breakdown.append("• Konfirmasi Golden Cross Valid")

    risk = harga_idr - sl_price
    reward = tp_price - harga_idr
    rrr = (reward / risk) if risk > 0 else 0

    if rrr >= 2.0:
        score += 25
        breakdown.append(f"• RRR Ideal ({rrr:.2f} >= 2.0) (+25)")
    elif rrr >= 1.5:
        score += 15
        breakdown.append(f"• RRR Cukup ({rrr:.2f} >= 1.5) (+15)")

    return min(score, 100), rrr, breakdown

class TechnicalPaperTrader:
    def __init__(self):
        self.state = load_state()

    def process(self, target_pair_name, signal_type, current_price, high_price, low_price, 
                current_time, sl_price, tp_price, score_info=None):
        pos = self.state.get("active_position")

        if pos:
            active_pair = pos.get("pair", "ETH-IDR")
            if active_pair != target_pair_name:
                return None

            entry_p = pos["entry_price_idr"]
            amount  = pos["amount"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            strat   = pos.get("strategy", "TECHNICAL")
            
            is_loss = low_price <= sl
            is_win  = high_price >= tp
            is_death = signal_type == "JUAL"
            
            if is_loss or is_win or is_death:
                exit_reason = "STOP LOSS 🛑" if is_loss else ("TAKE PROFIT 🎯" if is_win else "DEATH CROSS EXIT ⚠️")
                exit_price  = sl if is_loss else (tp if is_win else current_price)

                gross_val = exit_price * amount
                fee_tax   = gross_val * FEE_TAX_RATE
                net_val   = gross_val - fee_tax
                
                modal_val = entry_p * amount
                pnl_val   = net_val - modal_val
                pnl_pct   = (pnl_val / modal_val) * 100
                status    = "WIN" if pnl_val > 0 else "LOSS"
                
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
                if status == "WIN": self.state["stats"]["wins"] += 1
                else: self.state["stats"]["losses"] += 1
                self.state["stats"]["total_pnl_idr"] = round(float(self.state["stats"]["total_pnl_idr"] + pnl_val), 2)
                
                self.state["active_position"] = None
                save_state(self.state)
                
                stats = self.state["stats"]
                win_rate = (stats["wins"] / stats["total_trades"]) * 100 if stats["total_trades"] > 0 else 0
                
                return (
                    f"🧪 *[PAPER TRADING - TEKNIKAL EXIT]* {active_pair}\n"
                    f"──────────────────────────────\n"
                    f"Alasan    : {exit_reason}\n"
                    f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n"
                    f"Win Rate  : {win_rate:.1f}% | Total P/L: Rp {stats['total_pnl_idr']:+,.0f}"
                )
            return None

        if signal_type in ["BELI", "BELI_PULLBACK"] and not pos:
            cash = self.state["cash_idr"]
            if cash >= 100_000:
                amount = cash / current_price
                strat_name = "Golden Cross" if signal_type == "BELI" else "Pullback Bounce"
                
                self.state["active_position"] = {
                    "pair": target_pair_name,
                    "entry_price_idr": float(current_price),
                    "amount": float(amount),
                    "sl": float(sl_price),
                    "tp": float(tp_price),
                    "strategy": f"{strat_name} + Swing 4H",
                    "entry_time": current_time
                }
                self.state["cash_idr"] = 0.0
                save_state(self.state)
                
                score_str = ""
                if score_info:
                    sc, rrr, breakdown = score_info
                    score_str = f"Skor Setup: `{sc}/100`\n" + "\n".join(breakdown) + "\n"

                return (
                    f"🧪 *[PAPER TRADING - TEKNIKAL ENTRY]* {target_pair_name}\n"
                    f"──────────────────────────────\n"
                    f"{score_str}"
                    f"Modal In  : Rp {cash:,.0f}\n"
                    f"Harga In  : Rp {current_price:,.0f}\n"
                    f"TP Target : Rp {tp_price:,.0f}\n"
                    f"SL Batas  : Rp {sl_price:,.0f}"
                )
        return None

async def main():
    print("DEBUG: Menjalankan Paper Trader Teknikal Multi-Asset Scanner...")
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
    
    pt_tech = TechnicalPaperTrader()
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
            
            df_1h['ema9'] = df_1h['close'].ewm(span=9, adjust=False).mean()
            df_1h['ema21'] = df_1h['close'].ewm(span=21, adjust=False).mean()
            
            tr0 = df_1h['high'] - df_1h['low']
            tr1 = (df_1h['high'] - df_1h['close'].shift(1)).abs()
            tr2 = (df_1h['low'] - df_1h['close'].shift(1)).abs()
            df_1h['tr'] = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
            df_1h['atr'] = df_1h['tr'].rolling(14).mean()
            df_1h['avg_vol'] = df_1h['volume'].rolling(20).mean().shift(1)
            
            curr = df_1h.iloc[-2]
            latest = df_1h.iloc[-1]
            avg_vol_now = float(df_1h['avg_vol'].iloc[-2])
            
            harga_idr = float(curr['close'] * usd_idr)
            high_idr  = float(max(curr['high'], latest['high']) * usd_idr)
            low_idr   = float(min(curr['low'], latest['low']) * usd_idr)
            atr_idr   = float(curr['atr'] * usd_idr)
            
            swing = deteksi_swing_4h(df_4h, window=7)
            sl_price = float(swing['swing_low'] * usd_idr - (0.5 * atr_idr))
            tp_price = float(swing['swing_high'] * usd_idr)
            
            if tp_price <= harga_idr * 1.015: tp_price = float(harga_idr + (3.5 * atr_idr))
            if sl_price >= harga_idr * 0.985: sl_price = float(harga_idr - (1.8 * atr_idr))

            slope_ema9 = float(abs(df_1h['ema9'].iloc[-2] - df_1h['ema9'].iloc[-3]) / df_1h['ema9'].iloc[-3] * 100)
            is_sudut_tajam = slope_ema9 > 0.25
            is_spike_vol = curr['volume'] > (avg_vol_now * VOL_MULTIPLIER)
            
            ema9_n, ema9_p = float(df_1h['ema9'].iloc[-2]), float(df_1h['ema9'].iloc[-3])
            ema21_n, ema21_p = float(df_1h['ema21'].iloc[-2]), float(df_1h['ema21'].iloc[-3])
            
            golden = bool((ema9_p < ema21_p) and (ema9_n > ema21_n))
            death = bool((ema9_p > ema21_p) and (ema9_n < ema21_n))
            
            tren_b = ema9_n > ema21_n
            sentuh_e21 = df_1h['low'].iloc[-2] <= (ema21_n * 1.002)
            tutup_h = df_1h['close'].iloc[-2] > df_1h['open'].iloc[-2]
            tutup_atas_e9 = df_1h['close'].iloc[-2] > ema9_n
            vol_oke = df_1h['volume'].iloc[-2] > avg_vol_now
            
            pullback_bounce = bool(tren_b and sentuh_e21 and tutup_h and tutup_atas_e9 and vol_oke)
            
            raw_beli = bool(golden and is_spike_vol and is_sudut_tajam)
            raw_pullback = pullback_bounce
            
            signal_type = None
            if raw_beli or raw_pullback:
                target_sig = "BELI" if raw_beli else "BELI_PULLBACK"
                score, rrr, breakdown = hitung_skor_tech(curr, avg_vol_now, slope_ema9, harga_idr, sl_price, tp_price, is_golden=raw_beli)
                if score >= MIN_SCORE_ENTRY:
                    candidates.append({
                        "pair": pair_name,
                        "symbol": symbol,
                        "signal": target_sig,
                        "score": score,
                        "price": harga_idr,
                        "high": high_idr,
                        "low": low_idr,
                        "sl": sl_price,
                        "tp": tp_price,
                        "score_info": (score, rrr, breakdown)
                    })
            elif death:
                candidates.append({
                    "pair": pair_name,
                    "symbol": symbol,
                    "signal": "JUAL",
                    "score": 0,
                    "price": harga_idr,
                    "high": high_idr,
                    "low": low_idr,
                    "sl": sl_price,
                    "tp": tp_price,
                    "score_info": None
                })
        except Exception as ex:
            print(f"Error scan {symbol}: {ex}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    active_pos = pt_tech.state.get("active_position")
    notif_sent = False

    if active_pos:
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
                
                msg = pt_tech.process(active_pair, "CHECK_EXIT", c_idr, h_idr, l_idr, now_str, 0, 0)
                if msg:
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    notif_sent = True
                break
        # Jika sedang ada posisi aktif, simpan format kosong ber-struktur ke file signal
        save_signal_kosong(now_str)
    elif candidates:
        top = candidates[0]
        signal_payload = {
            "timestamp": now_str,
            "symbol": top["symbol"],
            "signal_type": top["signal"],
            "current_price": top["price"],
            "high_price": top["high"],
            "low_price": top["low"],
            "sl_price": top["sl"],
            "tp_price": top["tp"],
            "rrr": top["score_info"][1] if top["score_info"] else 0.0
        }
        save_signal(signal_payload)
        
        msg = pt_tech.process(top["pair"], top["signal"], top["price"], top["high"], top["low"], now_str, top["sl"], top["tp"], top.get("score_info"))
        if msg:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
            print(f"    🧪 Notif Entry Teknikal Terkirim untuk Juara #1 ({top['pair']})")
            notif_sent = True
    else:
        # Jika tidak ada posisi aktif dan tidak ada kandidat, simpan format kosong ber-struktur
        save_signal_kosong(now_str)

    if not notif_sent:
        print("    — Teknikal Scanner: Tidak ada posisi aktif atau sinyal valid yang memenuhi skor.")

if __name__ == '__main__':
    asyncio.run(main())
