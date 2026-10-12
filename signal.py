import datetime as dt, json, os
import requests

SYMBOL = "XAU/USD"
TD_URL = "https://api.twelvedata.com/time_series"
LOOKBACK = 300
OUT = "signals.json"

MODES = {
    "scalping": {"label": "Scalping", "interval": "5min", "impulse": 1.5,
                 "rr": 1.5, "buffer": 0.5, "fallback": 60, "chart": 150},
    "intraday": {"label": "Intraday", "interval": "15min", "impulse": 1.5,
                 "rr": 2.0, "buffer": 0.5, "fallback": 40, "chart": 150},
    "swing": {"label": "Swing", "interval": "4h", "impulse": 1.5,
              "rr": 2.5, "buffer": 0.5, "fallback": 30, "chart": 150},
}


def now_str():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fetch(interval):
    key = os.environ.get("8c868d19f62d468592b6bd14f6df85c5", "")
    if not key:
        raise RuntimeError("secret TWELVEDATA_KEY belum diset")
    r = requests.get(TD_URL, params={
        "symbol": SYMBOL, "interval": interval, "outputsize": LOOKBACK,
        "timezone": "UTC", "apikey": key,
    }, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} ({interval})")
    data = r.json()
    if data.get("status") != "ok" or "values" not in data:
        msg = str(data.get("message", "respons tidak valid")).replace(key, "***")
        raise RuntimeError(f"{interval}: {msg}")
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


def find_zones(rows, side, a, mult):
    zones = []
    for i in range(1, len(rows) - 1):
        o, c = rows[i][1], rows[i][4]
        if side == "buy":
            impulsive = c > o and (c - o) >= mult * a
        else:
            impulsive = c < o and (o - c) >= mult * a
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


def fallback(rows, side, a, n):
    recent = rows[-n:]
    if side == "buy":
        ref = min(r[3] for r in recent)
        return {"low": ref, "high": ref + 0.5 * a}, "perkiraan dari swing low"
    ref = max(r[2] for r in recent)
    return {"low": ref - 0.5 * a, "high": ref}, "perkiraan dari swing high"


def make_plan(side, zone, kind, price, a, cfg):
    lo, hi = zone["low"], zone["high"]
    mid = (lo + hi) / 2
    buf, rr = cfg["buffer"] * a, cfg["rr"]
    if side == "buy":
        sl = lo - buf
        tp = mid + (mid - sl) * rr
        wait = "MENUNGGU harga turun ke area"
    else:
        sl = hi + buf
        tp = mid - (sl - mid) * rr
        wait = "MENUNGGU harga naik ke area"
    inside = lo <= price <= hi
    dist = 0.0 if inside else min(abs(price - lo), abs(price - hi))
    return {
        "side": side, "type": kind,
        "entry_low": round(lo, 2), "entry_high": round(hi, 2),
        "sl": round(sl, 2), "tp": round(tp, 2),
        "status": ("AKTIF, harga di area" if inside else wait) + " • " + kind,
        "dist": round(dist, 2),
    }


def build_plans(rows, price, a, cfg):
    plans = {}
    for side in ("buy", "sell"):
        zones = find_zones(rows, side, a, cfg["impulse"])
        zone, kind = nearest(zones, side, price)
        if zone is None:
            zone, kind = fallback(rows, side, a, cfg["fallback"])
        plans[side] = make_plan(side, zone, kind, price, a, cfg)
    return plans


def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    stamp = now_str()
    prev = load_previous().get("modes", {})
    modes, errors = {}, {}
    for name, cfg in MODES.items():
        try:
            rows = fetch(cfg["interval"])
            price = rows[-1][4]
            modes[name] = {
                "label": cfg["label"],
                "updated": stamp,
                "price": round(price, 2),
                "candles": [{"time": to_ts(t), "open": o, "high": h, "low": l, "close": c}
                            for t, o, h, l, c in rows[-cfg["chart"]:]],
                "plans": build_plans(rows, price, atr(rows[:-1]), cfg),
            }
        except RuntimeError as e:
            errors[name] = str(e)
        except Exception as e:
            errors[name] = f"error: {type(e).__name__}"
        if name not in modes and name in prev:
            modes[name] = prev[name]
    out = {"updated": stamp, "modes": modes, "errors": errors}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("modes:", list(modes), "errors:", errors)


if __name__ == "__main__":
    main()
