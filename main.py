import os
import asyncio
import ccxt
import pandas as pd
import requests
from telegram import Bot
from datetime import datetime, timedelta, timezone

# ============================================================
# KONFIGURASI
# ============================================================
TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PORTFOLIO = {
    'BTC/USDT': {'buy_price_idr': 1_311_140_722, 'amount': 0.00076261},
    'ETH/USDT': {'buy_price_idr':    37_447_016, 'amount': 0.05060638},
}

ASSET_LIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'LINK/USDT', 'AAVE/USDT', 'ONDO/USDT', 'DOT/USDT',
]

# Jam laporan portofolio (WIB)
JAM_LAPORAN = {9, 14, 20}

# ============================================================
# HELPER
# ============================================================
def get_usd_idr() -> float:
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return r.json()['rates']['IDR']
    except Exception:
        return 16_400.0


def hitung_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)


def hitung_macd(series: pd.Series):
    ema12  = series.ewm(span=12, adjust=False).mean()
    ema26  = series.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return float(macd.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def hitung_atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def format_rp(nilai: float) -> str:
    return f"Rp {nilai:,.0f}"


# ============================================================
# ANALISA PER KOIN
# ============================================================
def analisa(df: pd.DataFrame, usd_idr: float) -> dict | None:
    """
    Kembalikan dict sinyal atau None kalau tidak ada sinyal.
    Prioritas: KONFIRMASI > SIAGA > CROSS > None
    """
    if len(df) < 30:
        return None

    close = df['close']

    # Indikator
    df['ema9']  = close.ewm(span=9,  adjust=False).mean()
    df['ema21'] = close.ewm(span=21, adjust=False).mean()
    df['ema50'] = close.ewm(span=50, adjust=False).mean()

    rsi  = hitung_rsi(close)
    macd_val, macd_sig, macd_hist = hitung_macd(close)
    atr  = hitung_atr(df)

    # Candle terakhir & sebelumnya
    c  = df.iloc[-1]
    p  = df.iloc[-2]
    pp = df.iloc[-3]

    harga_idr = c['close'] * usd_idr
    atr_idr   = atr * usd_idr

    # Spike
    avg_body = df['close'].sub(df['open']).abs().iloc[-6:-1].mean()
    avg_vol  = df['volume'].iloc[-6:-1].mean()
    body_now = abs(c['close'] - c['open'])
    is_spike_body = body_now > avg_body * 2.0
    is_spike_vol  = c['volume'] > avg_vol * 1.8

    # Arah candle
    is_bullish = c['close'] > c['open']
    is_bearish = c['close'] < c['open']

    # EMA Cross
    golden     = (p['ema9'] < p['ema21']) and (c['ema9'] > c['ema21'])
    dead       = (p['ema9'] > p['ema21']) and (c['ema9'] < c['ema21'])
    gap_now    = c['ema21'] - c['ema9']
    gap_prev   = p['ema21'] - p['ema9']
    pra_golden = (gap_now > 0) and (gap_now < gap_prev) and (c['ema9'] > p['ema9'])
    di_atas_ema50  = c['close'] > c['ema50']

    # Pivot (1 candle sebelumnya)
    pivot_p    = (p['high'] + p['low'] + p['close']) / 3
    pivot_r    = p['high'] - p['low']
    r2 = pivot_p + pivot_r
    r3 = pivot_p + 2 * pivot_r
    s2 = pivot_p - pivot_r
    s3 = pivot_p - 2 * pivot_r
    tembus_r3 = c['close'] >= r3
    tembus_r2 = (c['close'] >= r2) and not tembus_r3
    jebol_s3  = c['close'] <= s3
    jebol_s2  = (c['close'] <= s2) and not jebol_s3

    # Struktur HH/HL
    win_now  = df.iloc[-14:]
    win_prev = df.iloc[-28:-14]
    hh_hl = (win_now['high'].max() > win_prev['high'].max()) and \
            (win_now['low'].min()  > win_prev['low'].min())

    # Filter RSI
    rsi_oke_beli = rsi < 68
    rsi_oke_jual = rsi > 32

    # SL & TP berbasis ATR
    sl_buy = harga_idr - 1.5 * atr_idr
    tp_buy = harga_idr + 2.5 * atr_idr
    sl_sel = harga_idr + 1.5 * atr_idr
    tp_sel = harga_idr - 2.5 * atr_idr

    # Flag alasan yang selalu disertakan
    flags = {
        'rsi': rsi,
        'macd_hist': macd_hist,
        'spike_body': is_spike_body,
        'spike_vol': is_spike_vol,
        'tembus_r2': tembus_r2,
        'tembus_r3': tembus_r3,
        'jebol_s2': jebol_s2,
        'jebol_s3': jebol_s3,
        'golden': golden,
        'dead': dead,
        'pra_golden': pra_golden,
        'di_atas_ema50': di_atas_ema50,
        'di_bawah_ema50': not di_atas_ema50,
        'hh_hl': hh_hl,
    }

    def base(level, arah, sl, tp):
        return {'level': level, 'arah': arah, 'harga': harga_idr,
                'sl': sl, 'tp': tp, **flags}

    # ── LEVEL 1: KONFIRMASI ────────────────────────────────────
    if is_spike_body and is_spike_vol:
        if is_bullish and rsi_oke_beli and macd_hist > 0:
            return base('KONFIRMASI', 'BELI', sl_buy, tp_buy)
        if is_bearish and rsi_oke_jual and macd_hist < 0:
            return base('KONFIRMASI', 'JUAL', sl_sel, tp_sel)

    # ── LEVEL 2: SIAGA ─────────────────────────────────────────
    if is_spike_body or is_spike_vol:
        if is_bullish and rsi_oke_beli and macd_hist > 0:
            return base('SIAGA', 'BELI', sl_buy, tp_buy)
        if is_bearish and rsi_oke_jual and macd_hist < 0:
            return base('SIAGA', 'JUAL', sl_sel, tp_sel)

    # ── LEVEL 3: CROSS ─────────────────────────────────────────
    if golden and rsi_oke_beli:
        return base('CROSS', 'GOLDEN CROSS', sl_buy, tp_buy)
    if dead and rsi_oke_jual:
        return base('CROSS', 'DEAD CROSS', sl_sel, tp_sel)

    # ── LEVEL 4: PRA-GOLDEN ────────────────────────────────────
    if pra_golden and rsi < 55:
        return base('PRA', 'POTENSI NAIK', None, None)

    return None


# ============================================================
# FORMAT PESAN TELEGRAM
# ============================================================
def format_alasan(s: dict) -> str:
    """Susun kalimat alasan sinyal dari flag yang dikirim analisa()."""
    alasan = []

    # Candle & volume
    if s.get('spike_body') and s.get('spike_vol'):
        alasan.append("📊 Spike candle + volume meledak")
    elif s.get('spike_body'):
        alasan.append("📊 Spike candle besar (volume tipis)")
    elif s.get('spike_vol'):
        alasan.append("📊 Volume meledak (body normal)")

    # Pivot breakout/breakdown
    if s.get('tembus_r2'):
        alasan.append("🚀 Harga tembus R2")
    if s.get('tembus_r3'):
        alasan.append("🚀 Harga tembus R3!")
    if s.get('jebol_s2'):
        alasan.append("💥 Harga jebol S2")
    if s.get('jebol_s3'):
        alasan.append("💥 Harga jebol S3!")

    # EMA
    if s.get('golden'):
        alasan.append("📈 EMA9 memotong EMA21 ke atas (Golden Cross)")
    if s.get('dead'):
        alasan.append("📉 EMA9 memotong EMA21 ke bawah (Dead Cross)")
    if s.get('pra_golden'):
        alasan.append("🪝 EMA9 mendekati EMA21 dari bawah")
    if s.get('di_atas_ema50'):
        alasan.append("✅ Harga di atas EMA50 (trend positif)")
    elif s.get('di_bawah_ema50'):
        alasan.append("⚠️ Harga di bawah EMA50 (trend negatif)")

    # RSI
    rsi = s.get('rsi', 50)
    if rsi <= 30:
        alasan.append(f"📉 RSI {rsi} — oversold ekstrem")
    elif rsi <= 40:
        alasan.append(f"📉 RSI {rsi} — area oversold")
    elif rsi >= 70:
        alasan.append(f"📈 RSI {rsi} — overbought, hati-hati")
    elif rsi >= 60:
        alasan.append(f"📈 RSI {rsi} — momentum kuat")
    else:
        alasan.append(f"➡️ RSI {rsi} — netral")

    # MACD histogram
    hist = s.get('macd_hist', 0)
    if hist > 0:
        alasan.append(f"📈 MACD histogram hijau (+{hist:.2f})")
    else:
        alasan.append(f"📉 MACD histogram merah ({hist:.2f})")

    # Struktur HH/HL
    if s.get('hh_hl'):
        alasan.append("📈 Struktur HH+HL (tren naik)")

    return "\n".join(f"  • {a}" for a in alasan)


def format_sinyal(symbol: str, s: dict) -> str:
    level = s['level']
    arah  = s['arah']
    harga = format_rp(s['harga'])

    # Header
    if level == 'KONFIRMASI' and 'BELI' in arah:
        header = f"✅ KONFIRMASI BELI — {symbol}"
    elif level == 'KONFIRMASI' and 'JUAL' in arah:
        header = f"🚨 KONFIRMASI JUAL — {symbol}"
    elif level == 'SIAGA' and 'BELI' in arah:
        header = f"⚡ SIAGA BELI — {symbol}"
    elif level == 'SIAGA' and 'JUAL' in arah:
        header = f"⚠️ SIAGA JUAL — {symbol}"
    elif level == 'CROSS':
        ikon = "🔔" if 'GOLDEN' in arah else "🔕"
        header = f"{ikon} {arah} — {symbol}"
    else:
        header = f"🪝 PRA-GOLDEN — {symbol}"

    alasan_str = format_alasan(s)

    lines = [
        f"*{header}*",
        f"Harga : {harga}",
        "",
        "*Alasan:*",
        alasan_str,
    ]

    if s.get('sl') and s.get('tp'):
        lines += [
            "",
            f"SL : {format_rp(s['sl'])}",
            f"TP : {format_rp(s['tp'])}",
        ]

    return "\n".join(lines)


# ============================================================
# LAPORAN PORTOFOLIO
# ============================================================
async def kirim_laporan(bot: Bot, exchange, usd_idr: float):
    total_modal = 0.0
    total_nilai = 0.0
    baris = []

    for sym, p in PORTFOLIO.items():
        try:
            ticker = exchange.fetch_ticker(sym)
            harga_kini = ticker['last'] * usd_idr
            harga_beli = p['buy_price_idr']
            modal      = harga_beli * p['amount']
            nilai      = harga_kini * p['amount']
            pnl_val    = nilai - modal
            pnl_pct    = (pnl_val / modal * 100) if modal else 0
            ikon       = "🟢" if pnl_pct >= 0 else "🔴"
            
            total_modal += modal
            total_nilai += nilai
            
            baris.append(
                f"{ikon} *{sym}*\n"
                f"    Beli: {format_rp(harga_beli)}\n"
                f"    Skrg: {format_rp(harga_kini)}\n"
                f"    P/L : {pnl_pct:+.2f}%  ({format_rp(pnl_val)})"
            )
        except Exception as e:
            baris.append(f"⚠️ {sym} — gagal ({e})")

    total_pnl     = total_nilai - total_modal
    total_pnl_pct = (total_pnl / total_modal * 100) if total_modal else 0
    ikon_total    = "🟢" if total_pnl_pct >= 0 else "🔴"

    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    pesan = (
        f"📊 *PORTOFOLIO — {now_wib.strftime('%H:%M WIB')}*\n\n"
        + "\n\n".join(baris)
        + f"\n\n{'─'*20}\n"
        f"  Total Beli: {format_rp(total_modal)}\n"
        f"  Total Skrg: {format_rp(total_nilai)}\n"
        f"{ikon_total} Total P/L : {total_pnl_pct:+.2f}% ({format_rp(total_pnl)})"
    )
    await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')


# ============================================================
# MAIN LOOP
# ============================================================
async def main():
    exchange = ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'timeout': 30_000,
    })
    exchange.load_markets()

    bot      = Bot(token=TOKEN)
    usd_idr  = get_usd_idr()
    now_wib  = datetime.now(timezone.utc) + timedelta(hours=7)

    # Scan semua aset
    for symbol in ASSET_LIST:
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=60)
            df   = pd.DataFrame(bars, columns=['timestamp','open','high','low','close','volume'])
            df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)

            hasil = analisa(df, usd_idr)
            if hasil:
                pesan = format_sinyal(symbol, hasil)
                await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')

        except Exception as e:
            print(f"Error {symbol}: {e}")

        await asyncio.sleep(1.5)

    # Laporan portofolio 3× sehari
    if now_wib.hour in JAM_LAPORAN and now_wib.minute < 30:
        await kirim_laporan(bot, exchange, usd_idr)


if __name__ == '__main__':
    asyncio.run(main())
