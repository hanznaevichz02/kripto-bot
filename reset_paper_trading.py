"""
========================================================
   KRIPTO BOT — RESET UTILITY (PAPER TRADING)
   Fungsi: Mengembalikan modal ke Rp 1.000.000 & 
           membersihkan posisi aktif / history.
========================================================
"""

import os
import json
import shutil
from datetime import datetime

INITIAL_CAPITAL = 1_000_000.0

# Initial State untuk masing-masing bot
DEFAULT_SMC_STATE = {
    "status": "IDLE",
    "balance": INITIAL_CAPITAL,
    "buy_price": 0,
    "buy_time": "",
    "tp": 0,
    "sl": 0,
    "total_trades": 0,
    "wins": 0,
    "losses": 0
}

DEFAULT_TECH_STATE = {
    "cash_idr": INITIAL_CAPITAL,
    "active_position": None,
    "history": [],
    "stats": {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl_idr": 0.0
    }
}

DEFAULT_HYBRID_STATE = {
    "cash_idr": INITIAL_CAPITAL,
    "active_position": None,
    "history": [],
    "stats": {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl_idr": 0.0
    }
}

FILES_CONFIG = {
    "1": ("paper_trading_smc.json", DEFAULT_SMC_STATE, "Bot SMC Standalone"),
    "2": ("paper_trading_tech.json", DEFAULT_TECH_STATE, "Bot Technical Pure"),
    "3": ("paper_trading_hybrid.json", DEFAULT_HYBRID_STATE, "Bot Hybrid (SMC + Tech)")
}

def backup_file(filename):
    if os.path.exists(filename):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{filename}.bak_{timestamp}"
        shutil.copy(filename, backup_name)
        print(f"  📦 Backup dibuat: {backup_name}")

def reset_file(filename, default_state, bot_name):
    backup_file(filename)
    with open(filename, 'w') as f:
        json.dump(default_state, f, indent=4)
    print(f"  ✅ {bot_name} ({filename}) berhasil di-reset ke Rp {INITIAL_CAPITAL:,.0f}!")

def main():
    print("=" * 50)
    print("      🔄 KRIPTO BOT RESET UTILITY")
    print("=" * 50)
    print("Pilih bot yang ingin di-reset:")
    print("  [1] Bot SMC Standalone")
    print("  [2] Bot Technical Pure")
    print("  [3] Bot Hybrid")
    print("  [4] RESET ALL (Semua Bot)")
    print("  [0] Batal")
    print("-" * 50)

    pilihan = input("Masukkan pilihan (0-4): ").strip()

    if pilihan in ["1", "2", "3"]:
        filename, default_state, bot_name = FILES_CONFIG[pilihan]
        reset_file(filename, default_state, bot_name)
    elif pilihan == "4":
        confirm = input("⚠️ Yakin ingin mereset SEMUA bot? (y/n): ").lower().strip()
        if confirm == 'y':
            for filename, default_state, bot_name in FILES_CONFIG.values():
                reset_file(filename, default_state, bot_name)
        else:
            print("❌ Batal melakukan reset total.")
    elif pilihan == "0":
        print("❌ Operasi dibatalkan.")
    else:
        print("⚠️ Pilihan tidak valid.")

if __name__ == "__main__":
    main()
