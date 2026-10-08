#!/usr/bin/env python3
"""Build the house-price site data from HM Land Registry open data.

Sources (all Open Government Licence v3.0):
  * HM Land Registry Price Paid Data (England & Wales), yearly files - refreshed monthly by HMLR
  * UK House Price Index full file (HMLR / ONS) - monthly
  * OS Code-Point Open (postcode locations) - quarterly
  * postcodes.io - only to map each local authority to its region (≈330 lookups in 4 requests)

Writes site/data/meta.json, site/data/hpi.json, site/data/districts.json, site/data/la.json and
site/data/d/<OUTCODE>.json (stats, trend series, sector stats and recent sales for that postcode district).
Fails loudly (non-zero exit) if any source is missing, so the workflow never deploys broken data and the
previous good site stays live.
"""
import csv, datetime as dt, email.utils, hashlib, io, json, math, os, re, shutil, sys, time, urllib.request, zipfile
from collections import Counter, defaultdict
import numpy as np

UA = "HousePricesTool/1.0 (personal, non-commercial; England & Wales sold prices)"
PPD = "http://prod1.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com/pp-{}.csv"
HPI = "https://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data/UK-HPI-full-file-{}.csv"
CPO = "https://api.os.uk/downloads/v1/products/CodePointOpen/downloads?area=GB&format=CSV&redirect"
CACHE = os.environ.get("CACHE", "cache")
OUT = "site/data"
SALES_MONTHS = 24          # individual sales kept for the last 24 months
SALES_CAP = 2500           # ...and at most this many per postcode district (newest first)
TYPES = "DSTF"             # detached, semi, terraced, flat (O = other, only in category B)
NOW = dt.datetime.now(dt.timezone.utc)


def req(url, method="GET"):
    return urllib.request.Request(url, method=method, headers={"User-Agent": UA})


def head(url):
    try:
        with urllib.request.urlopen(req(url, "HEAD"), timeout=60) as r:
            return r.status, r.headers.get("Last-Modified"), int(r.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as e:
        return e.code, None, 0


def download(url, path, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(req(url), timeout=300) as r, open(path + ".part", "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
                lm = r.headers.get("Last-Modified")
            os.replace(path + ".part", path)
            return lm
        except Exception as e:
            print(f"  download {url} failed ({e}), try {i + 1}", file=sys.stderr)
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"could not download {url}")


def iso(lm):
    return email.utils.parsedate_to_datetime(lm).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if lm else None


# ---------- OSGB36 easting/northing -> WGS84 lat/lon (OS guide; ~5 m accuracy), vectorised ----------
def osgb_to_wgs84(E, N):
    E = np.asarray(E, float); N = np.asarray(N, float)
    a, b = 6377563.396, 6356256.909
    F0, lat0, lon0, N0, E0 = 0.9996012717, math.radians(49), math.radians(-2), -100000.0, 400000.0
    e2 = 1 - b * b / (a * a); n = (a - b) / (a + b)
    lat = np.full_like(N, lat0); M = np.zeros_like(N)
    for _ in range(12):
        lat = (N - N0 - M) / (a * F0) + lat
        dl, sl = lat - lat0, lat + lat0
        M = b * F0 * ((1 + n + 1.25 * n ** 2 + 1.25 * n ** 3) * dl - (3 * n + 3 * n ** 2 + 21 / 8 * n ** 3) * np.sin(dl) * np.cos(sl)
                      + (15 / 8 * n ** 2 + 15 / 8 * n ** 3) * np.sin(2 * dl) * np.cos(2 * sl) - 35 / 24 * n ** 3 * np.sin(3 * dl) * np.cos(3 * sl))
    s = np.sin(lat); c = np.cos(lat); t = np.tan(lat)
    nu = a * F0 / np.sqrt(1 - e2 * s ** 2); rho = a * F0 * (1 - e2) / (1 - e2 * s ** 2) ** 1.5; eta2 = nu / rho - 1
    VII = t / (2 * rho * nu); VIII = t / (24 * rho * nu ** 3) * (5 + 3 * t ** 2 + eta2 - 9 * t ** 2 * eta2)
    IX = t / (720 * rho * nu ** 5) * (61 + 90 * t ** 2 + 45 * t ** 4)
    X = 1 / (c * nu); XI = 1 / (c * 6 * nu ** 3) * (nu / rho + 2 * t ** 2)
    XII = 1 / (c * 120 * nu ** 5) * (5 + 28 * t ** 2 + 24 * t ** 4)
    XIIA = 1 / (c * 5040 * nu ** 7) * (61 + 662 * t ** 2 + 1320 * t ** 4 + 720 * t ** 6)
    dE = E - E0
    phi = lat - VII * dE ** 2 + VIII * dE ** 4 - IX * dE ** 6
    lam = lon0 + X * dE - XI * dE ** 3 + XII * dE ** 5 - XIIA * dE ** 7
    # Airy 1830 -> cartesian -> Helmert -> WGS84
    sp, cp = np.sin(phi), np.cos(phi)
    nu2 = a / np.sqrt(1 - e2 * sp ** 2)
    x = nu2 * cp * np.cos(lam); y = nu2 * cp * np.sin(lam); z = (1 - e2) * nu2 * sp
    tx, ty, tz, sc = 446.448, -125.157, 542.060, -20.4894e-6
    rx, ry, rz = [math.radians(v / 3600) for v in (0.1502, 0.2470, 0.8421)]
    x2 = tx + (1 + sc) * x - rz * y + ry * z
    y2 = ty + rz * x + (1 + sc) * y - rx * z
    z2 = tz - ry * x + rx * y + (1 + sc) * z
    a2, b2 = 6378137.0, 6356752.314245
    e22 = 1 - b2 * b2 / (a2 * a2); p = np.sqrt(x2 ** 2 + y2 ** 2)
    phi2 = np.arctan2(z2, p * (1 - e22))
    for _ in range(10):
        nu3 = a2 / np.sqrt(1 - e22 * np.sin(phi2) ** 2)
        phi2 = np.arctan2(z2 + e22 * nu3 * np.sin(phi2), p)
    return np.degrees(phi2), np.degrees(np.arctan2(y2, x2))


def smart_title(s):
    s = s.strip().title()
    s = re.sub(r"'S\b", "'s", s)
    s = re.sub(r"\b(\d+)([A-Z])\b", lambda m: m.group(1) + m.group(2), s)  # 12A stays 12A
    return s


# Individual sales whose address contains one of a few excluded words are left out of the published
# sales lists (they still count in all averages). Words are stored as short SHA-1 hashes.
EXCLUDE = {"e3579b1e47f2", "70dd840f5bc3", "7791502c667c", "3d48292e4d27"}


def excluded(*fields):
    for w in re.findall(r"[a-z]+", " ".join(fields).lower()):
        if hashlib.sha1(w.encode()).hexdigest()[:12] in EXCLUDE:
            return True
    return False


def med(a):
    return int(round(float(np.median(a)))) if len(a) else None


def main():
    os.makedirs(CACHE, exist_ok=True)
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT + "/d")

    # ---------- sources ----------
    cur_year = NOW.year
    years = list(range(cur_year - 6, cur_year + 1))
    ppd_lm = {}
    for y in years:
        st, lm, size = head(PPD.format(y))
        if st != 200:
            if y == cur_year:
                print(f"pp-{y}.csv not published yet ({st}) - fine early in the year")
                years.remove(y); continue
            raise RuntimeError(f"pp-{y}.csv HTTP {st}")
        path = f"{CACHE}/pp-{y}.csv"
        if not (os.path.exists(path) and os.path.getsize(path) == size and os.environ.get("REUSE")):
            print(f"downloading pp-{y}.csv ({size / 1e6:.0f} MB)", flush=True)
            download(PPD.format(y), path)
        ppd_lm[y] = iso(lm)
    hpi_month = None
    d = NOW.replace(day=1)
    for _ in range(6):
        key = d.strftime("%Y-%m")
        if head(HPI.format(key))[0] == 200:
            hpi_month = key; break
        d = (d - dt.timedelta(days=1)).replace(day=1)
    if not hpi_month:
        raise RuntimeError("UK HPI full file not found for the last 6 months")
    download(HPI.format(hpi_month), f"{CACHE}/uk-hpi.csv")
    cpo_path = f"{CACHE}/codepo_gb.zip"
    if not (os.path.exists(cpo_path) and os.environ.get("REUSE")):
        download(CPO, cpo_path)

    # ---------- postcode locations ----------
    t0 = time.time()
    pcs, E, N, LA = [], [], [], []
    with zipfile.ZipFile(cpo_path) as z:
        meta_txt = z.read("Doc/metadata.txt").decode("latin-1")
        cpo_ver = (re.search(r"DATASET VERSION NUMBER:\s*(\S+)", meta_txt) or [None, ""])[1]
        for name in z.namelist():
            if not name.startswith("Data/CSV/") or not name.endswith(".csv"):
                continue
            for r in csv.reader(io.TextIOWrapper(z.open(name), "latin-1")):
                if r[4] not in ("E92000001", "W92000004") or r[2] in ("0", ""):
                    continue
                pcs.append(r[0]); E.append(int(r[2])); N.append(int(r[3])); LA.append(r[8])
    lat, lon = osgb_to_wgs84(E, N)
    loc = {pc: (round(float(la_), 5), round(float(lo_), 5), l) for pc, la_, lo_, l in zip(pcs, lat, lon, LA)}
    print(f"Code-Point Open {cpo_ver}: {len(loc)} E&W postcodes ({time.time() - t0:.0f}s)", flush=True)
    del pcs, E, N, LA, lat, lon

    # ---------- price paid ----------
    t0 = time.time()
    base = years[0] * 12
    G = defaultdict(lambda: ([], [], []))    # group key -> (month idx, type idx, price), category A only
    sales = defaultdict(list)
    names = defaultdict(Counter)
    la_by_name = defaultdict(Counter)
    month_count = Counter()
    dist_pcs = defaultdict(set)      # every postcode with a sale (all years) -> district centroid
    dist_las = defaultdict(Counter)
    keep_from = (NOW - dt.timedelta(days=31 * (SALES_MONTHS + 6))).strftime("%Y-%m-%d")
    latest = ""
    rows = 0
    rows_b = 0
    for y in years:
        with open(f"{CACHE}/pp-{y}.csv", newline="", encoding="latin-1") as f:
            for r in csv.reader(f):
                # tid, price, date, postcode, type, new, tenure, paon, saon, street, locality, town, district, county, ppd cat, status
                if len(r) < 15 or r[15:16] == ["D"]:
                    continue
                rows += 1
                date = r[2][:10]
                if date > latest:
                    latest = date
                m = int(date[:4]) * 12 + int(date[5:7]) - 1 - base
                pc = r[3].strip()
                if not pc or " " not in pc:
                    continue
                dist = pc.split(" ")[0]
                cat = r[14]
                ptype = r[4]
                price = int(r[1])
                L = loc.get(pc)
                la = L[2] if L else None
                if la:
                    la_by_name[r[12]][la] += 1
                if cat == "A" and ptype in TYPES:
                    month_count[m] += 1
                    ti = TYPES.index(ptype)
                    for key in ("d:" + dist, "s:" + pc[:-2].strip(), "la:" + (la or "?" + r[12]), "ew"):
                        g = G[key]; g[0].append(m); g[1].append(ti); g[2].append(price)
                else:
                    rows_b += 1
                names[dist][r[10] or r[11]] += 1
                dist_pcs[dist].add(pc)
                if la:
                    dist_las[dist][la] += 1
                if date >= keep_from:
                    sales[dist].append((date, price, pc, r[8], r[7], r[9], r[10], r[11], ptype, r[5], r[6], cat))
    print(f"Price Paid: {rows} rows (cat B/other {rows_b}), latest sale {latest} ({time.time() - t0:.0f}s)", flush=True)

    # last 'complete' month: the newest month whose sales count is at least 60% of the median of the 12 before it
    last_m = max(month_count)
    L = last_m
    while L > 12:
        ref = sorted(month_count[i] for i in range(L - 12, L))[6]
        if month_count[L] >= 0.6 * ref:
            break
        L -= 1
    def mname(i):
        return f"{(base + i) // 12:04d}-{(base + i) % 12 + 1:02d}"
    first_end = 11
    print(f"complete through {mname(L)}; series from {mname(first_end)}", flush=True)

    # resolve unknown LA codes by the PPD local-authority name
    for key in [k for k in G if k.startswith("la:?")]:
        nm = key[4:]
        if la_by_name[nm]:
            code = la_by_name[nm].most_common(1)[0][0]
            g, h = G[key], G["la:" + code]
            h[0].extend(g[0]); h[1].extend(g[1]); h[2].extend(g[2])
        del G[key]
    la_name = {}
    for nm, c in la_by_name.items():
        code = c.most_common(1)[0][0]
        la_name.setdefault(code, smart_title(nm))

    # LA -> region via postcodes.io (one postcode per LA)
    sample = {}
    for pc, (_, _, la) in loc.items():
        if la and la not in sample:
            sample[la] = pc
    region = {}
    items = list(sample.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        body = json.dumps({"postcodes": [p for _, p in chunk]}).encode()
        r = urllib.request.Request("https://api.postcodes.io/postcodes", data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    res = json.load(resp)["result"]
                break
            except Exception as e:
                print("  postcodes.io", e, file=sys.stderr); time.sleep(5); res = []
        for (la, _), x in zip(chunk, res):
            q = x.get("result") if x else None
            if q:
                region[la] = q.get("region") or q.get("country")
                if q.get("admin_district"):
                    la_name[la] = q["admin_district"]
    for la in sample:
        if la not in region:
            region[la] = "Wales" if la.startswith("W") else None
    missing = [la for la in sample if not region.get(la)]
    if len(missing) > 20:
        raise RuntimeError(f"region lookup failed for {len(missing)} local authorities")
    for la, reg in region.items():
        if reg:
            g = G.get("la:" + la)
            if g:
                h = G["rg:" + reg]
                h[0].extend(g[0]); h[1].extend(g[1]); h[2].extend(g[2])

    # ---------- stats ----------
    t0 = time.time()

    def summarise(key, series=True):
        m, t, p = (np.asarray(v) for v in G[key])
        o = np.argsort(m, kind="stable"); m, t, p = m[o], t[o], p[o]
        bytype = [(m[t == i], p[t == i]) for i in range(4)]

        def win(mm, pp, end):
            lo, hi = np.searchsorted(mm, end - 11), np.searchsorted(mm, end, side="right")
            return pp[lo:hi]

        def cur(end):
            a = win(m, p, end)
            out = {"a": [int(len(a)), med(a), int(round(float(a.mean()))) if len(a) else None]}
            for i, ch in enumerate(TYPES):
                b = win(*bytype[i], end)
                out[ch] = [int(len(b)), med(b), int(round(float(b.mean()))) if len(b) else None]
            return out
        res = {"cur": cur(L), "ago": {str(k): cur(L - 12 * k) for k in (1, 3, 5) if L - 12 * k >= first_end}}
        if series:
            sr = {}
            for ch, (mm, pp) in [("a", (m, p))] + [(TYPES[i], bytype[i]) for i in range(4)]:
                vals, ns = [], []
                for end in range(first_end, L + 1):
                    w = win(mm, pp, end)
                    ns.append(int(len(w))); vals.append(med(w) if len(w) >= 5 else None)
                sr[ch] = vals; sr["n" + ch] = ns
            res["series"] = sr
        return res

    regions = {k[3:]: summarise(k) for k in G if k.startswith("rg:")}
    ew = summarise("ew")
    las = {}
    for k in G:
        if k.startswith("la:"):
            code = k[3:]
            s = summarise(k, series=False)
            s.update(name=la_name.get(code, code), region=region.get(code))
            las[code] = s
    print(f"stats for regions/LAs ({time.time() - t0:.0f}s)", flush=True)

    # ---------- per district files ----------
    t0 = time.time()
    dist_index = {}
    sector_keys = defaultdict(list)
    for k in G:
        if k.startswith("s:"):
            sec = k[2:]
            sector_keys[sec.split(" ")[0]].append(sec)
    cut = mname(last_m - SALES_MONTHS + 1) + "-01"
    total_sales = 0
    n_excluded = 0
    for dist in sorted(dist_pcs):
        if not re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?", dist):
            continue
        rec = sorted((s for s in sales.get(dist, []) if s[0] >= cut), key=lambda s: s[0], reverse=True)[:SALES_CAP]
        la = dist_las[dist].most_common(1)[0][0] if dist_las[dist] else None
        pcs_here = {}
        out_sales = []
        for date, price, pc, saon, paon, street, locality, town, ptype, new, ten, cat in rec:
            if excluded(saon, paon, street, locality, town):
                n_excluded += 1
                continue
            if pc in loc:
                pcs_here[pc] = [loc[pc][0], loc[pc][1]]
            out_sales.append([price, int(date.replace("-", "")), pc, smart_title(saon), smart_title(paon), smart_title(street),
                              smart_title(locality), smart_title(town), ptype, 1 if new == "Y" else 0, ten, cat])
        total_sales += len(out_sales)
        # centroid from all postcodes in the district that had any sale (all years)
        pts = [loc[pc][:2] for pc in dist_pcs.get(dist, ()) if pc in loc]
        if not pts:
            continue
        clat = round(sum(p[0] for p in pts) / len(pts), 4); clon = round(sum(p[1] for p in pts) / len(pts), 4)
        top = [smart_title(n) for n, _ in names[dist].most_common(6) if n][:4]
        doc = {"d": dist, "places": top, "la": la, "laName": la_name.get(la), "region": region.get(la), "lat": clat, "lon": clon,
               "stats": summarise("d:" + dist) if ("d:" + dist) in G else None,
               "sectors": {sec: summarise("s:" + sec, series=False) for sec in sorted(sector_keys.get(dist, []))},
               "pcs": pcs_here, "salesFrom": cut, "salesCapped": len(rec) >= SALES_CAP,
               "cols": ["price", "date", "postcode", "saon", "paon", "street", "locality", "town", "type", "new", "tenure", "cat"],
               "sales": out_sales}
        with open(f"{OUT}/d/{dist}.json", "w") as f:
            json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
        c = doc["stats"]["cur"]["a"] if doc["stats"] else [0, None, None]
        dist_index[dist] = [clat, clon, c[0], c[1], ", ".join(top[:2])]
    print(f"{len(dist_index)} district files, {total_sales} recent sales, {n_excluded} left out of lists ({time.time() - t0:.0f}s)", flush=True)
    if len(dist_index) < 2000 or total_sales < 300000:
        raise RuntimeError("suspiciously little data - not publishing")

    # ---------- UK HPI ----------
    hpi_latest, hpi_series = {}, defaultdict(dict)
    start = f"{years[0]}-01"
    with open(f"{CACHE}/uk-hpi.csv", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["AreaCode"]
            if not (code[:3] in ("K02", "K03", "K04", "E92", "W92", "S92", "N92", "E12") or code in las):
                continue
            dd = r["Date"]; ym = dd[6:10] + "-" + dd[3:5]
            try:
                ap = round(float(r["AveragePrice"]))
            except ValueError:
                continue
            if ym >= start and code[:3] in ("K02", "E92", "W92", "S92", "N92", "E12", "K04"):
                hpi_series[code][ym] = ap
            prev = hpi_latest.get(code)
            if not prev or ym > prev["date"]:
                def num(k):
                    try:
                        return round(float(r[k]), 1) if "%" in k else round(float(r[k]))
                    except (ValueError, KeyError):
                        return None
                hpi_latest[code] = {"name": r["RegionName"], "date": ym, "avg": ap, "ch12": num("12m%Change"),
                                    "D": num("DetachedPrice"), "S": num("SemiDetachedPrice"), "T": num("TerracedPrice"), "F": num("FlatPrice")}
    hpi = {"month": hpi_month, "latest": hpi_latest, "series": {k: [v[m] for m in sorted(v)] for k, v in hpi_series.items()},
           "seriesStart": {k: min(v) for k, v in hpi_series.items()}}
    with open(f"{OUT}/hpi.json", "w") as f:
        json.dump(hpi, f, separators=(",", ":"))

    with open(f"{OUT}/la.json", "w") as f:
        json.dump(las, f, separators=(",", ":"))
    with open(f"{OUT}/districts.json", "w") as f:
        json.dump({"cols": ["lat", "lon", "n12", "median12", "places"], "d": dist_index}, f, separators=(",", ":"))
    meta = {"built": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ppd": {"years": years, "published": max(v for v in ppd_lm.values() if v), "latestSale": latest, "complete": mname(L),
                    "seriesStart": mname(first_end), "rows": rows, "salesFrom": cut, "salesMonths": SALES_MONTHS, "salesCap": SALES_CAP},
            "hpi": {"month": hpi_month}, "codepoint": cpo_ver,
            "ew": ew, "regions": regions}
    with open(f"{OUT}/meta.json", "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    print("done", json.dumps(meta["ppd"]))


def cache_key():
    """Short key from the Last-Modified of each yearly file - unchanged key = reuse cached downloads."""
    parts = []
    for y in range(NOW.year - 6, NOW.year + 1):
        st, lm, size = head(PPD.format(y))
        parts.append(f"{y}:{st}:{lm}:{size}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


if __name__ == "__main__":
    if sys.argv[1:] == ["--key"]:
        print(cache_key())
    else:
        main()
