# -*- coding: utf-8 -*-
"""
Scan Gate USDT perpetual futures to find short candidates
focused on NEW/ALT coins near top reversal, then send a JSON card
to Telegram + Email ONLY when triggers fire.

Env secrets required (GitHub Actions / local env):
- TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO
"""
import os
import time
import json
import ssl
import smtplib
import datetime
from email.mime.text import MIMEText
from email.header import Header

import requests

# -------------------- HTTP Session --------------------
GATE = "https://api.gateio.ws"
HEADERS = {"Accept": "application/json"}
session = requests.Session()
session.headers.update(HEADERS)

# ================== 可调参数（策略） ==================
TOP_N_BY_CHANGE     = 60           # 先按24h涨幅挑前N个缩小范围
MIN_24H_VOL_USDT    = 0            # ✅ 体量无所谓：不再过滤体量
CANDLES_INTERVAL    = "5m"
CANDLES_LIMIT       = 288          # ≈24h 的 5m K 数

# “新币”判定：首根K线距今 ≤ 14天（可自行调整/放宽）
NEW_COIN_MAX_DAYS   = 14

# 观察池（满足其一即可进入）
OBS_ZF24_PCT        = 50.0         # ✅ 24h 涨幅 ≥ 50%
OBS_ACCEL_EMA25     = 1.25         # last / EMA25 ≥ 1.25（保留作为加速备选）

# 顶拐信号（任意命中一个即可触发推送）
WICK_RATIO_MIN      = 1.7          # 上影比 ≥ 1.7
DROP_5M_PCT_MAX     = -2.5         # 5m 跌幅 ≤ -2.5%
DROP_15M_PCT_MAX    = -5.0         # 15m 跌幅 ≤ -5%
DIST_TO_HI24_MAX    = 1.5          # 距24h高点 ≤ 1.5%

# 每轮最多提醒（防刷屏）
MAX_ALERTS_PER_RUN  = 5

# 白名单（只看这些；空=全市场）
WHITELIST = set()

# 黑名单：不监控的主流币（保留唯一一份）
BLACKLIST = {
    "BTC_USDT", "ETH_USDT", "BNB_USDT", "SOL_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "TRX_USDT", "TON_USDT", "DOT_USDT",
    # 如需补充黑名单： "LTC_USDT",
}
# =====================================================

# -------------------- Gate API --------------------
def _get(url, params=None, timeout=15):
    r = session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def get_futures_tickers():
    """USDT本位合约ticker列表"""
    return _get(f"{GATE}/api/v4/futures/usdt/tickers")

def get_candles(contract: str, interval=CANDLES_INTERVAL, limit=CANDLES_LIMIT):
    """获取K线（按时间升序返回）"""
    data = _get(
        f"{GATE}/api/v4/futures/usdt/candlesticks",
        params={"contract": contract, "interval": interval, "limit": limit},
    )
    try:
        data = sorted(data, key=lambda x: int(x[0]))  # 时间升序
    except Exception:
        pass
    return data

# -------------------- 指标/工具 --------------------
def parse_ohlcv(c):
    """
    Gate futures常见两种顺序：
      A: [t, vol, close, high, low, open]
      B: [t, open, high, low, close, vol]
    尝试A失败则回退B
    """
    try:
        o = float(c[5]); h = float(c[3]); l = float(c[4]); cl = float(c[2]); v = float(c[1])
        return o, h, l, cl, v, int(c[0])
    except Exception:
        o = float(c[1]); h = float(c[2]); l = float(c[3]); cl = float(c[4]); v = float(c[5])
        return o, h, l, cl, v, int(c[0])

def ema(vals, period):
    if not vals:
        return []
    k = 2.0 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def pct(a, b):
    try:
        return (a / b - 1.0) * 100.0
    except Exception:
        return 0.0

# -------------------- 推送封装 --------------------
def build_card_base(symbol, last, idx, mark, chg24, vol24,
                    age_days, chg_5m, chg_15m, hi24, lo24,
                    wick_ratio, dist_hi24, triggers, conf):
    now_utc = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    card = {
        "schema": "gate.short_candidate.v1",
        "generated_at_utc": now_utc,
        "symbol": symbol,
        "market": "futures",
        "exchange": "gate",
        "age_days": age_days,
        "price": {
            "last": last, "index": idx, "mark": mark,
            "high24h": hi24, "low24h": lo24
        },
        "momentum": {
            "chg_5m_pct": chg_5m, "chg_15m_pct": chg_15m, "chg_24h_pct": chg24
        },
        "volume": {"vol_24h_usdt": vol24},
        "derivs": {
            "funding_rate_pct": None, "funding_trend": None,
            "open_interest_usdt": None, "oi_change_30m_pct": None, "basis_pct": None
        },
        "ta": {
            "rsi_14": None, "ema7_vs_ema25": None,
            "wick_ratio": round(wick_ratio, 2) if wick_ratio is not None else None,
            "dist_to_24h_high_pct": round(dist_hi24, 2) if dist_hi24 is not None else None,
            "new_24h_high": True if dist_hi24 is not None and abs(dist_hi24) < 0.05 else False
        },
        "micro": {"spread_bps": None, "depth_imbalance_pct": None},
        "signals": triggers,
        "confidence_0to1": round(conf, 2),
        "links": {
            "trade": f"https://www.gate.io/zh/futures_trade/USDT/{symbol}",
            "kline": f"https://www.gate.io/zh/futures_market/{symbol}"
        },
        "raw_refs": {
            "tickers": "/api/v4/futures/usdt/tickers",
            "candles": f"/api/v4/futures/usdt/candlesticks?contract={symbol}&interval={CANDLES_INTERVAL}&limit={CANDLES_LIMIT}",
            "funding": f"/api/v4/futures/usdt/funding_rate?contract={symbol}",
            "contract": f"/api/v4/futures/usdt/contracts/{symbol}"
        }
    }
    return card

def send_telegram_and_email(header, payload_json):
    # Telegram
    TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID")
    if TG_TOKEN and TG_CHAT:
        tg_url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        text = f"{header}\n\nJSON👇\n{payload_json}"
        r = requests.post(tg_url, data={"chat_id": str(TG_CHAT), "text": text}, timeout=20)
        r.raise_for_status()
        print("Telegram 发送成功。")
    else:
        print("[warn] 未设置 TELEGRAM_*，跳过TG。")

    # Email
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASS")
    to   = os.environ.get("MAIL_TO")
    if host and user and pwd and to:
        msg = MIMEText(f"{header}\n\n{payload_json}", "plain", "utf-8")
        msg["Subject"] = Header("ShortCandidate (顶拐信号)", "utf-8")
        msg["From"] = user
        msg["To"] = to
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as smtp:
            smtp.login(user, pwd)
            smtp.sendmail(user, [to], msg.as_string())
        print("Email 发送成功。")
    else:
        print("[warn] 未设置 SMTP_* / MAIL_TO，跳过邮件。")

# -------------------- 主流程（单次扫描） --------------------
def main():
    tickers = get_futures_tickers()

    # Ⅰ. 预筛：黑/白名单 + 涨幅排序（不看体量）
    pool = []
    for t in tickers:
        try:
            sym   = t["contract"]
            last  = float(t.get("last", "0") or 0)
            idx   = float(t.get("index_price", "0") or 0) if "index_price" in t else None
            mark  = float(t.get("mark_price", "0") or 0) if "mark_price" in t else None
            chg24 = float(str(t.get("change_percentage", "0")).replace("%", ""))
            vol24 = float(t.get("volume_24h_quote", t.get("volume_24h", 0)) or 0)
            hi24  = float(t.get("high_24h", 0) or 0)
            lo24  = float(t.get("low_24h", 0) or 0)
        except Exception:
            continue

        if sym in BLACKLIST:
            continue
        if WHITELIST and sym not in WHITELIST:
            continue

        pool.append((sym, last, idx, mark, chg24, vol24, hi24, lo24))

    # 只保留涨幅靠前的
    pool.sort(key=lambda x: x[4], reverse=True)
    pool = pool[:TOP_N_BY_CHANGE]
    print(f"[info] 预筛后进入观察的合约数: {len(pool)}")

    alerts = 0

    # Ⅱ. 逐个深入：新币 + 观察池 + 顶拐信号（任一命中即推）
    for (sym, last, idx, mark, chg24, vol24, hi24, lo24) in pool:
        if alerts >= MAX_ALERTS_PER_RUN:
            break
        try:
            candles = get_candles(sym)
            if len(candles) < 50:
                continue

            # 新币年龄（首根K线时间）
            first_ts = int(candles[0][0])
            age_days = (int(time.time()) - first_ts) / 86400.0
            if age_days > NEW_COIN_MAX_DAYS:
                continue  # 只要“新”的

            # OHLCV arrays
            opens, highs, lows, closes, vols = [], [], [], [], []
            for c in candles:
                o, h, l, cl, v, _ = parse_ohlcv(c)
                opens.append(o); highs.append(h); lows.append(l); closes.append(cl); vols.append(v)

            # 观察池规则：涨幅 or 加速（任意满足即可）
            ema25 = ema(closes, 25)
            accel_ok = False
            if ema25 and ema25[-1] > 0:
                if (last / ema25[-1]) >= OBS_ACCEL_EMA25:
                    accel_ok = True
            if not (chg24 >= OBS_ZF24_PCT or accel_ok):
                continue

            # 顶拐信号（任意一个满足就算命中）
            chg_5m  = pct(closes[-1], closes[-2]) if len(closes) >= 2 else 0.0
            chg_15m = pct(closes[-1], closes[-4]) if len(closes) >= 4 else 0.0

            # 最近一根上影比
            o, h, l, cl, _, _ = parse_ohlcv(candles[-1])
            body = abs(cl - o)
            upper = max(0.0, h - max(cl, o))
            wick_ratio = upper / (body if body != 0 else 1e-9)

            # 距24h高点
            hi24_use = hi24 or (max(highs[-288:]) if len(highs) >= 10 else max(highs))
            dist_hi24 = pct(last, hi24_use)

            # 构建信号（OR 逻辑）
            triggers = []
            cond_wick = (wick_ratio >= WICK_RATIO_MIN)
            cond_d5   = (chg_5m  <= DROP_5M_PCT_MAX)
            cond_d15  = (chg_15m <= DROP_15M_PCT_MAX)
            cond_near = (abs(dist_hi24) <= DIST_TO_HI24_MAX)

            if cond_wick: triggers.append("wick_exhaustion")
            if cond_d5:   triggers.append("drop_5m")
            if cond_d15:  triggers.append("drop_15m")
            if cond_near: triggers.append("near_24h_high")

            if not triggers:
                continue  # ✅ 没命中就不发

            # 置信度：按命中数量简单累加，供你二次判断参考
            conf = 0.4 + 0.15 * len(triggers)   # 命中越多，越接近1
            conf = min(1.0, conf)

            # 组卡发送
            card = build_card_base(
                symbol=sym, last=last, idx=idx, mark=mark, chg24=chg24, vol24=vol24,
                age_days=round(age_days, 1), chg_5m=round(chg_5m, 2), chg_15m=round(chg_15m, 2),
                hi24=hi24_use, lo24=lo24,
                wick_ratio=wick_ratio, dist_hi24=dist_hi24,
                triggers=triggers, conf=conf
            )
            header = f"📉 做空候选: {sym}  （新币{age_days:.1f}天 / 24h涨幅{chg24:.1f}%）"
            payload = json.dumps(card, ensure_ascii=False, separators=(",", ":"))

            send_telegram_and_email(header, payload)
            alerts += 1
            time.sleep(1.0)

        except Exception as e:
            # 打印完整堆栈，方便定位
            import traceback
            print(f"[warn] {sym} 处理异常: {e}")
            traceback.print_exc()

    print(f"[done] 本轮触发 {alerts} 条。仅在命中条件时推送。")

# -------------------- 入口 --------------------
if __name__ == "__main__":
    # 单次扫描；循环由定时任务/Actions 负责调度
    main()
