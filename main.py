async def main():
    exchange = ccxt.kucoin({'enableRateLimit': True})
    bot = Bot(token=TOKEN)
    usd_idr_rate = get_usd_to_idr()
    
    # Dapatkan jam WIB
    now_wib = datetime.utcnow() + timedelta(hours=7)
    
    # 1. JALANKAN ANALISA SINYAL (Selalu jalan setiap kali bot aktif)
    # Kita loop semua aset (kombinasi portfolio + watchlist)
    all_assets = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'] # Tambahkan koinmu di sini
    
    for symbol in all_assets:
        await cek_koin(exchange, symbol, bot, usd_idr_rate)
        await asyncio.sleep(1) # Delay biar tidak kena rate limit
    
    # 2. JALANKAN LAPORAN PORTOFOLIO (Hanya di jam tertentu)
    # Tambahkan menit < 15 supaya hanya terkirim sekali di jam tersebut 
    # (mengantisipasi jika cron-job jalan tiap 15 menit)
    if now_wib.hour in [9, 14, 20] and now_wib.minute < 15:
        await kirim_laporan_porto(bot, exchange, usd_idr_rate)
