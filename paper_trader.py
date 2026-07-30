"""
========================================================
    KRIPTO BOT — Hybrid Aggressive Edition (Paper Trading) v4.4
    Fungsi  : Integrasi Skor SMC Kuantitatif, ATR Buffer, Volume Spike, FVG, MA Inflection/Squeeze, & Paper Trading
========================================================
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
import ccxt
import pandas as pd
import requests
from telegram import Bot

# --- KONFIGURASI LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# --- KONFIGURASI ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

VOL_MULTIPLIER_TECH = 2.0  # Acuan Bot TECH
VOL_MULTIPLIER_SMC = 1.5  # Acuan Bot SMC
MIN_SCORE_ENTRY = 40  # Batas minimal skor kelayakan (0 - 100)

INITIAL_CAPITAL_IDR = 1_000_000.0
STATE_FILE = "paper_trading_hybrid.json"
SIGNAL_FILE = "signal_agr.json"
FEE_TAX_RATE = 0.013  # Fee + Pajak PMK 68 dipotong saat Sell/Exit (1.3%)

ASSET_LIST = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "SUI/USDT",
    "XRP/USDT",
    "LINK/USDT",
    "AAVE/USDT",
    "DOT/USDT",
    "ONDO/USDT",
    "ARB/USDT",
    "NEAR/USDT",
    "ZEC/USDT",
    "TAO/USDT",
    "AVAX/USDT",
    "ADA/USDT",
]

# Mapping Simbol Spot ke Kucoin Futures untuk Fetch Funding Rate (16 Koin)
FUTURES_MAP = {
    "BTC/USDT": "XBTUSDTM",
    "ETH/USDT": "ETHUSDTM",
    "SOL/USDT": "SOLUSDTM",
    "BNB/USDT": "BNBUSDTM",
    "SUI/USDT": "SUIUSDTM",
    "XRP/USDT": "XRPUSDTM",
    "LINK/USDT": "LINKUSDTM",
    "AAVE/USDT": "AAVEUSDTM",
    "DOT/USDT": "DOTUSDTM",
    "ONDO/USDT": "ONDOUSDTM",
    "ARB/USDT": "ARBUSDTM",
    "NEAR/USDT": "NEARUSDTM",
    "ZEC/USDT": "ZECUSDTM",
    "TAO/USDT": "TAOUSDTM",
    "AVAX/USDT": "AVAXUSDTM",
    "ADA/USDT": "ADAUSDTM",
}


# --- MANAJEMEN STATE & SIGNAL ---
def load_state():
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r") as f:
        state = json.load(f)
        if "cash_idr" not in state:
          state["cash_idr"] = INITIAL_CAPITAL_IDR
        if "active_position" not in state:
          state["active_position"] = None
        if "history" not in state:
          state["history"] = []
        if "stats" not in state:
          state["stats"] = {
              "total_trades": 0,
              "wins": 0,
              "losses": 0,
              "total_pnl_idr": 0.0,
          }
        return state
    except Exception:
      pass
  return {
      "cash_idr": INITIAL_CAPITAL_IDR,
      "active_position": None,
      "history": [],
      "stats": {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_idr": 0.0},
  }


def save_state(state):
  with open(STATE_FILE, "w") as f:
    json.dump(state, f, indent=4)


def save_signal(signal_data):
  with open(SIGNAL_FILE, "w") as f:
    json.dump(signal_data, f, indent=4)


# --- FUNGSI HELPER ---
def get_usd_idr() -> float:
  try:
    r = requests.get("https://indodax.com/api/ticker/usdtidr", timeout=5)
    logging.info(
        'HTTP Request: GET https://indodax.com/api/ticker/usdtidr "HTTP/1.1 200'
        ' OK"'
    )
    raw_idr = float(r.json()["ticker"]["last"])

    # Kalibrasi spread Pluang agar hampir mendekati harga Pluang
    PLUANG_MARGIN = 1.0052

    return raw_idr * PLUANG_MARGIN
  except Exception:
    return 18000.0 * 1.0052


def get_funding_rate(exchange_futures, futures_symbol):
  """Mengambil nilai Funding Rate dari Kucoin Futures."""
  try:
    info = exchange_futures.fetch_funding_rate(futures_symbol)
    return float(info.get("fundingRate", 0))
  except Exception as e:
    logging.error(f"Gagal ambil funding rate {futures_symbol}: {e}")
    return None


def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 20) -> dict:
  swing_low = float(df_4h["low"].iloc[-window - 1 : -1].min())
  swing_high = float(df_4h["high"].iloc[-window - 1 : -1].max())
  return {"swing_high": swing_high, "swing_low": swing_low}


def hitung_skor_hybrid(
    ema9_now,
    ema21_now,
    is_spike_vol_tech,
    vol_spike_smc,
    golden_cross,
    pullback_bounce,
    inflection_entry,
    bull_sweep_smc,
    harga_idr,
    sl_price,
    tp_price,
    funding_rate=None,
):
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
  elif inflection_entry:
    score += 25
    breakdown.append("• MA Inflection / Squeeze (+25)")

  if bull_sweep_smc:
    score += 30
    breakdown.append("• SMC Liquidity Sweep (+30)")

  # 4. Risk-to-Reward Ratio / RRR (25 Poin)
  risk = harga_idr - sl_price
  reward = tp_price - harga_idr
  rrr = (reward / risk) if risk > 0 else 0.0

  if rrr >= 2.0:
    score += 25
    breakdown.append(f"• RRR Sangat Baik ({rrr:.2f} >= 2.0) (+25)")
  elif rrr >= 1.5:
    score += 15
    breakdown.append(f"• RRR Cukup Baik ({rrr:.2f} >= 1.5) (+15)")

  # 5. SKORING TERINTEGRASI FUNDING RATE (-15 / +15 Poin)
  if funding_rate is not None:
    fr_pct = funding_rate * 100
    if funding_rate < -0.0005:  # < -0.05% (Potensi Short Squeeze)
      score += 15
      breakdown.append(f"• Funding Rate Negatif Squeeze ({fr_pct:.4f}%) (+15)")
    elif funding_rate > 0.0005:  # > +0.05% (Overheated Long / Rawan Dump)
      score -= 15
      breakdown.append(
          f"• Funding Rate Overheated Long ({fr_pct:.4f}%) (-15)"
      )
    else:
      breakdown.append(f"• Funding Rate Normal ({fr_pct:+.4f}%) (+0)")
  else:
    breakdown.append("• Funding Rate: N/A (+0)")

  final_score = max(0, min(score, 100))
  return final_score, rrr, breakdown


# --- ANALISA SINGLE KOIN ---
def analisa_koin_hybrid(exchange_spot, exchange_futures, symbol, usd_idr):
  try:
    bars_1h = exchange_spot.fetch_ohlcv(symbol, timeframe="1h", limit=50)
    bars_4h = exchange_spot.fetch_ohlcv(symbol, timeframe="4h", limit=30)

    if len(bars_1h) < 40 or len(bars_4h) < 15:
      return None

    df_1h = pd.DataFrame(
        bars_1h, columns=["timestamp", "open", "high", "low", "close", "volume"]
    ).astype(float)
    df_4h = pd.DataFrame(
        bars_4h, columns=["timestamp", "open", "high", "low", "close", "volume"]
    ).astype(float)

    curr_idx = -2  # Candle tertutup
    prev_idx = -3
    c = df_1h.iloc[curr_idx]
    p = df_1h.iloc[prev_idx]

    latest_c = df_1h.iloc[-1]
    harga_idr = float(latest_c["close"] * usd_idr)
    high_idr = float(latest_c["high"] * usd_idr)
    low_idr = float(latest_c["low"] * usd_idr)

    # ATR 1H
    tr0 = df_1h["high"] - df_1h["low"]
    tr1 = (df_1h["high"] - df_1h["close"].shift(1)).abs()
    tr2 = (df_1h["low"] - df_1h["close"].shift(1)).abs()
    df_1h["tr"] = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
    df_1h["atr"] = df_1h["tr"].rolling(window=14).mean()
    atr_idr = float(df_1h["atr"].iloc[curr_idx] * usd_idr)

    # 1. SMC LOGIC
    avg_vol_smc = float(df_1h["volume"].iloc[-22:-2].median())
    candle_range = c["high"] - c["low"]
    lower_wick = min(c["close"], c["open"]) - c["low"]
    upper_wick = c["high"] - max(c["close"], c["open"])
    vol_spike_smc = c["volume"] > (avg_vol_smc * VOL_MULTIPLIER_SMC)

    tutup_hijau_smc = c["close"] > c["open"]
    tutup_merah_smc = c["close"] < c["open"]
    body_size = abs(c["close"] - c["open"])
    is_panic_dump = tutup_merah_smc and (body_size > candle_range * 0.5)

    bull_sweep_smc = bool(
        (lower_wick > candle_range * 0.35)
        and vol_spike_smc
        and tutup_hijau_smc
        and not is_panic_dump
        and (c["close"] > p["low"])
    )

    bear_sweep_smc = bool(
        (upper_wick > candle_range * 0.35)
        and vol_spike_smc
        and tutup_merah_smc
        and (c["close"] < p["high"])
    )

    # 2. TEKNIKAL LOGIC & MA INFLECTION / SQUEEZE
    df_1h["ema9"] = df_1h["close"].ewm(span=9, adjust=False).mean()
    df_1h["ema21"] = df_1h["close"].ewm(span=21, adjust=False).mean()
    df_1h["avg_vol_tech"] = df_1h["volume"].rolling(window=3).mean().shift(1)

    is_spike_vol_tech = bool(
        c["volume"]
        > (df_1h["avg_vol_tech"].iloc[curr_idx] * VOL_MULTIPLIER_TECH)
    )

    slope_ema9 = (
        abs(df_1h["ema9"].iloc[curr_idx] - df_1h["ema9"].iloc[prev_idx])
        / df_1h["ema9"].iloc[prev_idx]
        * 100
    )
    is_sudut_tajam = slope_ema9 > 0.25
    ema9_now = df_1h["ema9"].iloc[curr_idx]
    ema9_prev = df_1h["ema9"].iloc[prev_idx]
    ema21_now = df_1h["ema21"].iloc[curr_idx]
    ema21_prev = df_1h["ema21"].iloc[prev_idx]

    golden_cross = bool(
        (ema9_prev < ema21_prev)
        and (ema9_now > ema21_now)
        and is_spike_vol_tech
        and is_sudut_tajam
    )

    tren_bullish = ema9_now > ema21_now
    sentuh_ema21 = c["low"] <= (ema21_now * 1.002)
    tutup_hijau = c["close"] > c["open"]
    tutup_atas_ema9 = c["close"] > ema9_now
    vol_oke_tech = c["volume"] > df_1h["avg_vol_tech"].iloc[curr_idx]
    pullback_bounce = bool(
        tren_bullish
        and sentuh_ema21
        and tutup_hijau
        and tutup_atas_ema9
        and vol_oke_tech
    )

    # Deteksi Patahan / Inflection Point & Squeeze MA
    df_1h["ema_spread"] = (df_1h["ema9"] - df_1h["ema21"]).abs()
    spread_now = df_1h["ema_spread"].iloc[curr_idx]
    spread_prev = df_1h["ema_spread"].iloc[prev_idx]
    spread_prev2 = df_1h["ema_spread"].iloc[prev_idx - 1]

    slope_now_val = (
        df_1h["ema9"].iloc[curr_idx] - df_1h["ema9"].iloc[prev_idx]
    )
    slope_prev_val = df_1h["ema9"].iloc[prev_idx] - df_1h["ema9"].iloc[prev_idx - 1]
    is_inflection_bottom = bool(
        (slope_prev_val < 0) and (slope_now_val >= 0)
    )
    is_ma_squeeze = bool(
        (spread_prev < spread_prev2)
        and (spread_now > spread_prev)
        and (spread_now < (df_1h["close"].iloc[curr_idx] * 0.003))
    )

    inflection_entry = bool(
        tren_bullish and (is_inflection_bottom or is_ma_squeeze) and vol_oke_tech
    )

    tech_entry_signal = golden_cross or pullback_bounce or inflection_entry

    # SWING 4H
    swing = deteksi_swing_4h(df_4h, window=7)
    swing_high_idr = swing["swing_high"] * usd_idr
    swing_low_idr = swing["swing_low"] * usd_idr

    sl_bullish = swing_low_idr - (0.5 * atr_idr)
    tp_bullish = swing_high_idr

    if tp_bullish <= harga_idr * 1.015:
      tp_bullish = harga_idr + (3.5 * atr_idr)
    if sl_bullish >= harga_idr * 0.985:
      sl_bullish = harga_idr - (1.8 * atr_idr)

    # FETCH FUNDING RATE KUCOIN FUTURES
    futures_symbol = FUTURES_MAP.get(symbol)
    funding_rate = (
        get_funding_rate(exchange_futures, futures_symbol)
        if futures_symbol
        else None
    )

    # SKORING TERINTEGRASI
    score, rrr, breakdown = hitung_skor_hybrid(
        ema9_now,
        ema21_now,
        is_spike_vol_tech,
        vol_spike_smc,
        golden_cross,
        pullback_bounce,
        inflection_entry,
        bull_sweep_smc,
        harga_idr,
        sl_bullish,
        tp_bullish,
        funding_rate,
    )

    pemicu_list = []
    if golden_cross:
      pemicu_list.append("Golden Cross")
    if pullback_bounce:
      pemicu_list.append("Pullback Bounce")
    if inflection_entry:
      pemicu_list.append("MA Inflection / Squeeze")
    if bull_sweep_smc:
      pemicu_list.append("SMC Bull Sweep")
    trigger_str = " + ".join(pemicu_list) if pemicu_list else "Monitoring"

    return {
        "symbol": symbol,
        "pair_name": symbol.replace("/", "-").replace("USDT", "IDR"),
        "harga_idr": float(harga_idr),
        "high_idr": float(high_idr),
        "low_idr": float(low_idr),
        "sl_price": float(sl_bullish),
        "tp_price": float(tp_bullish),
        "is_entry": (tech_entry_signal or bull_sweep_smc),
        "is_emergency_exit": bear_sweep_smc,
        "emerg_reason": "SMC Bear Sweep" if bear_sweep_smc else "",
        "trigger_str": trigger_str,
        "score": score,
        "rrr": rrr,
        "breakdown": breakdown,
    }
  except Exception as e:
    logging.error(f"Error analisa Hybrid {symbol}: {e}")
    return None


# --- MAIN EXECUTOR ---
async def main():
  logging.info(
      "Menjalankan Paper Trader Hybrid Multi-Asset Scanner (v4.4 -"
      " Inflection/Squeeze Edition)..."
  )

  exchange_spot = ccxt.kucoin({
      "enableRateLimit": True,
      "options": {"defaultType": "spot"},
      "timeout": 30000,
  })
  exchange_futures = ccxt.kucoinfutures(
      {"enableRateLimit": True, "timeout": 30000}
  )

  try:
    exchange_spot.load_markets()
  except Exception as e:
    logging.error(f"Gagal memuat market: {e}")
    return

  bot = Bot(token=TOKEN)
  usd_idr = get_usd_idr()
  now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
  time_wib_str = now_wib.strftime("%H:%M")
  logging.info(f"[{time_wib_str} WIB] USD/IDR={usd_idr:,.0f}")

  state = load_state()
  pos = state.get("active_position")

  # =========================================================
  # 1. EVALUASI EXIT (JIKA ADA POSISI OPEN)
  # =========================================================
  if pos:
    symbol = pos["symbol"]
    pair_name = pos["pair_name"]
    data = analisa_koin_hybrid(
        exchange_spot, exchange_futures, symbol, usd_idr
    )

    if data:
      entry_p, amount, sl, tp = (
          pos["entry_price_idr"],
          pos["amount"],
          pos["sl"],
          pos["tp"],
      )
      is_win = data["high_idr"] >= tp
      is_loss = data["low_idr"] <= sl
      is_emerg_exit = data["is_emergency_exit"]

      if is_win or is_loss or is_emerg_exit:
        if is_loss:
          exit_reason = "STOP LOSS (SWING 4H) 🛑"
          exit_price = sl
        elif is_win:
          exit_reason = "TAKE PROFIT (SWING 4H) 🎯"
          exit_price = tp
        else:
          exit_reason = f"EMERGENCY EXIT ({data['emerg_reason']}) ⚠️"
          exit_price = data["harga_idr"]

        gross = exit_price * amount
        net = gross - (gross * FEE_TAX_RATE)
        modal = entry_p * amount
        pnl_val = net - modal
        pnl_pct = (pnl_val / modal) * 100
        status = "WIN" if pnl_val > 0 else "LOSS"

        state["cash_idr"] += net
        state["history"].append({
            "pair": pair_name,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_idr": round(pnl_val, 2),
            "status": status,
            "entry_time": pos["entry_time"],
            "exit_time": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
        })
        state["stats"]["total_trades"] += 1
        if status == "WIN":
          state["stats"]["wins"] += 1
        else:
          state["stats"]["losses"] += 1
        state["stats"]["total_pnl_idr"] += round(pnl_val, 2)
        state["active_position"] = None
        save_state(state)

        stats = state["stats"]
        wr = (
            (stats["wins"] / stats["total_trades"]) * 100
            if stats["total_trades"] > 0
            else 0
        )

        msg = (
            f"🧪 <b>[PAPER TRADING - HYBRID EXIT]</b> {pair_name}\n"
            f"──────────────────────────────\n"
            f"Alasan    : {exit_reason}\n"
            f"Harga In  : Rp {entry_p:,.0f}\n"
            f"Harga Out : Rp {exit_price:,.0f}\n"
            f"P/L       : {pnl_pct:+.2f}% (Rp {pnl_val:+,.0f})\n\n"
            f"📊 <b>REKAP TOTAL HYBRID</b>:\n"
            f"• Total Trade : {stats['total_trades']}x\n"
            f"• Win / Loss  : {stats['wins']} Win / {stats['losses']} Loss (WR:"
            f" {wr:.1f}%)\n"
            f"• Total P/L   : Rp {stats['total_pnl_idr']:+,.0f}\n"
            f"• Sisa Kas    : Rp {state['cash_idr']:,.0f}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
        logging.info(f"Notif Exit Terkirim untuk {pair_name}")
      else:
        logging.info(f"— Posisi {pair_name} masih aktif (1 Open Position Lock).")

  # =========================================================
  # 2. SCANNING, RANKING & PENYIMPANAN SIGNAL JSON
  # =========================================================
  candidates = []
  scanned_summary = []

  for symbol in ASSET_LIST:
    res = analisa_koin_hybrid(exchange_spot, exchange_futures, symbol, usd_idr)
    if res:
      scanned_summary.append(res)
      if res["is_entry"] and res["score"] >= MIN_SCORE_ENTRY:
        candidates.append(res)
        logging.info(
            f"✅ {symbol}: Sinyal VALID! Pemicu: {res['trigger_str']} | Skor:"
            f" {res['score']} | RRR: {res['rrr']:.2f}x"
        )
      elif res["is_entry"] and res["score"] < MIN_SCORE_ENTRY:
        logging.info(
            f"🚫 {symbol}: Sinyal diabaikan karena Skor ({res['score']}) di"
            f" bawah batas minimum ({MIN_SCORE_ENTRY})."
        )
      else:
        logging.info(f"— {symbol}: tidak ada sinyal")
    else:
      logging.info(f"— {symbol}: gagal dianalisa")

  scanned_summary.sort(key=lambda x: x["score"], reverse=True)

  signal_payload = {
      "timestamp": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
      "top_signals": scanned_summary[:3],
      "all_scanned": scanned_summary,
  }
  save_signal(signal_payload)
  logging.info("📄 File signal_agr.json berhasil diperbarui.")

  if state.get("active_position") is not None:
    logging.info(
        "Kunci 1 Open Position Aktif. Mengabaikan eksekusi entry baru."
    )
    return

  if not candidates:
    logging.info(
        f"— Tidak ada sinyal BELI yang valid (memenuhi syarat Skor >="
        f" {MIN_SCORE_ENTRY}) pada siklus ini."
    )
    return

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
        "entry_time": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state["cash_idr"] = 0.0
    save_state(state)

    rincian_skor = "\n".join(winner["breakdown"])

    msg = (
        f"🧪 <b>[PAPER TRADING - HYBRID ENTRY]</b> {winner['pair_name']}\n"
        f"──────────────────────────────\n"
        f"Pemicu    : {winner['trigger_str']}\n"
        f"📊 <b>SKOR HYBRID JUARA #1</b>: <code>{winner['score']}/100</code>\n"
        f"<b>Rincian Skoring</b>:\n{rincian_skor}\n"
        f"──────────────────────────────\n"
        f"Modal In  : Rp {available_cash:,.0f}\n"
        f"Harga In  : Rp {winner['harga_idr']:,.0f}\n"
        f"Target TP : Rp {winner['tp_price']:,.0f}\n"
        f"Batas SL  : Rp {winner['sl_price']:,.0f}"
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
    logging.info(f"Notif Entry Terkirim untuk Juara #1 ({winner['pair_name']})")


if __name__ == "__main__":
  asyncio.run(main())
