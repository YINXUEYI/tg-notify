import os
import requests

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TEXT = os.environ.get("TELEGRAM_TEXT", "云端自动通知：一切正常✅")

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = {"chat_id": CHAT_ID, "text": TEXT}

r = requests.post(url, data=data, timeout=20)
r.raise_for_status()
print("✅ Telegram 已发送：", TEXT)
