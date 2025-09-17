# top5_futures.py
import requests
from datetime import datetime

API = "https://api.gateio.ws/api/v4/futures/usdt/tickers"  # USDT本位永续
TIMEOUT = 15

def fetch_top5():
    r = requests.get(API, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    rows = []
    for t in data:
        pair = t.get("contract", "")
        try:
            chg = float(t.get("change_percentage", "0"))   # 24h涨跌幅 %
            last = float(t.get("last", "0"))
            vol  = float(t.get("volume_24h_base", "0"))    # 24h成交量(基币)
            funding = float(t.get("funding_rate", "0") or 0)
        except Exception:
            continue
        rows.append((chg, pair, last, vol, funding))
    rows.sort(reverse=True)
    return rows[:5]

def format_msg(rows):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Gate 合约(USDT永续) 24h 涨幅榜前5\n时间：{ts}\n"]
    for i, (chg, pair, last, vol, funding) in enumerate(rows, 1):
        lines.append(f"{i}. {pair}  涨幅: {chg:.2f}%  价格: {last}  24h量: {vol:.0f}  资金费率: {funding:.4%}")
    return "\n".join(lines)

if __name__ == "__main__":
    rows = fetch_top5()
    msg = format_msg(rows)
    print(msg)

    # 写入邮件正文文件
    with open("message.txt", "w", encoding="utf-8") as f:
        f.write(msg)

    # 发 Telegram（兼容 send_telegram.py 或 中文文件名“发送电报.py”）
    try:
        from send_telegram import send_message
    except Exception:
        try:
            import importlib
            send_message = importlib.import_module('发送电报').send_message
        except Exception as e:
            send_message = None
            print("未找到 Telegram 发送模块：", e)
    if send_message:
        try:
            send_message(msg)
        except Exception as e:
            print("Telegram 发送失败：", e)
