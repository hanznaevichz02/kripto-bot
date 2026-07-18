import asyncio
import ccxt
import pandas as pd
from telegram import Bot

# --- KONFIGURASI ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# Ambang Batas RSI
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20

# 20 Koin stabil pilihan (Binance USDT Pairs)
WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'LRC/USDT', 
    'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOT/USDT', 
    'LINK/USDT', 'UNI/USDT', 'LTC/USDT', 'TRX/USDT', 'ATOM/USDT', 
    'ALGO/USDT', 'BCH/USDT', 'GRT/USDT', 'FIL/USDT', 'DOGE/USDT', 
    'SUI/USDT', 'ARB/USDT', 'TON/USDT', 'INJ/USDT', 
    'NEAR/USDT', 'OP/USDT', 'AVAX/USDT'
]

def hitung_rsi(data, period=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    # Menghindari pembagian dengan nol
    rs = gain / loss.replace(0, 0.000000001)
    rsi = 100 - (100 / (1 + rs))
    
    # Jika hasil RSI kosong/NaN, kita anggap 50 (netral)
    return rsi.fillna(50)

async def cek_koin(exchange, coin, bot):
    try:
        # Mengambil data dari Binance
        bars = exchange.fetch_ohlcv(coin, timeframe='1h', limit=50)
        
        # Pengaman data
        if not bars or len(bars) < 25:
            return 

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['AvgVol'] = df['volume'].rolling(window=20).mean()
        df['RSI'] = hitung_rsi(df['close'])
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- LOGIKA SINYAL ---
        is_volume_ok = curr['volume'] > curr['AvgVol']
        is_golden_cross = (prev['EMA9'] <= prev['EMA21']) and (curr['EMA9'] > curr['EMA21'])
        is_dead_cross = (prev['EMA9'] >= prev['EMA21']) and (curr['EMA9'] < curr['EMA21'])
        
        # Logika Reversal RSI (Keluar dari zona jenuh)
        is_reversal_down = (prev['RSI'] > RSI_OVERBOUGHT) and (curr['RSI'] <= RSI_OVERBOUGHT)
        is_reversal_up = (prev['RSI'] < RSI_OVERSOLD) and (curr['RSI'] >= RSI_OVERSOLD)
        
        # --- URUTAN PRIORITAS NOTIFIKASI ---
        if is_golden_cross and is_volume_ok:
            pesan = f"🟢 *SINYAL BELI {coin}*\nHarga: {curr['close']:.4f} USDT\nRSI: {curr['RSI']:.2f}\nVolume: Bagus"
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif is_dead_cross:
            pesan = f"🔴 *SINYAL JUAL {coin}*\nHarga: {curr['close']:.4f} USDT\nStatus: Dead Cross"
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif is_reversal_down:
            pesan = f"⚠️ *REVERSAL {coin}*\nRSI turun dari {prev['RSI']:.2f} ke {curr['RSI']:.2f}.\nPotensi pembalikan harga ke bawah (Bearish)."
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
        elif is_reversal_up:
            pesan = f"✅ *REVERSAL {coin}*\nRSI naik dari {prev['RSI']:.2f} ke {curr['RSI']:.2f}.\nPotensi pembalikan harga ke atas (Bullish)."
            await bot.send_message(chat_id=CHAT_ID, text=pesan, parse_mode='Markdown')
            
    except Exception as e:
        print(f"Error pada {coin}: {e}")

async def main():
    exchange = ccxt.binance({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    
    print("Memulai scan koin sekali jalan...")
    
    for coin in WATCHLIST:
        await cek_koin(exchange, coin, bot)
        await asyncio.sleep(2) # Jeda agar tidak kena limit Binance
        
    print("Scan selesai.")

if __name__ == '__main__':
    asyncio.run(main())