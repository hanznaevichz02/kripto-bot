import json
import os

class PaperTrader:
    def __init__(self, file_path="paper_trading.json", initial_capital=1_000_000, target_pair="ETH-IDR"):
        self.file_path = file_path
        self.initial_capital = initial_capital
        self.target_pair = target_pair
        # Total potongan Fee + Pajak PMK 68 (Beli & Jual) = ~0.65%
        self.fee_tax_rate = 0.0065  
        self.data = self.load_data()

    def load_data(self):
        """Membaca status transaksi simulasi dari file JSON"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "status": "IDLE",            # 'IDLE' atau 'IN_POSITION'
            "balance": self.initial_capital,
            "buy_price": 0,
            "buy_time": "",
            "total_trades": 0,
            "wins": 0,
            "losses": 0
        }

    def save_data(self):
        """Menyimpan data simulasi ke file JSON"""
        with open(self.file_path, "w") as f:
            json.dump(self.data, f, indent=4)

    def process(self, pair_name, signal_type, current_price, current_time):
        """
        Memproses simulasi beli/jual KHUSUS untuk ETH-IDR.
        """
        # Filter: Hanya jalankan paper trading jika koinnya adalah ETH-IDR
        if pair_name != self.target_pair:
            return None

        msg = None

        # -------------------------------------------------------------
        # 1. CEK EXIT (Jika Sedang Memegang Posisi Virtual ETH)
        # -------------------------------------------------------------
        if self.data["status"] == "IN_POSITION":
            buy_p = self.data["buy_price"]
            price_change_pct = ((current_price - buy_p) / buy_p) * 100

            is_tp = price_change_pct >= 1.5              # Target TP +1.5%
            is_sl = price_change_pct <= -1.0             # Stop Loss -1.0%
            is_bear_signal = signal_type == "BEAR_SWEEP"  # Sinyal Whale Jual

            if is_tp or is_sl or is_bear_signal:
                gross_pct = price_change_pct / 100
                net_pct = gross_pct - self.fee_tax_rate
                
                pnl_rp = self.data["balance"] * net_pct
                self.data["balance"] += pnl_rp
                self.data["status"] = "IDLE"
                self.data["total_trades"] += 1

                if pnl_rp > 0:
                    self.data["wins"] += 1
                    status_title = "🟢 TAKE PROFIT (VIRTUAL)"
                else:
                    self.data["losses"] += 1
                    status_title = "🔴 EXIT / STOP LOSS (VIRTUAL)"

                reason = "Target TP (+1.5%)" if is_tp else ("Stop Loss (-1.0%)" if is_sl else "Sinyal BEAR_SWEEP")
                win_rate = (self.data["wins"] / self.data["total_trades"]) * 100

                msg = (
                    f"🧪 **[PAPER TRADING - {self.target_pair}]**\n"
                    f"**{status_title}**\n\n"
                    f"• **Alasan Exit:** {reason}\n"
                    f"• **Harga Beli:** Rp {buy_p:,.0f}\n"
                    f"• **Harga Jual:** Rp {current_price:,.0f}\n"
                    f"• **Hasil Bersih:** {net_pct*100:+.2f}% (Rp {pnl_rp:+,.0f})\n"
                    f"• **Saldo Sekarang:** Rp {self.data['balance']:,.0f}\n"
                    f"• **Win Rate:** {win_rate:.1f}% ({self.data['wins']}/{self.data['total_trades']} Trade)"
                )
                self.save_data()

        # -------------------------------------------------------------
        # 2. CEK ENTRY (Jika Posisi Sedang Kosong / IDLE)
        # -------------------------------------------------------------
        elif self.data["status"] == "IDLE":
            if signal_type == "BULL_SWEEP":
                self.data["status"] = "IN_POSITION"
                self.data["buy_price"] = current_price
                self.data["buy_time"] = str(current_time)

                msg = (
                    f"🧪 **[PAPER TRADING - {self.target_pair}]**\n"
                    f"**🟢 VIRTUAL BUY**\n\n"
                    f"• **Harga Entry:** Rp {current_price:,.0f}\n"
                    f"• **Modal Transaksi:** Rp {self.data['balance']:,.0f}\n"
                    f"• **Target TP (+1.5%):** Rp {current_price * 1.015:,.0f}\n"
                    f"• **Stop Loss (-1.0%):** Rp {current_price * 0.990:,.0f}"
                )
                self.save_data()

        return msg
