import logging
import httpx
import datetime
import json
import os
import asyncio
from telegram.ext import ApplicationBuilder
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ==================== CẤU HÌNH ====================
TOKEN = "8723751974:AAFBnzKUi0n-wgJBaCqpGi2VT5cme8teVZ4"
CHAT_ID = "7138785294"
FIREBASE_URL = "https://tuan-anh-dz-default-rtdb.asia-southeast1.firebasedatabase.app/.json"

CHECK_INTERVAL = 1       # Kiểm tra Firebase mỗi 1 giây để báo bill ngay lập tức
RECONNECT_INTERVAL = 5   # Nếu mất kết nối, tự động thử lại sau đúng 5 giây

SENT_FILE = "sent_transactions.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==================== TẠO SERVER MINI ĐỂ RAILWAY KHÔNG KHỞI ĐỘNG LẠI ====================
class HealthCheckServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot đang chạy ổn định!".encode("utf-8"))

def start_health_server():
    # Railway sẽ cấp một cổng ngẫu nhiên qua biến PORT, nếu không có sẽ dùng 8080
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckServer)
    logging.info(f"🌐 Đã mở cổng Healthcheck tại port: {port} cho Railway")
    server.serve_forever()

# ==================== TẢI & LƯU LỊCH SỬ GỬI BILL ====================
def load_sent():
    if not os.path.exists(SENT_FILE):
        return []
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_sent(data):
    try:
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Lỗi ghi file lịch sử: {e}")

# ==================== HÀM TRÁNH LỖI CÚ PHÁP MARKDOWNV2 ====================
def escape_markdown_v2(text):
    """Tự động chèn dấu gạch chéo ngược trước các ký tự đặc biệt để tránh lỗi Telegram"""
    escape_chars = r"_*[]()~`>#+-=|{}.!\\"
    return "".join(f"\\{char}" if char in escape_chars else char for char in str(text))

# ==================== ĐỊNH DẠNG VÀ GỬI BILL ====================
async def send_bill(bot, tx):
    amount = int(tx.get("amount", 0))
    try:
        time_str = datetime.datetime.fromtimestamp(
            tx.get("timestamp", 0) / 1000
        ).strftime("%H:%M:%S %d/%m/%Y")
    except Exception:
        time_str = "Không xác định"
        
    amount_text = f"{amount:,}".replace(",", ".")
    
    # Xử lý chuỗi an toàn chống lỗi hiển thị Telegram MarkdownV2
    username = escape_markdown_v2(tx.get('username', 'N/A'))
    tx_id = escape_markdown_v2(tx.get('transactionId', 'N/A'))
    amount_text = escape_markdown_v2(amount_text)
    time_str = escape_markdown_v2(time_str)
    
    # Giao diện tiếng Việt thiết kế dạng Hóa Đơn Premium cực đẹp
    message = (
        f"👑 *HÓA ĐƠN THANH TOÁN THÀNH CÔNG*\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"👤 *Khách hàng:* `{username}`\n"
        f"📦 *Sản phẩm:* _1 Key Tools HSD Gói 1 Ngày_\n"
        f"💰 *Tổng tiền:* 🔥 `{amount_text}đ`\n"
        f"📝 *Mã giao dịch:* `{tx_id}`\n"
        f"⚙️ *Trạng thái:* ✅  *ĐÃ PHÊ DUYỆT*\n"
        f"🕒 *Thời gian:* `{time_str}`\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"✨ _Cảm ơn bạn đã lựa chọn dịch vụ của chúng tôi_"
    )
    
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logging.error(f"Lỗi gửi tin nhắn Telegram: {e}")

# ==================== THEO DÕI FIREBASE LIÊN TỤC ====================
async def monitor(bot):
    sent_ids = load_sent()
    logging.info("🚀 Hệ thống bắt đầu quét giao dịch thời gian thực...")
    
    # Sử dụng httpx.AsyncClient để thực hiện request bất đồng bộ hoàn toàn, tránh treo luồng
    async with httpx.AsyncClient() as client:
        while True:
            try:    
                response = await client.get(FIREBASE_URL, timeout=10.0)    
                data = response.json()    

                if not isinstance(data, dict):    
                    await asyncio.sleep(CHECK_INTERVAL)    
                    continue    

                deposit_requests = data.get("deposit_requests", {})    

                for key, tx in deposit_requests.items():    
                    if not isinstance(tx, dict):
                        continue

                    transaction_id = tx.get("transactionId", "")    
                    status = str(tx.get("status", "")).lower()    

                    if not transaction_id:    
                        continue    

                    if status != "approved":    
                        continue    

                    if transaction_id in sent_ids:    
                        continue    

                    # Tiến hành gửi thông báo tức thì
                    await send_bill(bot, tx)    

                    # Lưu lịch sử cục bộ
                    sent_ids.append(transaction_id)    
                    save_sent(sent_ids)    

                    logging.info(f"✅ Đã gửi bill thành công cho GD: {transaction_id}")    

                # Nghỉ 1 giây trước khi quét chu kỳ tiếp theo
                await asyncio.sleep(CHECK_INTERVAL)

            except (httpx.RequestError, Exception) as e:    
                # Khi mất kết nối mạng hoặc Firebase lỗi, code tự động nhảy vào đây
                logging.error(f"❌ Lỗi kết nối Firebase hoặc hệ thống: {e}")
                logging.info(f"🔄 Đang tự động kết nối lại sau {RECONNECT_INTERVAL} giây...")
                
                # Chờ đúng 5 giây trước khi thực hiện lại vòng lặp quét dữ liệu mới
                await asyncio.sleep(RECONNECT_INTERVAL)  

# ==================== KHỞI ĐỘNG HỆ THỐNG ====================
async def post_init(app):
    asyncio.create_task(
        monitor(app.bot)
    )

if __name__ == "__main__":
    # Khởi chạy cổng phụ chạy ngầm đáp ứng điều kiện sống của Railway
    threading.Thread(target=start_health_server, daemon=True).start()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )
    print("=========================================")
    print("🔔 BOT TELEGRAM ĐANG THEO DÕI GIAO DỊCH...")
    print("=========================================")
    app.run_polling()
