import csv, datetime as dt, os
import requests

SYMBOL = "XAU/USD"
TD_URL = "https://api.twelvedata.com/time_series"
MIN_RR = 1.5
IMPULSE_MULT = 1.5
SL_BUFFER = 0.5
LOG = "signals.csv"


def fetch(n=60):
    r = requests.get(TD_URL, params={
        "symbol": SYMBOL, "interval": "5min", "outputsize": n + 1,
        "timezone": "UTC", "apikey": os.environ["8c868d19f62d468592b6bd14f6df85c5"],
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok" or "values" not in data:
        raise RuntimeError(data.get("message", "respons tidak dikenal"))
    rows = [(v["datetime"], float(v["open"]), float(v["high"]),
             float(v["low"]), float(v["close"])) for v in reversed(data["values"])]
    return rows[:-1]


def atr(rows, n=14):
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i][2], rows[i][3], rows[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def find_zones(rows, side, a):
    zones = []
    for i in range(1, len(rows) - 1):
        o, c = rows[i][1], rows[i][4]
        if side == "buy":
            impulsive = c > o and (c - o) >= IMPULSE_MULT * a
        else:
            impulsive = c < o and (o - c) >= IMPULSE_MULT * a
        if not impulsive:
            continue
        base = rows[i - 1]
        z = {"low": base[3], "high": base[2]}
        if side == "buy":
            touched = any(r[3] <= z["high"] for r in rows[i + 1:-1])
        else:
            touched = any(r[2] >= z["low"] for r in rows[i + 1:-1])
        if not touched:
            zones.append(z)
    return zones


def rule_signal(m5, price):
    a = atr(m5)
    for side in ("buy", "sell"):
        for z in reversed(find_zones(m5, side, a)):
            if not (z["low"] <= price <= z["high"]):
                continue
            lo, hi = z["low"], z["high"]
            mid = (lo + hi) / 2
            if side == "buy":
                sl = lo - SL_BUFFER * a
                tp = mid + (mid - sl) * MIN_RR
                action, name = "BUY", "demand"
            else:
                sl = hi + SL_BUFFER * a
                tp = mid - (sl - mid) * MIN_RR
                action, name = "SELL", "supply"
            return {"action": action, "entry_low": round(lo, 2), "entry_high": round(hi, 2),
                    "sl": round(sl, 2), "tp": round(tp, 2),
                    "reason": f"zona {name} M5 fresh, harga di area zona"}
    return {"action": "NO_TRADE", "reason": "tidak ada zona fresh di harga sekarang"}


def valid(s):
    if s["action"] == "NO_TRADE":
        return True
    lo, hi, sl, tp = s["entry_low"], s["entry_high"], s["sl"], s["tp"]
    mid = (lo + hi) / 2
    ok = sl < lo <= hi < tp if s["action"] == "BUY" else tp < lo <= hi < sl
    rr = abs(tp - mid) / abs(mid - sl)
    return ok and rr >= MIN_RR - 0.01


def is_duplicate(s):
    if not os.path.exists(LOG):
        return False
    with open(LOG, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return False
    last = rows[-1]
    return (last[1] == s["action"] and last[2] == str(s["entry_low"])
            and last[3] == str(s["entry_high"]))


def notify(text):
    try:
        requests.post(f"https://api.telegram.org/bot{os.environ['8926741783:AAG1nHi1FG2HoTl7D3d7r8EcIYnAlUg1yuM']}/sendMessage",
                      json={"chat_id": os.environ["524505436"], "text": text}, timeout=10)
    except requests.RequestException as e:
        print(f"notifikasi gagal: {e}")


def main():
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        m5 = fetch()
        price = m5[-1][4]
        s = rule_signal(m5, price)
        if not valid(s):
            s = {"action": "NO_TRADE", "reason": "RR kurang dari 1:1,5"}
    except Exception as e:
        s = {"action": "NO_TRADE", "reason": f"error: {e}"}

    dup = s["action"] != "NO_TRADE" and is_duplicate(s)
    row = [now, s["action"], s.get("entry_low"), s.get("entry_high"),
           s.get("sl"), s.get("tp"), s["reason"]]
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

    if s["action"] != "NO_TRADE" and not dup:
        notify(f"XAUUSD {s['action']}\nArea: {s['entry_low']} - {s['entry_high']}\n"
               f"SL: {s['sl']}\nTP: {s['tp']}\n{s['reason']}")
    print(row)


if __name__ == "__main__":
    main()
