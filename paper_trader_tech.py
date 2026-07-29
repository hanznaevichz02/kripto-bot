"""
========================================================
Bot TECH (v4.2.1 - Enhanced)
Paper Trader Teknikal Multi-Asset dengan Logika Move-to-Break-Even (BE)

Fitur Utama:
1. Multi-Asset Scanning (KuCoin Spot via CCXT)
2. Indikator: EMA 9/21, ATR 14, Volume Spike, Slope EMA, Swing High/Low 4H
3. Paper Trading Simulator dengan Modal IDR, Fee & Pajak PMK 68 (1.3% total cost factor)
4. Dynamic Risk Management:
   - Stop Loss (SL) berbasis Swing Low 4H + 0.5 ATR
   - Take Profit (TP) berbasis Swing High 4H / Multiplier ATR
   - Move to Break-Even (BE): Saat profit mencapai 1x Risk (1R), SL otomatis dinaikkan ke Entry + Buffer Fee
   - Murni mengandalkan TP & SL (Tanpa Death Cross Exit prematur)
========================================================
"""

import os
import json
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import ccxt
from telegram import Bot

# ==========================================
# KONFIGURASI GLOBAL
# ==========================================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")

WATCHLIST = [
    {"symbol": "BTC/USDT", "pair": "BTC-IDR"},
    {"symbol": "ETH/USDT", "pair": "ETH-IDR"},
    {"symbol": "SOL/USDT", "pair": "SOL-IDR"},
    {"symbol": "XRP/USDT", "pair": "XRP-IDR"},
    {"symbol": "BNB/USDT", "pair": "BNB-IDR"},
]

VOL_MULTIPLIER = 1.5
MIN_SCORE_ENTRY = 70.0
INITIAL_BALANCE_IDR = 10_000_000.0  # Modal awal Rp 10 Juta
FEE_BUY_PCT = 0.001                 # Fee beli 0.1%
FEE_SELL_PCT = 0.001                # Fee jual 0.1%
PAJAK_PMK68_PCT = 0.011             # Pajak PMK 68 (0.1% PPN + 1.0% PPh)
TOTAL_FEE_COST = FEE_BUY_PCT + FEE_SELL_PCT + PAJAK_PMK68_PCT # ~1.3%

STATE_FILE = "paper_trader_state.json"
SIGNAL_FILE = "signal_tech.json"

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_usd_idr():
    """Mendapatkan kurs USD ke IDR"""
    return 16200.0

def save_signal(payload):
    """Menyimpan data sinyal aktif ke JSON"""
    try:
        with open(SIGNAL_FILE, "w") as f:
            json.dump(payload, f, indent=4)
    except Exception as e:
        print(f"Error save_signal: {e}")

def save_signal_kosong(now_str):
    """Reset data sinyal aktif ke JSON"""
    payload = {
        "timestamp": now_str,
        "symbol": None,
        "signal_type": "NEUTRAL",
        "current_price": 0,
        "high_price": 0,
        "low_price": 0,
        "sl_price": 0,
        "tp_price": 0,
        "rrr": 0.0
    }
    save_signal(payload)

def deteksi_swing_4h(df_4h, window=7):
    """Mendeteksi Swing High & Swing Low dari DataFrame 4H"""
    recent_4h = df_4h.tail(window)
    swing_high = recent_4h['high'].max()
    swing_low = recent_4h['low'].min()
    return {"swing_high": swing_high, "swing_low": swing_low}

def hitung_skor_tech(curr, avg_vol_now, slope_ema9, harga_idr, sl_price, tp_price, is_golden=True):
    """Menghitung skor kualitas setup entry (0 - 100)"""
    score = 50.0
    
    # 1. Volume Score
    vol_ratio = curr['volume'] / avg_vol_now if avg_vol_now > 0 else 1.0
    if vol_ratio >= 2.0:
        score += 20.0
    elif vol_ratio >= 1.5:
        score += 15.0
    elif vol_ratio >= 1.0:
        score += 10.0
        
    # 2. Slope / Momentum Score
    if slope_ema9 >= 0.5:
        score += 15.0
    elif slope_ema9 >= 0.25:
        score += 10.0
        
    # 3. Risk-to-Reward Ratio (RRR)
    risk = max(1.0, harga_idr - sl_price)
    reward = max(1.0, tp_price - harga_idr)
    rrr = reward / risk
    
    if rrr >= 2.0:
        score += 15.0
    elif rrr >= 1.5:
        score += 10.0
    elif rrr < 1.0:
        score -= 20.0
        
    breakdown = f"VolRatio: {vol_ratio:.2f}x, Slope: {slope_ema9:.2f}%, RRR: 1:{rrr:.2f}"
    return min(100.0, max(0.0, score)), rrr, breakdown

# ==========================================
# CLASS TECHNICAL PAPER TRADER
# ==========================================
class TechnicalPaperTrader:
    def __init__(self, state_file=STATE_FILE):
        self.state_file = state_file
        self.state = self.load_state()

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Gagal memuat state, membuat state baru: {e}")
        return {
            "balance_idr": INITIAL_BALANCE_IDR,
            "active_position": None,
            "trade_history": []
        }

    def save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            print(f"Gagal menyimpan state: {e}")

    def process(self, pair, signal_type, current_price, high_price, low_price, timestamp, sl_price=0, tp_price=0, score_info=None):
        """
        Memproses sinyal Entry, Monitoring SL/TP/BE, dan Exit
        """
        active = self.state.get("active_position")

        # -------------------------------------------------------------
        # A. PENGECEKAN POSISI AKTIF (CHECK EXIT & MOVE TO BREAK-EVEN)
        # -------------------------------------------------------------
        if active and active["pair"] == pair:
            entry_price = active["entry_price"]
            initial_sl = active["initial_sl"]
            current_sl = active["sl_price"]
            tp_level = active["tp_price"]
            amount_coin = active["amount_coin"]
            is_be_moved = active.get("is_be_moved", False)
            
            # Hitung Jarak Risk Awal (1R)
            risk_1r = entry_price - initial_sl
            target_be_trigger = entry_price + risk_1r
            
            # 1. LOGIKA MOVE TO BREAK-EVEN (BE)
            # Trigger jika High mencapai/melewati (Entry + 1R) dan belum dinaikkan ke BE
            if not is_be_moved and high_price >= target_be_trigger:
                # Set SL baru di atas harga Entry sebesar buffer fee (+0.3%)
                new_sl = entry_price * 1.003
                active["sl_price"] = new_sl
                active["is_be_moved"] = True
                self.save_state()
                
                profit_1r_pct = ((target_be_trigger - entry_price) / entry_price) * 100
                msg = (
                    f"🛡️ *MOVE TO BREAK-EVEN (BE) ACTIVATED*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Aset: `{pair}`\n"
                    f"Harga Entry: `Rp {entry_price:,.0f}`\n"
                    f"Harga Tertinggi Tercapai: `Rp {high_price:,.0f}` (+1R / +{profit_1r_pct:.2f}%)\n"
                    f"📍 *Stop Loss Baru (BE)*: `Rp {new_sl:,.0f}` (Bebas risiko rugi!)\n"
                    f"🎯 Target TP Tetap: `Rp {tp_level:,.0f}`\n"
                    f"⏰ Waktu: `{timestamp}`"
                )
                return msg

            # 2. CEK TAKE PROFIT (TP)
            if high_price >= tp_level:
                exit_price = tp_level
                gross_value = amount_coin * exit_price
                sell_fee_tax = gross_value * (FEE_SELL_PCT + PAJAK_PMK68_PCT)
                net_received = gross_value - sell_fee_tax
                
                capital_used = active["capital_used"]
                pnl_idr = net_received - capital_used
                pnl_pct = (pnl_idr / capital_used) * 100
                
                self.state["balance_idr"] += net_received
                
                history_entry = {
                    "pair": pair,
                    "type": "WIN (TP)",
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "capital": capital_used,
                    "net_received": net_received,
                    "pnl_idr": pnl_idr,
                    "pnl_pct": pnl_pct,
                    "entry_time": active["entry_time"],
                    "exit_time": timestamp
                }
                self.state["trade_history"].append(history_entry)
                self.state["active_position"] = None
                self.save_state()

                msg = (
                    f"🎉 *TAKE PROFIT (TP) HIT! (WIN)*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Aset: `{pair}`\n"
                    f"Harga Entry: `Rp {entry_price:,.0f}`\n"
                    f"Harga Exit (TP): `Rp {exit_price:,.0f}`\n"
                    f"Profit Bersih: *+Rp {pnl_idr:,.0f}* (+{pnl_pct:.2f}%)\n"
                    f"Saldo Baru: `Rp {self.state['balance_idr']:,.0f}`\n"
                    f"⏰ Waktu: `{timestamp}`"
                )
                return msg

            # 3. CEK STOP LOSS (SL) ATAU BREAK-EVEN EXIT
            if low_price <= current_sl:
                exit_price = current_sl
                gross_value = amount_coin * exit_price
                sell_fee_tax = gross_value * (FEE_SELL_PCT + PAJAK_PMK68_PCT)
                net_received = gross_value - sell_fee_tax
                
                capital_used = active["capital_used"]
                pnl_idr = net_received - capital_used
                pnl_pct = (pnl_idr / capital_used) * 100
                
                self.state["balance_idr"] += net_received
                
                exit_type = "BREAK-EVEN (BE)" if is_be_moved else "STOP LOSS (SL)"
                history_entry = {
                    "pair": pair,
                    "type": exit_type,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "capital": capital_used,
                    "net_received": net_received,
                    "pnl_idr": pnl_idr,
                    "pnl_pct": pnl_pct,
                    "entry_time": active["entry_time"],
                    "exit_time": timestamp
                }
                self.state["trade_history"].append(history_entry)
                self.state["active_position"] = None
                self.save_state()

                status_emoji = "🛡️" if is_be_moved else "🔻"
                msg = (
                    f"{status_emoji} *EXIT POSITION: {exit_type}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Aset: `{pair}`\n"
                    f"Harga Entry: `Rp {entry_price:,.0f}`\n"
                    f"Harga Exit: `Rp {exit_price:,.0f}`\n"
                    f"Hasil PnL: *Rp {pnl_idr:,.0f}* ({pnl_pct:+.2f}%)\n"
                    f"Saldo Baru: `Rp {self.state['balance_idr']:,.0f}`\n"
                    f"⏰ Waktu: `{timestamp}`"
                )
                return msg

            return None

        # -------------------------------------------------------------
        # B. EKSEKUSI ENTRY BARU (BELI / BELI_PULLBACK)
        # -------------------------------------------------------------
        if not active and signal_type in ["BELI", "BELI_PULLBACK"]:
            available_balance = self.state["balance_idr"]
            if available_balance < 100_000:
                print("Saldo tidak mencukupi untuk melakukan paper trading entry.")
                return None

            capital_to_use = available_balance  # Alokasi 100% modal
            buy_fee = capital_to_use * FEE_BUY_PCT
            net_capital = capital_to_use - buy_fee
            
            amount_coin = net_capital / current_price
            
            self.state["balance_idr"] -= capital_to_use
            self.state["active_position"] = {
                "pair": pair,
                "entry_price": current_price,
                "initial_sl": sl_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "amount_coin": amount_coin,
                "capital_used": capital_to_use,
                "is_be_moved": False,
                "entry_time": timestamp
            }
            self.save_state()

            score_str = f"{score_info[0]:.1f}" if score_info else "N/A"
            rrr_str = f"1:{score_info[1]:.2f}" if score_info else "N/A"
            breakdown_str = score_info[2] if score_info else ""

            msg = (
                f"🚀 *PAPER TRADING ENTRY ({signal_type})*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Aset: `{pair}`\n"
                f"Skor Setup: *{score_str}/100* (RRR {rrr_str})\n"
                f"Detail Skor: _{breakdown_str}_\n\n"
                f"Harga Beli: `Rp {current_price:,.0f}`\n"
                f"Modal Digunakan: `Rp {capital_to_use:,.0f}`\n"
                f"Jumlah Koin: `{amount_coin:.6f}`\n"
                f"📍 *Initial SL*: `Rp {sl_price:,.0f}`\n"
                f"🎯 *Target TP*: `Rp {tp_price:,.0f}`\n"
                f"🛡️ *Logic*: Move-to-BE aktif saat +1R\n"
                f"⏰ Waktu: `{timestamp}`"
            )
            return msg

        return None


# ==========================================
# MAIN ASYNC SCANNER LOOP
# ==========================================
async def main():
    print("DEBUG: Menjalankan Paper Trader Teknikal Multi-Asset Scanner (With Dynamic BE)...")
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30000})
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Gagal memuat market KuCoin: {e}")
        return

    bot = Bot(token=TOKEN)
    usd_idr = get_usd_idr()
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    now_str = now_wib.strftime('%Y-%m-%d %H:%M:%S')
    
    pt_tech = TechnicalPaperTrader()
    active_pos = pt_tech.state.get("active_position")
    candidates = []
    notif_sent = False

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
            
            # Indikator Teknikal
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
            
            tren_b = ema9_n > ema21_n
            sentuh_e21 = df_1h['low'].iloc[-2] <= (ema21_n * 1.002)
            tutup_h = df_1h['close'].iloc[-2] > df_1h['open'].iloc[-2]
            tutup_atas_e9 = df_1h['close'].iloc[-2] > ema9_n
            vol_oke = df_1h['volume'].iloc[-2] > avg_vol_now
            
            pullback_bounce = bool(tren_b and sentuh_e21 and tutup_h and tutup_atas_e9 and vol_oke)
            
            # -------------------------------------------------------------
            # 1. JIKA ADA POSISI AKTIF, EVALUASI EXIT / MOVE-TO-BE
            # -------------------------------------------------------------
            if active_pos and active_pos["pair"] == pair_name:
                # Cek Move-to-BE, SL, atau TP
                msg = pt_tech.process(pair_name, "CHECK_EXIT", harga_idr, high_idr, low_idr, now_str, 0, 0)
                if msg:
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    notif_sent = True
                save_signal_kosong(now_str)
                continue  # Lanjut loop tanpa pendaftaran kandidat entry

            # -------------------------------------------------------------
            # 2. JIKA TIDAK ADA POSISI AKTIF, SCAN KANDIDAT ENTRY
            # -------------------------------------------------------------
            if not active_pos:
                raw_beli = bool(golden and is_spike_vol and is_sudut_tajam)
                raw_pullback = pullback_bounce
                
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

        except Exception as ex:
            print(f"Error scanning {symbol}: {ex}")

    # -------------------------------------------------------------
    # EKSEKUSI ENTRY TERBAIK DARI HASIL SCANNING
    # -------------------------------------------------------------
    if not active_pos and candidates:
        candidates.sort(key=lambda x: x["score"], reverse=True)
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
            print(f"    🧪 Entry Sent for Candidate #1 ({top['pair']})")
            notif_sent = True
    elif not active_pos:
        save_signal_kosong(now_str)

    if not notif_sent:
        print("    — Scanner Selesai: Posisi aman / Tidak ada sinyal entry baru.")

if __name__ == "__main__":
    asyncio.run(main())
