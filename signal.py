import csv, datetime as dt, json, os
import requests

SYMBOL = "XAU/USD"
TD_URL = "https://api.twelvedata.com/time_series"
RR = 1.5
IMPULSE_MULT = 1.5
SL_BUFFER = 0.5
CHART_BARS = 100
LOG = "signals.csv"
OUT = "signals.json"
COLS = ["time", "side", "entry_low", "entry_high", "sl", "tp"]


def fetch(n=120):
    key = os.environ.get("8c868d19f62d468592b6bd14f6df85c5", "")
    if not key:
        raise RuntimeError("secret TWELVEDATA_KEY belum diset")
    r = requests.get(TD_URL, params={
        "symbol": SYMBOL, "interval": "5min", "outputsize": n,
        "timezone": "UTC", "apikey": key,
    }, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} dari Twelve Data")
    data = r.json()
    if data.get("status") != "ok" or "values" not in data:
        msg = str(data.get("message", "respons tidak valid")).replace(key, "***")
        raise RuntimeError(msg)
    return [(v["datetime"], float(v["open"]), float(v["high"]),
             float(v["low"]), float(v["close"])) for v in reversed(data["values"])]


def to_ts(s):
    t = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return int(t.replace(tzinfo=dt.timezone.utc).timestamp())


def atr(rows, n=14):
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i][2], rows[i][3], rows[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def find_zones(rows, side, a):
    zones = []
    for i in range(1, len(rows) - 1):  # candle terakhir masih berjalan, tidak jadi impuls
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
            touched = any(r[3] <= z["high"] for r in rows[i + 1:])
        else:
            touched = any(r[2] >= z["low"] for r in rows[i + 1:])
        if not touched:
            zones.append(z)
    return zones


def make_plan(side, z, price, a):
    if z is None:
        return None
    lo, hi = z["low"], z["high"]
    mid = (lo + hi) / 2
    if side == "buy":
        sl = lo - SL_BUFFER * a
        tp = mid + (mid - sl) * RR
        waiting = "MENUNGGU harga turun ke area"
    else:
        sl = hi + SL_BUFFER * a
        tp = mid - (sl - mid) * RR
        waiting = "MENUNGGU harga naik ke area"
    inside = lo <= price <= hi
    dist = 0.0 if inside else min(abs(price - lo), abs(price - hi))
    return {
        "entry_low": round(lo, 2), "entry_high": round(hi, 2),
        "sl": round(sl, 2), "tp": round(tp, 2),
        "status": "AKTIF, harga di area" if inside else waiting,
        "dist": round(dist, 2),
    }


def build_plans(rows, price, a):
    demand = [z for z in find_zones(rows, "buy", a) if z["low"] <= price]
    supply = [z for z in find_zones(rows, "sell", a) if z["high"] >= price]
    buy = max(demand, key=lambda z: z["high"], default=None)
    sell = min(supply, key=lambda z: z["low"], default=None)
    return {"buy": make_plan("buy", buy, price, a),
            "sell": make_plan("sell", sell, price, a)}


def ensure_log():
    if not os.path.exists(LOG):
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLS)


def read_log():
    with open(LOG, encoding="utf-8") as f:
        return [dict(zip(COLS, r)) for r in csv.reader(f) if r and r[0] != "time"]


def new_entries(plans, log, stamp):
    out = []
    for side in ("buy", "sell"):
        p = plans[side]
        if p is None:
            continue
        prev = next((r for r in reversed(log) if r.get("side") == side), None)
        if prev and prev.get("entry_low") == str(p["entry_low"]) \
                and prev.get("entry_high") == str(p["entry_high"]):
            continue
        out.append([stamp, side, p["entry_low"], p["entry_high"], p["sl"], p["tp"]])
    return out


def write_json(data):
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def set_error(stamp, msg):
    data = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["updated"] = stamp
    data["error"] = msg
    write_json(data)
    print(msg)


def main():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        rows = fetch()
        price = rows[-1][4]
        plans = build_plans(rows, price, atr(rows[:-1]))
        ensure_log()
        fresh = new_entries(plans, read_log(), stamp)
        if fresh:
            with open(LOG, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(fresh)
        log = read_log()
        write_json({
            "updated": stamp,
            "price": round(price, 2),
            "candles": [{"time": to_ts(t), "open": o, "high": h, "low": l, "close": c}
                        for t, o, h, l, c in rows[-CHART_BARS:]],
            "plans": plans,
            "log": log[-30:][::-1],
        })
        print("plans:", plans)
    except RuntimeError as e:
        set_error(stamp, str(e))
    except Exception as e:
        set_error(stamp, f"error: {type(e).__name__}")


if __name__ == "__main__":
    main()
