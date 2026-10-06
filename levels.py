"""cMTrades Levels – läuft per GitHub Action Mo–Fr morgens, schreibt levels/heute.md
Morgens 2 Textblöcke für NQ (wie im Backtest-Journal), wird von morgen_levels.py aufgerufen:
1. OI-Levels: QQQ-Kette (Cboe, Vortags-OI), Freitags-Verfall, nur Strikes in der Vortags-Range,
   C1–C3 / P1–P3 / DP, Faktor = NQ 16:14 NY Vortag ÷ QQQ-Kurs (Cboe)
2. Volumen-Levels: Value Area Vortag (14–22 Uhr DE) + Vorwoche (Mo–Fr), POC/VAH/VAL, 5-Punkte-Bins
NQ-1m-Daten: Databento (letzte 12 Tage, ca. 0,4 Cent pro Tag)
Manuell testen: .venv/bin/python morgen_textblock.py
"""
import datetime as dt
import re
from pathlib import Path

import databento as db
import pandas as pd

ORDNER = Path(__file__).parent
BIN = 5.0


def kfmt(n):
    return f"{n / 1000:.1f}k".replace(".", ",") if n >= 1000 else f"{int(n)}"


def nq_holen():
    import os
    key = os.environ.get("DATABENTO_API_KEY") or re.search(r"db-[A-Za-z0-9]+", (ORDNER / ".env").read_text()).group(0)
    c = db.Historical(key)
    ende = c.metadata.get_dataset_range(dataset="GLBX.MDP3")["end"]
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    df = c.timeseries.get_range(dataset="GLBX.MDP3", symbols=["NQ.v.0"], stype_in="continuous",
                                schema="ohlcv-1m", start=start, end=ende).to_df()
    df["de"] = df.index.tz_convert("Europe/Berlin")
    df["ny"] = df.index.tz_convert("America/New_York")
    df["tag"] = df.de.dt.date
    return df


def profil(df):
    tp = ((df.high + df.low + df.close) / 3 / BIN).round() * BIN
    v = df.volume.groupby(tp).sum().sort_index()
    poc = v.idxmax()
    lo = hi = v.index.get_loc(poc)
    acc, tot = v.iloc[lo], v.sum()
    while acc < 0.7 * tot and (lo > 0 or hi < len(v) - 1):
        a = v.iloc[lo - 1] if lo > 0 else -1
        b = v.iloc[hi + 1] if hi < len(v) - 1 else -1
        if b >= a:
            hi += 1; acc += b
        else:
            lo -= 1; acc += a
    sm = v.rolling(5, center=True).mean()  # HVN wie im Backtest-Journal (oi_chartdaten.py)
    hvn = [float(q) for i, q in enumerate(sm.index) if 2 <= i < len(sm) - 2 and sm.iloc[i] == sm.iloc[i - 2:i + 3].max() and sm.iloc[i] > sm.mean() * 1.2]
    return {"VAH": float(v.index[hi]), "POC": float(poc), "VAL": float(v.index[lo]), "HVN": hvn}


def oi_block(kette, qqq_kurs, fut, heute):
    tage = sorted(t for t in set(fut[(fut.de.dt.hour >= 14) & (fut.de.dt.hour < 22)].tag) if t < heute)
    vt = tage[-1]
    vs = fut[(fut.tag == vt) & (fut.de.dt.hour >= 6) & (fut.de.dt.hour < 22)]
    inst = vs.instrument_id.mode()[0]
    vs = vs[vs.instrument_id == inst]
    k = fut[(fut.instrument_id == inst) & (fut.ny.dt.date == vt) & (fut.ny.dt.hour == 16) & (fut.ny.dt.minute == 14)]
    faktor = (k.close.iloc[-1] if len(k) else vs.close.iloc[-1]) / qqq_kurs
    # Freitags-Verfall (bei Feiertag Donnerstag davor)
    exp = pd.Series(sorted(kette.verfall[kette.verfall >= pd.Timestamp(heute)].unique()))
    fr = exp[exp.dt.weekday == 4]
    verfall = fr.iloc[0] if len(fr) else exp.iloc[0]
    do = exp[(exp.dt.weekday == 3) & (exp < verfall) & (exp > verfall - pd.Timedelta(days=7))]
    if len(fr) and (verfall - pd.Timestamp(heute)).days > 7 and len(do):
        verfall = do.iloc[-1]
    x = kette[(kette.verfall == verfall) & (kette.strike >= vs.low.min() / faktor) & (kette.strike <= vs.high.max() / faktor)]
    p = x.pivot_table(index="strike", columns="typ", values="open_interest", aggfunc="sum").fillna(0)
    for c in ("C", "P"):
        if c not in p:
            p[c] = 0
    p["summe"] = p.C + p.P
    calls = p.C.nlargest(3); calls = calls[calls > 0]
    puts = p.P.nlargest(3); puts = puts[puts > 0]
    lv = {}
    for i, st in enumerate(calls.index, 1):
        lv.setdefault(st, {})["C"] = i
    for i, st in enumerate(puts.index, 1):
        lv.setdefault(st, {})["P"] = i
    st_kombi = p.summe.idxmax()
    zeilen, dp_nr = [], 0
    for st, rr in lv.items():
        c, pu = p.loc[st, "C"], p.loc[st, "P"]
        dp = "C" in rr and "P" in rr and min(c, pu) >= 0.5 * max(c, pu)
        dp_nr += dp
        label = f"DP{dp_nr}" if dp else (f"C{rr['C']}" if "C" in rr and ("P" not in rr or c >= pu) else f"P{rr['P']}")
        typ = "DOPPELZONE" if dp else ("Call OI" if label.startswith("C") else "Put OI")
        st_txt = [s for s, ok in (("stärkster Call", len(calls) and st == calls.index[0]), ("stärkster Put", len(puts) and st == puts.index[0]),
                                  ("stärkstes kombiniert", st == st_kombi)) if ok]
        rolle = "Doppelzone" if dp else ("Wall" if typ == "Call OI" else "Support")  # Chico 05.10.: kurzes Format
        stark = " (strong)" if any(x in st_txt for x in ("stärkster Call", "stärkster Put")) else ""
        preis = round(st * faktor * 4) / 4
        zeilen.append((preis, f"{int(round(preis))},{label} -{rolle} | {kfmt(c)} - {kfmt(pu)}{stark}"))
    # Chico 06.10.: zusätzlich die 2 stärksten Levels ÜBER und UNTER der Vortags-Range (bis 3 % Abstand)
    alle = kette[kette.verfall == verfall].pivot_table(index="strike", columns="typ", values="open_interest", aggfunc="sum").fillna(0)
    for c in ("C", "P"):
        if c not in alle:
            alle[c] = 0
    alle["summe"] = alle.C + alle.P
    lo_s, hi_s = vs.low.min() / faktor, vs.high.max() / faktor
    for seite, maske, pf in (("über", (alle.index > hi_s) & (alle.index <= hi_s * 1.03), "↑"),
                             ("unter", (alle.index < lo_s) & (alle.index >= lo_s * 0.97), "↓")):
        for st in alle[maske].summe.nlargest(2).index:
            kand = alle[maske]
            c, pu = kand.loc[st, "C"], kand.loc[st, "P"]
            dp = min(c, pu) >= 0.5 * max(c, pu)
            lab = "DP" if dp else ("C" if c >= pu else "P")
            rolle = "Doppelzone" if dp else ("Wall" if lab == "C" else "Support")
            preis = round(st * faktor * 4) / 4
            zeilen.append((preis, f"{int(round(preis))},{lab}{pf} -{rolle} | {kfmt(c)} - {kfmt(pu)} (außerhalb Range)"))
    zeilen.sort(key=lambda z: -z[0])
    info = f"Verfall {verfall.date():%d.%m.} · Faktor {faktor:.3f}"
    return "\n".join(z[1] for z in zeilen), info


def vol_block(fut, heute):
    tage = sorted(t for t in set(fut[(fut.de.dt.hour >= 14) & (fut.de.dt.hour < 22)].tag) if t < heute)
    vt = tage[-1]
    tag = profil(fut[(fut.tag == vt) & (fut.de.dt.hour >= 14) & (fut.de.dt.hour < 22)])
    mo = heute - dt.timedelta(days=heute.weekday())  # Vorwoche = Kalenderwoche davor
    vw_von, vw_bis = mo - dt.timedelta(days=7), mo - dt.timedelta(days=3)
    woche = profil(fut[(fut.tag >= vw_von) & (fut.tag <= vw_bis)])
    z = []
    for prof, kz, txt in ((tag, "T", "Vortag"), (woche, "W", "Vorwoche")):
        z += [(w, f"{int(w)},{k} {kz} | Volumen {txt}") for k, w in prof.items() if k != "HVN"]
        z += [(w, f"{int(w)},HVN {kz} | Volumen {txt}") for w in prof["HVN"]
              if min(abs(w - prof[k]) for k in ("VAH", "POC", "VAL")) > 10]  # HVN direkt an VAH/POC/VAL weglassen
    # Chico 06.10.: Value Area Vormonat (VAH/POC/VAL M, ohne HVN)
    m_bis = heute.replace(day=1) - dt.timedelta(days=1)
    m_von = m_bis.replace(day=1)
    mfut = fut[(fut.tag >= m_von) & (fut.tag <= m_bis)]
    if len(mfut) > 1000:
        monat = profil(mfut)
        z += [(w, f"{int(w)},{k} M | Volumen Vormonat") for k, w in monat.items() if k != "HVN"]
    z.sort(key=lambda a: -a[0])
    return "\n".join(a[1] for a in z), f"Vortag {vt:%d.%m.} · Vorwoche {vw_von:%d.%m.}–{vw_bis:%d.%m.} · Vormonat {m_von:%m.%Y}"


def qqq_schluss():
    """QQQ-Schlusskurs (Feld close), nicht der Live-Kurs – sonst verrutscht der Faktor tagsüber."""
    import requests
    d = requests.get("https://cdn-api.cboe.com/api/global/delayed_quotes/options/QQQ.json",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=60).json()["data"]
    # Handel heute schon gelaufen → close ist der Live-Kurs, dann prev_day_close (= Vortags-Schluss)
    if str(d.get("last_trade_time", ""))[:10] == dt.date.today().isoformat():
        return d["prev_day_close"]
    return d.get("close") or d["current_price"]


def bloecke(kette_qqq, qqq_kurs=None):
    heute = dt.date.today()
    qqq_kurs = qqq_schluss()
    fut = nq_holen()
    oi, oi_info = oi_block(kette_qqq, qqq_kurs, fut, heute)
    vol, vol_info = vol_block(fut, heute)
    return [f"## OI-Levels NQ ({oi_info})", "```", oi, "```", "", f"## Volumen-Levels NQ ({vol_info})", "```", vol, "```", ""]


def hole_qqq():
    import requests
    r = requests.get("https://cdn-api.cboe.com/api/global/delayed_quotes/options/QQQ.json",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    d = r.json()
    df = pd.DataFrame(d["data"]["options"])
    m = df["option"].str.extract(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
    df["verfall"] = pd.to_datetime(m[1], format="%y%m%d")
    df["typ"], df["strike"] = m[2], m[3].astype(int) / 1000
    return df, d["timestamp"]


if __name__ == "__main__":
    import json
    heute = dt.date.today().isoformat()
    import os
    jetzt = dt.datetime.now()
    if os.environ.get("EVENT") == "schedule" and (jetzt.hour, jetzt.minute) < (7, 30):
        print("vor 07:30 DE – warte")
        raise SystemExit
    fertig = ORDNER / f"levels/{heute}.md"
    if os.environ.get("EVENT") == "schedule" and fertig.exists() and "⚠️" not in fertig.read_text():
        print("heute schon fertig")
        raise SystemExit
    df, stand = hole_qqq()
    # Prüfen, ob Cboe den OI schon aktualisiert hat (Summe OI gleich wie gestern → Warnung)
    summe = int(df.open_interest.sum())
    alt = ORDNER / "levels/oi_summe.json"
    vorher = json.loads(alt.read_text()) if alt.exists() else {}
    warnung = ""
    if vorher.get("summe") == summe and vorher.get("datum") != heute:
        warnung = "⚠️ OI unverändert seit " + vorher["datum"] + " – Cboe hat evtl. noch nicht aktualisiert\n\n"
    else:
        alt.write_text(json.dumps({"datum": heute, "summe": summe}))
    text = f"# Levels {heute}\n\n{warnung}" + "\n".join(bloecke(df)) + f"\nCboe-Stand {stand}\n"
    (ORDNER / "levels/heute.md").write_text(text, encoding="utf-8")
    (ORDNER / f"levels/{heute}.md").write_text(text, encoding="utf-8")
    print(text)
