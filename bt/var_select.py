"""Varianten (vooraf vastgelegd 4 okt 00:30): van weinig naar veel trades, per groep de hoogste winst.
Keuze op data t/m 18-8-2026, bot-basis (instap = eerste fill van de trader, 0,32% kosten per trade).
Groepen op trades/week: weinig 0,5-2 | swing 2-5 | actief 5-15 | veel 15-50.
Eisen voor alle groepen: >= 180 dagen geschiedenis, >= 20 trades, >= 5 maanden met trades, <= 30% verliesmaanden,
totaal > 0, >= 70% trades in Kraken-perps, laatste trade <= 45 dagen voor 18-8, K <= 5, gem. per trade < 50%,
grootste daling > -50%. Rangorde: gem. maandrendement op het potje (bot-basis, na kosten), hoogste eerst.
Toets top 15 op 19-8 t/m gisteren (API): bot met kosten, bot zonder kosten, hun prijs, handmatig 1 uur later (15m-candles).
Gebruik: python -m bt.var_select <groep> <statsmap> <universe.parquet> <uitmap>
"""

import glob
import math
import os
import signal
import sys
import time

import pandas as pd

from bot import hl
from bt.bot_stats import KOSTEN, bot_trades
from bt.brede_stats import pot_stats

EIND = pd.Timestamp("2026-08-19").value // 10**6
NU = int(time.time() * 1000) // hl.DAY * hl.DAY
GROEPEN = {"weinig": (0.5, 2), "swing": (2, 5), "actief": (5, 15), "veel": (15, 50)}
Q = 15 * 60_000
_c = {}


def px_later(coin, t, min_=60):
    """Openingsprijs van de eerste 15m-candle >= t + min_ minuten."""
    if coin not in _c:
        _c[coin] = {k: v[0] for k, v in hl.candles(coin, EIND - hl.DAY, NU + hl.DAY, "15m").items()}
    return _c[coin].get(math.ceil((t + min_ * 60_000) / Q) * Q)


def main():
    groep, sdir, upath, out = sys.argv[1:5]
    lo, hi = GROEPEN[groep]
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/bot_*.parquet", recursive=True)])
    m = s.merge(pd.read_parquet(upath)[["address", "trades_per_week", "spanne_dagen"]], on="address")
    stap = {"met_stats": len(m)}
    m = m[(m.trades_per_week >= lo) & (m.trades_per_week < hi)]; stap["in_groep"] = len(m)
    m = m[(m.spanne_dagen >= 180) & (m.bot_trades >= 20) & (m.bot_maanden >= 5)]; stap["lang_genoeg"] = len(m)
    m = m[(m.bot_verliesmaanden <= 0.3 * m.bot_maanden) & (m.bot_totaal_pct > 0)]; stap["lang_winstgevend"] = len(m)
    m = m[(m.kraken_pct >= 70) & (m.laatste_trade >= EIND - 45 * hl.DAY) & (m.bot_K <= 5)
          & (m.bot_gem_r_pct < 50) & (m.bot_maxdd_pct > -50)]; stap["kraken_actief_veilig"] = len(m)
    print(groep, stap, flush=True)
    top = m.sort_values("bot_gem_maand_pct", ascending=False).head(15).reset_index(drop=True)
    rows = []
    pd.DataFrame([{"groep": groep, **stap}]).to_csv(f"{out}/{groep}_trechter.csv", index=False)

    def te_lang(*_):
        raise TimeoutError

    signal.signal(signal.SIGALRM, te_lang)
    begin = time.time()
    for _, w in top.iterrows():
        if time.time() - begin > 80 * 60:
            print("tijdbudget op, stop", flush=True)
            break
        t0 = time.time()
        signal.alarm(300)
        try:
            rows.append(toets(groep, w))
        except Exception as exc:  # noqa: BLE001
            print(w.address[:10], "overgeslagen:", type(exc).__name__, flush=True)
        finally:
            signal.alarm(0)
        if rows:
            pd.DataFrame(rows).to_csv(f"{out}/{groep}_top15.csv", index=False)
        print(f"{w.address[:10]} klaar in {time.time() - t0:.0f}s", flush=True)


def toets(groep, w):
    if True:
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        bt_ = [t for t in bot_trades(fl) if t[1] >= EIND]
        k = int(w.bot_K)
        bot = [r * (po / p1 - 1) - KOSTEN for c, o, cl, r, p1, pv, po, q, ad in bt_]
        hun = [r * (po / pv - 1) for c, o, cl, r, p1, pv, po, q, ad in bt_]
        hand = []
        for c, o, cl, r, p1, pv, po, q, ad in bt_:
            if cl + 3_600_000 >= NU:
                continue
            a, b = px_later(c, o), px_later(c, cl)
            if a and b:
                hand.append(r * (b / a - 1) - KOSTEN)
        e = lambda x: round(100 / k * sum(x), 2)  # noqa: E731
        ch = hl.info({"type": "clearinghouseState", "user": w.address})
        res = {"groep": groep, "kort": w.address[:6] + "…" + w.address[-4:], "address": w.address,
                     "trades_per_week": round(w.trades_per_week, 2), "K": k, "keuze_trades": int(w.bot_trades),
                     "keuze_maanden": int(w.bot_maanden), "keuze_verliesmaanden": int(w.bot_verliesmaanden),
                     "keuze_winst_pct": w.bot_winst_pct, "keuze_gem_r_pct": w.bot_gem_r_pct,
                     "keuze_gem_maand_pct": w.bot_gem_maand_pct, "keuze_maxdd_pct": w.bot_maxdd_pct,
                     "houdtijd_uur": w.bot_houdtijd_uur, "test_trades": len(bt_),
                     "test_bot": e(bot), "test_bot_zonder_kosten": e([x + KOSTEN for x in bot]),
                     "test_hun_prijs": e(hun), "test_hand_1u": e(hand), "test_hand_trades": len(hand),
                     "accountwaarde_nu": round(float(ch.get("marginSummary", {}).get("accountValue", 0)), 0)}
        print(res["kort"], res["test_bot"], res["test_hand_1u"], flush=True)
        return res


if __name__ == "__main__":
    main()
