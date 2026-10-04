"""Recent stap 3: trechter, 'te volgen'-labels, handmatig-1-uur-toets voor de beste kandidaten, top 20.
Te volgen (bot): bot-basis gem. per trade >= 0,3% na kosten, <= 50 trades/week, K <= 5, >= 70% Kraken-perps,
mediaan <= 150 fills/dag, niet te veel fills. Scalper = mediaan houdtijd < 2 uur of > 50 trades/week of > 150 fills/dag.
Te volgen (hand): ook mediaan houdtijd >= 6 uur en handmatig 1 uur later (15m-candles) per maand > 0.
Rangorde top 20: bot-basis winst per maand op het potje (na kosten), alleen winstgevend (hun prijs) en 'te volgen (bot)'.
Gebruik: python -m bt.recent_select <statsmap> <uitmap>
"""

import glob
import json
import os
import sys

import pandas as pd

from bt.bot_stats import KOSTEN
from bt.recent import DAGEN
from bt.var_select import px_later


def main():
    sdir, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/recent_*.parquet", recursive=True)],
                  ignore_index=True)
    t = {"kandidaten_opgehaald": len(s), "fout_of_te_veel_fills": int((s.status != "ok").sum())}
    m = s[(s.status == "ok") & (s.trades >= 30)].copy(); t["min_30_trades"] = len(m)
    m = m[m.hun_per_maand_pct > 0]; t["winstgevend_hun_prijs"] = len(m)
    m["scalper"] = (m.hun_houdtijd_uur < 2) | (m.trades_per_week > 50) | (m.fills_per_dag_mediaan > 150)
    t["waarvan_scalper"] = int(m.scalper.sum())
    m["te_volgen_bot"] = (~m.scalper) & (m.bot_gem_r_pct >= 0.3) & (m.bot_K <= 5) & (m.kraken_pct >= 70)
    t["te_volgen_bot"] = int(m.te_volgen_bot.sum())
    kand = m[m.te_volgen_bot].sort_values("bot_per_maand_pct", ascending=False).head(60).copy()
    hand = []
    for r in kand.itertuples():
        tr = json.loads(r.trades_json)
        x = []
        for c, o, cl, d, p1, po in tr:
            a, b = px_later(c, o), px_later(c, cl)
            if a and b:
                x.append(d * (b / a - 1) - KOSTEN)
        hand.append(round(100 * sum(x) / r.bot_K / (DAGEN / 30.44), 2) if x else None)
    kand["hand_1u_per_maand_pct"] = hand
    kand["te_volgen_hand"] = (kand.hun_houdtijd_uur >= 6) & (kand.hand_1u_per_maand_pct.fillna(-1) > 0)
    t["te_volgen_hand_in_top60"] = int(kand.te_volgen_hand.sum())
    kand["kort"] = kand.address.str[:6] + "…" + kand.address.str[-4:]
    cols = ["kort", "address", "trades", "trades_per_week", "hun_houdtijd_uur", "bot_K", "hun_winst_pct",
            "hun_gem_r_pct", "bot_gem_r_pct", "hun_per_maand_pct", "bot_per_maand_pct", "hand_1u_per_maand_pct",
            "bot_maxdd_pct", "kraken_pct", "eerste_fill_aandeel", "munten", "te_volgen_hand", "accountwaarde",
            "maand_pnl"]
    kand[cols].to_csv(f"{out}/top60.csv", index=False)
    kand[cols].head(20).to_csv(f"{out}/top20.csv", index=False)
    m.drop(columns=["trades_json"]).to_csv(f"{out}/alle_winstgevend.csv", index=False)
    pd.DataFrame([t]).to_csv(f"{out}/trechter.csv", index=False)
    print(t)
    print(kand[cols].head(20).drop(columns=["address"]).to_string(index=False))


if __name__ == "__main__":
    main()
