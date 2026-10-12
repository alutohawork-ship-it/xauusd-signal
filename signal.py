import csv, datetime as dt, json, os
import requests

SYMBOL = "XAU/USD"
TD_URL = "https://api.twelvedata.com/time_series"
RR = 1.5
IMPULSE_MULT = 1.5
SL_BUFFER = 0.5
LOOKBACK = 300
CHART_BARS = 150
FALLBACK_BARS = 60
LOG = "signals.csv"
OUT = "signals.json"
COLS = ["time", "side", "type", "entry_low", "entry_high", "sl", "tp"]


def now_str():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fetch(n=LOOKBACK):
    key = os.environ.get("8c868d19f62d468592b6bd14f6df85c5", "")
    if not key:
        raise RuntimeError("secret TWELVEDATA_KEY belum diset")
    r = requests.get(TD_URL, params={
        "symbol": SYMBOL, "interval": "5min", "outputsize": n,
        "timezone": "UTC", "apikey": key,
    }, timeout=20)
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
    for i in range(1, len(rows) - 1):  # candle terakhir masih berjalan, dilewati
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
        z["fresh"] = not touched
        zones.append(z)
    return zones


def nearest(zones, side, price):
    if side == "buy":
        cand = [z for z in zones if z["low"] <= price]
        key, pick = (lambda z: z["high"]), max
    else:
        cand = [z for z in zones if z["high"] >= price]
        key, pick = (lambda z: z["low"]), min
    fresh = [z for z in cand if z["fresh"]]
    if fresh:
        return pick(fresh, key=key), "zona fresh"
    if cand:
        return pick(cand, key=key), "zona sudah pernah disentuh"
    return None, None


def fallback(rows, side, a):
    recent = rows[-FALLBACK_BARS:]  # termasuk candle berjalan, jadi selalu mengapit harga
    if side == "buy":
        ref = min(r[3] for r in recent)
        return {"low": ref, "high": ref + 0.5 * a}, "perkiraan dari swing low"
    ref = max(r[2] for r in recent)
    return {"low": ref - 0.5 * a, "high": ref}, "perkiraan dari swing high"


def make_plan(side, zone, kind, price, a):
    lo, hi = zone["low"], zone["high"]
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
        "side": side,
        "type": kind,
        "entry_low": round(lo, 2), "entry_high": round(hi, 2),
        "sl": round(sl, 2), "tp": round(tp, 2),
        "status": ("AKTIF, harga di area" if inside else waiting) + " • " + kind,
        "dist": round(dist, 2),
    }


def build_plans(rows, price, a):
    plans = {}
    for side in ("buy", "sell"):
        zone, kind = nearest(find_zones(rows, side, a), side, price)
        if zone is None:
            zone, kind = fallback(rows, side, a)
        plans[side] = make_plan(side, zone, kind, price, a)
    return plans


def ensure_log():
    if not os.path.exists(LOG):
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLS)


def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8") as f:
        return [dict(zip(COLS, r)) for r in csv.reader(f) if r and r[0] != "time"]


def new_entries(plans, log, stamp):
    out = []
    for side in ("buy", "sell"):
        p = plans[side]
        prev = next((r for r in reversed(log) if r.get("side") == side), None)
        if prev and prev.get("entry_low") == str(p["entry_low"]) \
                and prev.get("entry_high") == str(p["entry_high"]):
            continue
        out.append([stamp, side, p["type"], p["entry_low"], p["entry_high"], p["sl"], p["tp"]])
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
    stamp = now_str()
    try:
        rows = fetch()
        price = rows[-1][4]
        plans = build_plans(rows, price, atr(rows[:-1]))
        ensure_log()
        fresh = new_entries(plans, read_log(), stamp)
        if fresh:
            with open(LOG, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(fresh)
        write_json({
            "updated": stamp,
            "price": round(price, 2),
            "candles": [{"time": to_ts(t), "open": o, "high": h, "low": l, "close": c}
                        for t, o, h, l, c in rows[-CHART_BARS:]],
            "plans": plans,
            "log": read_log()[-30:][::-1],
        })
        print("plans:", plans)
    except RuntimeError as e:
        set_error(stamp, str(e))
    except Exception as e:
        set_error(stamp, f"error: {type(e).__name__}")


if __name__ == "__main__":
    main()
