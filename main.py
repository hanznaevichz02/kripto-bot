"""
========================================================
    KRIPTO BOT — Smart Money Edition (Main Scanner)
    Versi: 4.1 (Integrasi Informan Pribadi & Pra-Golden Cross)
    SPOT MARKET — [PORTFOLIO & WARNING MONITOR ONLY]
========================================================
"""

import os
import json
import asyncio
import logging
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import httpx
import pandas as pd
import ccxt.async_support as ccxt
from telegram import Bot

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CryptoBot")

# ============================================================
# KONFIGURASI UTAMA
# ============================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MIN_RRR_THRESHOLD = 1.2      # Batas minimum RRR disesuaikan untuk scalping/swing pendek
MIN_PROFIT_PCT_THRESHOLD = 1.3 # Target profit minimal untuk cover fee transaksi dan pajak

PORTFOLIO: Dict[str, Dict[str, float]] = {
    'BTC/USDT': {'buy_price_idr': 1_311_140_722, 'amount': 0.00076261},
    'ETH/USDT': {'buy_price_idr':    37_447_016, 'amount': 0.05060638}
}

JAM_LAPORAN = {9, 14, 20}

# ============================================================
# [DI-NONAKTIFKAN] ASSET_LIST & FUTURES_MAP (Scanner Utama)
# ============================================================
# ASSET_LIST: List[str] = [
#     'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'SUI/USDT',
#     'XRP/USDT', 'LINK/USDT', 'AAVE/USDT', 'DOT/USDT', 'ONDO/USDT',
#     'ARB/USDT', 'NEAR/USDT', 'TAO/USDT', 'AVAX/USDT',
#     'ADA/USDT', 'TRX/USDT', 'UNI/USDT'
# ]
# 
# FUTURES_MAP: Dict[str, str] = {
#     'BTC/USDT': 'XBTUSDTM',  'ETH/USDT': 'ETHUSDTM',
#     'SOL/USDT': 'SOLUSDTM',  'BNB/USDT': 'BNBUSDTM',
#     'SUI/USDT': 'SUIUSDTM',  'XRP/USDT': 'XRPUSDTM',
#     'LINK/USDT': 'LINKUSDTM', 'AAVE/USDT': 'AAVEUSDTM',
#     'DOT/USDT':  'DOTUSDTM',  'ONDO/USDT': 'ONDOUSDTM',
#     'ARB/USDT':  'ARBUSDTM',  'NEAR/USDT': 'NEARUSDTM',
#     'ZEC/USDT':  'ZECUSDTM',  'TAO/USDT':  'TAOUSDTM',
#     'AVAX/USDT': 'AVAXUSDTM', 'ADA/USDT':  'ADAUSDTM'
# }

# BULLISH_SIGNAL_TYPES = {'BULL_SWEEP', 'BULL_OB', 'AKUMULASI', 'BULL_BREAKOUT', 'EMA9_BREAK'}

DESKRipSI = {
    'BULL_SWEEP':     ("HARGA AKAN NAIK", "Bandar sapu SL ritel disertai FVG, siap loncat naik."),
    'BEAR_SWEEP':     ("HARGA AKAN TURUN", "Bandar jebak ritel beli, siap dump."),
    'BULL_OB':         ("ZONA BELI BANDAR", "Harga kembali ke area demand institusi + FVG."),
    'BEAR_OB':         ("ZONA JUAL BANDAR", "Harga menyentuh area supply institusi."),
    'AKUMULASI':       ("AKUMULASI WHALE", "Volume besar, spread sempit (Nampung barang)."),
    'DISTRIBUSI':      ("DISTRIBUSI WHALE", "Volume besar, spread sempit (Jualan barang)."),
    'BULL_BREAKOUT':  ("BREAKOUT VOLUME", "Modal besar jebol atap ke atas + FVG."),
    'BEAR_BREAKOUT':  ("BEAR_BREAKOUT", "Modal besar jebol lantai ke bawah."),
    'EMA9_BREAK':     ("PRA-GOLDEN CROSS", "Harga jebol EMA9 ke atas + Volume Beli (Curi start sebelum Golden Cross)."),
}

# ============================================================
# ASYNC API HELPERS
# ============================================================
async def get_usd_idr(client: httpx.AsyncClient) -> float:
    try:
        r = await client.get("https://indodax.com/api/ticker/usdtidr", timeout=5.0)
        return float(r.json()['ticker']['last'])
    except Exception as e:
        logger.warning(f"Gagal mengambil kurs USD/IDR ({e}), menggunakan fallback Rp 18,000")
        return 18000.0

async def get_fear_greed(client: httpx.AsyncClient) -> Dict[str, Any]:
    try:
        r = await client.get("https://api.alternative.me/fng/?limit=1", timeout=5.0)
        d = r.json()['data'][0]
        return {'value': int(d['value']), 'label': d['value_classification']}
    except Exception as e:
        logger.warning(f"Gagal mengambil Fear & Greed ({e}), menggunakan fallback Neutral")
        return {'value': 50, 'label': 'Neutral'}

# async def get_funding_rate(exchange_futures: ccxt.Exchange, futures_symbol: str) -> Optional[float]:
#     try:
#         info = await exchange_futures.fetch_funding_rate(futures_symbol)
#         return float(info.get('fundingRate', 0))
#     except Exception as e:
#         logger.warning(f"Funding Rate error [{futures_symbol}]: {e}")
#         return None

def format_rp(nilai: float) -> str:
    return f"Rp {nilai:,.0f}"

# ============================================================
# [DI-KOMENTARI] KALKULASI INDIKATOR & SMC (Scanner)
# ============================================================
# def hitung_atr(df: pd.DataFrame, period: int = 14) -> float:
#     tr = pd.concat([
#         df['high'] - df['low'],
#         (df['high'] - df['close'].shift()).abs(),
#         (df['low']  - df['close'].shift()).abs(),
#     ], axis=1).max(axis=1)
#     return float(tr.rolling(period).mean().iloc[-2])
# 
# def hitung_volume_delta(df: pd.DataFrame) -> pd.Series:
#     hl = (df['high'] - df['low']).replace(0, 1e-9)
#     return df['volume'] * ((df['close'] - df['open']) / hl)
# 
# def hitung_sudut(df: pd.DataFrame, period: int = 10) -> float:
#     if len(df) < period: return 0.0
#     y = df['close'].iloc[-period:].values
#     x = np.arange(period)
#     y_norm = (y - y[0]) / (y[0] + 1e-9) * 100
#     slope, _ = np.polyfit(x, y_norm, 1)
#     return float(math.degrees(math.atan(slope)))
# 
# def deteksi_swing_4h(df_4h: pd.DataFrame, window: int = 7) -> Dict[str, float]:
#     swing_low = float(df_4h['low'].iloc[-window-1:-1].min())
#     swing_high = float(df_4h['high'].iloc[-window-1:-1].max())
#     return {'swing_high': swing_high, 'swing_low': swing_low}
# 
# def deteksi_order_block(df: pd.DataFrame) -> Dict[str, Optional[Dict[str, float]]]:
#     result = {'bullish_ob': None, 'bearish_ob': None}
#     avg_body = (df['close'] - df['open']).abs().rolling(10).median()
#     for i in range(len(df) - 3, max(len(df) - 15, 0), -1):
#         row = df.iloc[i]
#         nxt = df.iloc[i + 1]
#         body = abs(row['close'] - row['open'])
#         if body < avg_body.iloc[i] * 1.2: continue
# 
#         if row['close'] < row['open'] and nxt['close'] > row['high']:
#             if df.iloc[i+2:]['close'].min() >= row['low']:
#                 result['bullish_ob'] = {'high': float(row['high']), 'low': float(row['low'])}
#                 break
# 
#         if row['close'] > row['open'] and nxt['close'] < row['low']:
#             if df.iloc[i+2:]['close'].max() <= row['high']:
#                 result['bearish_ob'] = {'high': float(row['high']), 'low': float(row['low'])}
#                 break
#     return result
# 
# def deteksi_fvg(df: pd.DataFrame) -> Dict[str, Optional[Dict[str, float]]]:
#     result = {'bullish_fvg': None, 'bearish_fvg': None}
#     if len(df) < 10: return result
#     
#     for i in range(len(df) - 1, max(len(df) - 15, 2), -1):
#         c1 = df.iloc[i-2]
#         c3 = df.iloc[i]
#         
#         if c3['low'] > c1['high'] and result['bullish_fvg'] is None:
#             result['bullish_fvg'] = {'high': float(c3['low']), 'low': float(c1['high'])}
#         
#         if c3['high'] < c1['low'] and result['bearish_fvg'] is None:
#             result['bearish_fvg'] = {'high': float(c1['low']), 'low': float(c3['high'])}
#             
#         if result['bullish_fvg'] and result['bearish_fvg']:
#             break
#             
#     return result

# ============================================================
# [DI-KOMENTARI] ANALISA UTAMA SCANNER
# ============================================================
# def analisa(...):
#     pass

# ============================================================
# FUNGSI ANTI SPAM NOTIF
# ============================================================
STATE_FILE = "last_sent_alert.json"

def cek_dan_simpan_duplikasi(symbol: str, signal_type: str) -> bool:
    last_data = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                last_data = json.load(f)
        except Exception:
            pass

    if last_data.get(symbol) == signal_type:
        return True  
    
    last_data[symbol] = signal_type
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(last_data, f, indent=4)
    except Exception as e:
        logger.error(f"Gagal menyimpan state alert: {e}")
        
    return False

# ============================================================
# TELEGRAM FORMATTERS
# ============================================================
def format_pesan(symbol: str, s: dict, is_porto_alert: bool = False) -> str:
    tipe, harga = s['tipe'], format_rp(s['harga'])
    judul, ket = DESKRIPSI.get(tipe, (tipe, ""))
    
    fr = s.get('funding_rate')
    if fr is None: fr_str = "N/A"
    elif fr > 0.0005: fr_str = f"+{fr*100:.4f}% (Rawan Dump)"
    elif fr < -0.0005: fr_str = f"{fr*100:+.4f}% (Squeeze)"
    else: fr_str = f"{fr*100:+.4f}% (Normal)"

    is_bullish = tipe in {'BULL_SWEEP', 'BULL_OB', 'AKUMULASI', 'BULL_BREAKOUT', 'EMA9_BREAK'}
    if is_bullish:
        rm_label_1, rm_val_1 = "TP (Target)", format_rp(s.get('tp_buy', 0))
        rm_label_2, rm_val_2 = "SL (Batas) ", format_rp(s.get('sl_buy', 0))
        rrr_value = s.get('rrr_buy', s.get('rrr', 0.0))  
    else:
        rm_label_1, rm_val_1 = "Serok Bawah", format_rp(s.get('tp_sell', 0))
        rm_label_2, rm_val_2 = "Invalidasi ", format_rp(s.get('sl_sell', 0))
        rrr_value = s.get('rrr_sell', s.get('rrr', 0.0))  

    header = f"🚨 <b>WARNING PORTOFOLIO — {symbol}</b>" if is_porto_alert else f"⚡ <b>QUANT SIGNAL — {symbol}</b>"
    vol_ultra_str = " (ULTRA)" if s.get('vol_ultra') else ""
    strength_str = f" {s.get('strength', '')}" if s.get('strength') else ""

    return (
        f"{header}{strength_str}\n"
        f"<code>"
        f"[ 1. SIGNAL DETECTION ]\n"
        f"  • Trigger : {judul}\n"
        f"  • Tren 4H : {s.get('trend_4h', 'N/A')} ({s.get('fase_4h', 'N/A')})\n"
        f"  • Skor    : {s.get('skor', 0.0)} / 100\n"
        f"  • Sudut   : {s.get('sudut', 0.0):+.2f}°\n"
        f"  • RSI 4H  : {s.get('rsi_4h', 0.0):.1f}\n"
        f"  • Harga   : {harga}\n"
        f"------------------------------\n"
        f"[ 2. MARKET METRICS ]\n"
        f"  • Volume   : {s.get('vol_ratio', 0)}x median{vol_ultra_str}\n"
        f"  • Delta   : {'Beli Dominan' if s.get('cvd_naik') else 'Jual Dominan'}\n"
        f"  • Funding : {fr_str}\n"
        f"  • Market  : {s['fear_greed']['value']} ({s['fear_greed']['label']})\n"
        f"------------------------------\n"
        f"[ 3. RISK MANAGEMENT ]\n"
        f"  • {rm_label_1} : {rm_val_1}\n"
        f"  • {rm_label_2} : {rm_val_2}\n"
        f"  • RRR Ratio : {rrr_value:.2f}x\n"
        f"  • Target %  : {s.get('profit_pct', 0.0):.2f}%\n"
        f"</code>\n"
        f"🎯 <b>ACTION PLAN :</b> {s['aksi']}\n"
        f"💡 <b>Insight    :</b> {ket}"
    )

# [DI-KOMENTARI] Radar Informan
# def format_informan_radar(...):
#     pass

# ============================================================
# LAPORAN PORTOFOLIO
# ============================================================
async def kirim_laporan(bot: Bot, exchange: ccxt.Exchange, usd_idr: float, fear_greed: dict):
    total_modal = total_nilai = 0.0
    baris = []

    try:
        tickers = await exchange.fetch_tickers(list(PORTFOLIO.keys()))
    except Exception:
        tickers = {}

    for sym, p in PORTFOLIO.items():
        try:
            last_price = tickers[sym]['last'] if sym in tickers else (await exchange.fetch_ticker(sym))['last']
            harga_kini = last_price * usd_idr
            modal = p['buy_price_idr'] * p['amount']
            nilai = harga_kini * p['amount']
            pnl_val = nilai - modal
            pnl_pct = (pnl_val / modal) * 100
            ikon = "🟢" if pnl_pct >= 0 else "🔴"

            total_modal += modal
            total_nilai += nilai

            baris.append(
                f"{ikon} <b>{sym}</b>\n<code>"
                f"Beli : {format_rp(p['buy_price_idr'])}\n"
                f"Skrg : {format_rp(harga_kini)}\n"
                f"P/L  : {pnl_pct:+.2f}% ({format_rp(pnl_val)})\n</code>"
            )
        except Exception as e:
            baris.append(f"⚠️ {sym} — gagal ({e})")

    total_pnl = total_nilai - total_modal
    total_pnl_pct = (total_pnl / total_modal * 100) if total_modal else 0
    ikon_total = "🟢" if total_pnl_pct >= 0 else "🔴"
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)

    fng_translation = {
        "Extreme Fear": "Ketakutan Ekstrem", "Fear": "Takut",
        "Neutral": "Netral", "Greed": "Serakah", "Extreme Greed": "Keserakahan Ekstrem"
    }
    behavior_map = {
        "Extreme Fear": "Ritel Panik JUAL, Bandar BELI",
        "Fear": "Ritel cicil JUAL, Bandar cicil BELI",
        "Neutral": "Ritel WAIT & SEE, Bandar Konsolidasi",
        "Greed": "Ritel cicil BELI, Bandar cicil JUAL",
        "Extreme Greed": "Ritel FOMO BELI, Bandar JUAL (TP)"
    }

    label_indo = fng_translation.get(fear_greed['label'], fear_greed['label'])
    analisis_pasar = behavior_map.get(fear_greed['label'], "Ritel & Bandar Bergerak Dinamis")

    pesan = (
        f"📊 <b>PORTOFOLIO — {now_wib.strftime('%d %b %Y, %H:%M WIB')}</b>\n\n"
        + "\n".join(baris)
        + f"\n────────────────────\n"
        + f"{ikon_total} <b>SUMMARY</b>\n<code>"
        + f"Total Beli : {format_rp(total_modal)}\n"
        + f"Total Skrg : {format_rp(total_nilai)}\n"
        + f"Total P/L  : {total_pnl_pct:+.2f}% ({format_rp(total_pnl)})\n"
        + f"------------------------------\n"
        + f"Pasar      : {fear_greed['value']} — {label_indo}\n"
        + f"Aksi       : {analisis_pasar}\n</code>"
    )
    await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='HTML')

# ============================================================
# [DI-KOMENTARI] WORKER SCANNER PER ASSET
# ============================================================
# async def scan_asset(...):
#     pass

# ============================================================
# MAIN EXECUTOR (PORTFOLIO & WARNING MONITOR ONLY)
# ============================================================
async def main():
    if not TOKEN or not CHAT_ID: return
    
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)

    # 1. FAIL-SAFE: Tulis sinyal kosong (reset) SEBELUM melakukan apa pun
    default_signal = {
        "timestamp": now_wib.strftime('%Y-%m-%d %H:%M:%S'),
        "symbol": "NONE",
        "signal_type": None,
        "score": 0.0,
        "angle": 0.0,
        "current_price": 0.0,
        "high_price": 0.0,
        "low_price": 0.0,
        "sl_price": 0.0,
        "tp_price": 0.0,
        "rrr": 0.0,
        "profit_pct": 0.0,
        "status": "PORTFOLIO_MONITOR_ONLY"
    }
    
    try:
        with open("signal_main.json", "w") as f:
            json.dump(default_signal, f, indent=4)
    except Exception as e:
        logger.error(f"Gagal melakukan reset file signal_main.json: {e}")
        return 

    # 2. INISIALISASI EXCHANGE (Hanya Spot Kucoin untuk cek portofolio)
    exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}, 'timeout': 30_000})
    bot = Bot(token=TOKEN)

    try:
        await exchange.load_markets()
        async with httpx.AsyncClient() as client:
            usd_idr_task = get_usd_idr(client)
            fear_greed_task = get_fear_greed(client)
            usd_idr, fear_greed = await asyncio.gather(usd_idr_task, fear_greed_task)

        # 3. LAPORAN PORTOFOLIO REGULER / CEK BERKALA
        logger.info("🔍 Memeriksa kondisi portofolio...")
        if now_wib.hour in JAM_LAPORAN and now_wib.minute < 30:
            await kirim_laporan(bot, exchange, usd_idr, fear_greed)

    except Exception as e:
        logger.error(f"Error utama pada executor main(): {e}")
    finally:
        try: await exchange.close()
        except: pass

if __name__ == '__main__':
    asyncio.run(main())
