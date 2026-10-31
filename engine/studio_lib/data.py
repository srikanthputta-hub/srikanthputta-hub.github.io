"""Real open data for the videos: FRED (St. Louis Fed) and World Bank, both free and keyless.

The model never invents numbers: it only picks series from CATALOG; the code downloads the data and computes the
fact sheet that the script, the fact-checker and the number audit are all held to.
"""
import csv
import io
import time

import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
WB_URL = "https://api.worldbank.org/v2/country/{c}/indicator/{i}"

COUNTRIES = {"US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia", "DE": "Germany",
             "JP": "Japan", "FR": "France", "IN": "India", "BR": "Brazil", "TR": "Turkey", "AR": "Argentina",
             "ZA": "South Africa", "MX": "Mexico", "CN": "China"}

CATALOG = {
    "us_inflation": dict(source="fred", id="CPIAUCSL", transform="yoy", title="US inflation rate (year over year)", unit="%"),
    "us_fed_funds": dict(source="fred", id="FEDFUNDS", title="US federal funds rate", unit="%"),
    "us_unemployment": dict(source="fred", id="UNRATE", title="US unemployment rate", unit="%"),
    "us_mortgage_30y": dict(source="fred", id="MORTGAGE30US", monthly=True, title="US 30-year mortgage rate", unit="%"),
    "us_10y_yield": dict(source="fred", id="DGS10", monthly=True, title="US 10-year Treasury yield", unit="%"),
    "us_m2": dict(source="fred", id="M2SL", scale=0.001, title="US money supply (M2)", unit="$ trillion"),
    "us_debt_gdp": dict(source="fred", id="GFDEGDQ188S", title="US federal debt as a share of GDP", unit="% of GDP"),
    "us_house_prices": dict(source="fred", id="CSUSHPINSA", transform="yoy", title="US home price growth (year over year)", unit="%"),
    "wb_inflation": dict(source="wb", id="FP.CPI.TOTL.ZG", title="Inflation rate", unit="%"),
    "wb_gdp_growth": dict(source="wb", id="NY.GDP.MKTP.KD.ZG", title="Economic growth (GDP)", unit="%"),
    "wb_unemployment": dict(source="wb", id="SL.UEM.TOTL.ZS", title="Unemployment rate", unit="%"),
    "wb_gov_debt": dict(source="wb", id="GC.DOD.TOTL.GD.ZS", title="Central government debt", unit="% of GDP"),
    "wb_real_rate": dict(source="wb", id="FR.INR.RINR", title="Real interest rate", unit="%"),
    "wb_broad_money": dict(source="wb", id="FM.LBL.BMNY.GD.ZS", title="Broad money supply", unit="% of GDP"),
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def catalog_text(only=None):
    fred = [f"- {k}: {v['title']} ({v['unit']}), monthly/quarterly, from about 1950" for k, v in CATALOG.items() if v["source"] == "fred"]
    wb = [f"- {k}: {v['title']} ({v['unit']}), annual, any country below" for k, v in CATALOG.items() if v["source"] == "wb"]
    codes = ", ".join(f"{c}={n}" for c, n in COUNTRIES.items())
    wb_part = "World Bank series (choose 1-4 country codes):\n" + "\n".join(wb) + f"\nCountry codes: {codes}"
    return wb_part if only == "wb" else "US series:\n" + "\n".join(fred) + "\n" + wb_part


def _get(url, params, tries=4):
    """FRED in particular can be slow from cloud runners: generous timeout, backoff, several attempts."""
    err = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, timeout=(10, 75), headers={"User-Agent": "money-blueprint-studio/1.0"})
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            err = e
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
    raise err


def fetch_text(url, params=None):
    return _get(url, params).text


def fetch_json(url, params=None):
    return _get(url, params).json()


def fred_ok():
    """FRED often hangs when called from cloud runners. One quick probe decides whether to use it at all."""
    try:
        r = requests.get(FRED_CSV, params={"id": "UNRATE", "cosd": "2024-01-01"}, timeout=(8, 15), headers={"User-Agent": "money-blueprint-studio/1.0"})
        return r.ok and "UNRATE" in r.text[:40]
    except requests.RequestException:
        return False


def parse_fred(text, spec):
    """FRED csv -> [(year_fraction, value)] with optional monthly averaging, year-over-year and scaling."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for row in reader:
        if len(row) < 2 or row[1].strip() in ("", ".", "NA"):
            continue
        try:
            rows.append((int(row[0][:4]), int(row[0][5:7]), float(row[1])))
        except ValueError:
            continue
    if spec.get("monthly"):
        acc = {}
        for y, m, v in rows:
            acc.setdefault((y, m), []).append(v)
        rows = [(y, m, sum(vs) / len(vs)) for (y, m), vs in sorted(acc.items())]
    if spec.get("transform") == "yoy":
        idx = {(y, m): v for y, m, v in rows}
        rows = [(y, m, (v / idx[(y - 1, m)] - 1) * 100) for y, m, v in rows if idx.get((y - 1, m))]
    scale = spec.get("scale", 1)
    return [(y + (m - 1) / 12, v * scale) for y, m, v in rows]


def parse_wb(payload):
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    return sorted((float(p["date"]), float(p["value"])) for p in payload[1] if p.get("value") is not None)


def load_chart(spec, cid):
    """spec: {"series": catalog id, "countries": [...], "start_year": int, "title": str} -> chart dict (or ValueError)."""
    sid = spec.get("series")
    if sid not in CATALOG:
        raise ValueError(f"unknown series {sid!r}")
    s = CATALOG[sid]
    fred = s["source"] == "fred"
    start = int(spec.get("start_year") or (2010 if fred else 2000))
    lines = []
    if fred:
        pts = parse_fred(fetch_text(FRED_CSV, {"id": s["id"], "cosd": f"{start - 1}-01-01"}), s)
        lines.append({"label": s["title"], "points": [p for p in pts if p[0] >= start]})
    else:
        for c in [c for c in (spec.get("countries") or ["US"]) if c in COUNTRIES][:4] or ["US"]:
            pts = parse_wb(fetch_json(WB_URL.format(c=c, i=s["id"]), {"format": "json", "per_page": 80}))
            lines.append({"label": COUNTRIES[c], "points": [p for p in pts if p[0] >= start]})
    lines = [ln for ln in lines if len(ln["points"]) >= 8]
    if not lines:
        raise ValueError(f"not enough data for {sid}")
    return {"id": cid, "series": sid, "title": spec.get("title") or s["title"], "unit": s["unit"],
            "granularity": "month" if fred else "year", "lines": lines,
            "source": "FRED (Federal Reserve Bank of St. Louis)" if fred else "World Bank",
            "series_code": s["id"], "why": spec.get("why", "")}


def fmt_t(t, gran):
    y = int(t + 1e-6)
    return f"{MONTHS[min(11, int(round((t - y) * 12)))]} {y}" if gran == "month" else str(y)


def fmt_v(v, unit):
    return f"{v:,.1f} {unit}".replace(" %", "%").strip()


def fact_sheet(charts):
    """Returns (text, allowed_numbers). Every figure the script may quote appears in the text."""
    out, allowed = [], set()
    for ch in charts:
        g = ch["granularity"]
        out.append(f"[{ch['id']}] {ch['title']} (source: {ch['source']}; unit: {ch['unit']})")
        lasts = []
        for ln in ch["lines"]:
            pts = ln["points"]
            first, last = pts[0], pts[-1]
            hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
            chg = last[1] - first[1]
            out.append(f"  {ln['label']}: {fmt_t(first[0], g)} = {fmt_v(first[1], ch['unit'])}; latest {fmt_t(last[0], g)} = "
                       f"{fmt_v(last[1], ch['unit'])}; peak {fmt_v(hi[1], ch['unit'])} ({fmt_t(hi[0], g)}); "
                       f"low {fmt_v(lo[1], ch['unit'])} ({fmt_t(lo[0], g)}); change {chg:+,.1f}")
            allowed.update([first[1], last[1], hi[1], lo[1], chg, abs(chg), hi[1] - lo[1]])
            lasts.append(last[1])
            steps = [(abs(b[1] - a[1]), a, b) for a, b in zip(pts, pts[1:])]
            if steps:  # the Researcher's highlight for the Writer: the single biggest move in the series
                size, a, b = max(steps, key=lambda s: s[0])
                out.append(f"    Highlight: biggest single move was {fmt_t(a[0], g)} to {fmt_t(b[0], g)}, from {fmt_v(a[1], ch['unit'])} to "
                           f"{fmt_v(b[1], ch['unit'])} (change {b[1] - a[1]:+,.1f})")
                allowed.update([a[1], b[1], b[1] - a[1], size])
        allowed.update(abs(a - b) for i, a in enumerate(lasts) for b in lasts[i + 1:])
    return "\n".join(out), sorted(allowed)
