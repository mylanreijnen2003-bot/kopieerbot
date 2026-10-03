"""Dagrapport en GO-check. Bedragen in de munt van het potje (Kraken rekent in USD; geen wisselkoers)."""

from __future__ import annotations

from statistics import mean

from .config import short


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _stats(rows: list[dict], shadow: list[dict]) -> dict:
    done = [r for r in rows if r["status"] == "uitgevoerd"]
    sdone = [r for r in shadow if r["status"] == "uitgevoerd"]
    return {"trades": len(done),
            "delay": mean(_f(r["vertraging_s"]) for r in done) if done else 0.0,
            "slip": mean(_f(r["slippage_pct"]) for r in done) if done else 0.0,
            "cost": sum(_f(r["slippage_eur"]) + _f(r["fee"]) for r in done),
            "te_klein": sum(r["status"] == "te klein" for r in rows),
            "niet_kraken": sum(r["status"] == "niet op Kraken" for r in rows),
            "te_laat": sum(r["status"] == "te laat" for r in rows),
            "eigen": sum(_f(r["resultaat"]) for r in done),
            "hun": sum(_f(r["resultaat"]) for r in sdone),
            "worst": min((_f(r["resultaat"]) for r in done), default=0.0)}


def build(cfg, copier, book, rows: list[dict], shadow: list[dict], start_ms: int, now_ms: int,
          title: str = "Dagrapport") -> tuple[str, dict]:
    lines, tot_eq = [title], 0.0
    for addr in cfg.traders:
        sid = short(addr)
        s = _stats([r for r in rows if r["trader"] == sid], [r for r in shadow if r["trader"] == sid])
        eq = copier.equity(addr, book)
        tot_eq += eq
        tr = copier.state["traders"][addr]
        lines.append(
            f"\n{sid}{' (GEPAUZEERD)' if tr['paused'] else ''}\n"
            f" winst {eq - cfg.pot:+.2f} ({(eq / cfg.pot - 1) * 100:+.1f}%), trades {s['trades']}\n"
            f" gem. vertraging {s['delay']:.1f} s, gem. slippage {s['slip']:+.3f}%\n"
            f" gemist: te klein {s['te_klein']}, niet op Kraken {s['niet_kraken']}, te laat {s['te_laat']}\n"
            f" gerealiseerd eigen {s['eigen']:+.2f} vs op hun prijzen {s['hun']:+.2f}\n"
            f" grootste verlies 1 trade {s['worst']:+.2f}")
    pot_tot = cfg.pot * len(cfg.traders)
    t = _stats(rows, shadow)
    days = (now_ms - start_ms) / 86_400_000
    profit = tot_eq - pot_tot
    cost_pt = t["cost"] / t["trades"] if t["trades"] else 0.0
    their_pt = t["hun"] / t["trades"] if t["trades"] else 0.0
    go = {"≥ 14 dagen": days >= 14, "≥ 100 trades": t["trades"] >= 100, "totaal positief na kosten": profit > 0,
          "slippage + fees < ½ hun winst/trade": their_pt > 0 and cost_pt < their_pt / 2}
    lines.append(
        f"\nTOTAAL\n winst {profit:+.2f} ({profit / pot_tot * 100 if pot_tot else 0:+.1f}%), trades {t['trades']}\n"
        f" gem. vertraging {t['delay']:.1f} s, gem. slippage {t['slip']:+.3f}%\n"
        f" gemist: te klein {t['te_klein']}, niet op Kraken {t['niet_kraken']}, te laat {t['te_laat']}\n"
        f" gerealiseerd eigen {t['eigen']:+.2f} vs op hun prijzen {t['hun']:+.2f}\n"
        f" grootste verlies 1 trade {t['worst']:+.2f}\n"
        f"\nGO-check (dag {days:.1f}; kosten/trade {cost_pt:.3f} vs hun winst/trade {their_pt:.3f})")
    lines += [f" {k}: {'ja' if v else 'nee'}" for k, v in go.items()]
    lines.append(f" => {'GO' if all(go.values()) else 'nog geen GO'}")
    return "\n".join(lines), go
