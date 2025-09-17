# strategy.py
import os, sys, time, math, statistics as stats
import requests

# ========== 可自定义参数 ==========
PAIRS = ["HIFI_USDT", "PEPE_USDT", "SATS_USDT"]   # 币对列表（后面你告诉我要盯哪些）
INTERVAL = "5m"                                    # K线周期
LIMIT = 30                                         # 拉取根数
PUMP_PCT = 0.05                                    # 快速拉升阈值：5%
VOL_WEAK_RATIO = 0.9                               # 量能弱：当前成交量 < 过去中位量的 0.9
# =================================

BASE = "https://api.gateio.ws/api/v4"

def fetch_klines(pair, interval=INTERVAL, limit=LIMIT):
    url = f"{BASE}/spot/candlesticks"
    # Gate 的返回按时间倒序：[t, vol, close, high, low, open]
    r = requests.get(url, params={"currency_pair": pair, "interval": interval, "limit": limit}, timeout=15)
    r.raise_for_status()
    data = r.json()
    # 防御：确保是按时间从新到旧
    data.sort(key=lambda x: int(x[0]), reverse=True)
    return data

def pct(a, b):
    if b == 0: return 0.0
    return (a - b) / b

def median_volume(kl):
    vols = [float(k[1]) for k in kl[1:11]]  # 过去10根（排除当前）
    return stats.median(vols) if vols else 0.0

def check_pair(pair):
    try:
        kl = fetch_klines(pair)
        # 最新两根
        k0, k1 = kl[0], kl[1]
        close0 = float(k0[2]); close1 = float(k1[2])
        high1  = float(k1[3])
        vol0   = float(k0[1])
        medv   = median_volume(kl)

        fast_pump = pct(close0, close1) >= PUMP_PCT
        vol_weak  = (medv > 0) and (vol0 < medv * VOL_WEAK_RATIO)
        double_top_hint = (close0 < high1) and fast_pump  # 冲高后收不回前高

        if (fast_pump and vol_weak) or double_top_hint:
            # 构造提醒文本
            msg = (
                f"[Gate 5m] {pair} 可能出现做空拐点\n"
                f"- 最新收盘: {close0:.6f}\n"
                f"- 前一收盘: {close1:.6f}（涨幅 {pct(close0, close1)*100:.2f}%）\n"
                f"- 当前量/中位量: {vol0:.2f} / {medv:.2f}\n"
                f"- 条件: "
                f"{'快速拉升 ' if fast_pump else ''}"
                f"{'量能偏弱 ' if vol_weak else ''}"
                f"{'二次冲高不破前高' if double_top_hint else ''}"
            )
            return msg
    except Exception as e:
        return f"[错误] {pair}: {e}"
    return None

def main():
    hits = []
    for p in PAIRS:
        m = check_pair(p)
        if m: hits.append(m)
        time.sleep(0.3)  # 轻微限速

    if hits:
        # 打印到 stdout，供 Actions 后续步骤读取
        print("\n\n".join(hits))
        # 同时写入文件（可选）
        with open("message.txt", "w", encoding="utf-8") as f:
            f.write("\n\n".join(hits))
        # 用退出码 0 表示正常
        sys.exit(0)
    else:
        print("no-signal")
        # 用特殊退出码 0 也行，后续步骤判断内容再决定是否发送
        sys.exit(0)

if __name__ == "__main__":
    main()
