"""Dagrapport en GO-check. Bedragen in de munt van het potje (Kraken rekent in USD; geen wisselkoers).
Trade = positie van plat tot weer plat (of richtingwissel), zoals in context/kopieertraders-methode.md."""

from __future__ import annotations

from statistics import mean

from .config import short

CLOSES = ("sluiten", "sluiten (stop)")
DAY = 86_400_000


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def round_trips(rows: list[dict]) -> list[float]:
    """Resultaat per afgeronde trade: alle uitgevoerde regels van openen t/m sluiten per (trader, munt)."""
    open_, out = {}, []
    for r in rows:
        if r["status"] != "uitgevoerd":
            continue
        key = (r["trader"], r["munt"])
        open_[key] = open_.get(key, 0.0) + _f(r["resultaat"])
        if r["actie"] in CLOSES:
            out.append(open_.pop(key))
    return out


def _stats(rows: list[dict], shadow: list[dict]) -> dict:
    done = [r for r in rows if r["status"] == "uitgevoerd"]
    small = [r for r in rows if r["status"] == "te klein"]
    own_rt, their_rt = round_trips(rows), round_trips(shadow)
    return {"trades": len(own_rt), "orders": len(done),
            "delay": mean(_f(r["vertraging_s"]) for r in done) if done else 0.0,
            "slip": mean(_f(r["slippage_pct"]) for r in done) if done else 0.0,
            "cost": sum(_f(r["slippage_eur"]) + _f(r["fee"]) for r in done),
            "klein_open": sum(r["actie"] in ("openen", "wissel") for r in small),
            "klein_rest": sum(r["actie"] not in ("openen", "wissel") for r in small),
            "min_pot": max((_f(r["min_potje"]) for r in small if r.get("min_potje")), default=0.0),
            "niet_kraken": sum(r["status"] == "niet op Kraken" for r in rows),
            "te_laat": sum(r["status"] == "te laat" for r in rows),
            "max_lev": sum(r["status"] == "max hefboom" for r in rows),
            "eigen": sum(own_rt), "hun": sum(their_rt),
            "worst": min(own_rt, default=0.0)}


def _block(s: dict, pot: float) -> str:
    mp = f", minimaal potje {s['min_pot']:.0f} (nu {pot:.0f})" if s["min_pot"] > pot else ""
    return (f" trades {s['trades']} (orders {s['orders']}), gem. vertraging {s['delay']:.1f} s, "
            f"gem. slippage {s['slip']:+.3f}%\n"
            f" winst op hun prijzen {s['hun']:+.2f} | eigen na vertraging en kosten {s['eigen']:+.2f}\n"
            f" te klein: {s['klein_open']} openingen + {s['klein_rest']} bij/af{mp}\n"
            f" niet op Kraken {s['niet_kraken']}, te laat {s['te_laat']}, max hefboom {s['max_lev']}\n"
            f" grootste verlies 1 trade {s['worst']:+.2f}")


def build(cfg, copier, book, rows: list[dict], shadow: list[dict], start_ms: int, now_ms: int,
          title: str = "Dagrapport", accounts: dict | None = None) -> tuple[str, dict]:
    """accounts: {adres: accountwaarde trader in $} voor de vervangregel (optioneel)."""
    lines, tot_eq = [title], 0.0
    for addr in cfg.traders:
        sid = short(addr)
        s = _stats([r for r in rows if r["trader"] == sid], [r for r in shadow if r["trader"] == sid])
        eq = copier.equity(addr, book)
        tot_eq += eq
        tr = copier.state["traders"][addr]
        quiet = (now_ms - tr.get("last_signal", start_ms)) / DAY
        av = (accounts or {}).get(addr)
        swap = [w for w, hit in (("7 dagen geen trade", quiet >= 7), ("account < $100", av is not None and av < 100),
                                 ("potje -20%", tr["paused"])) if hit]
        lines.append(f"\n{sid} K={cfg.traders[addr]}{' (GEPAUZEERD)' if tr['paused'] else ''}\n"
                     f" potje {eq:.2f}: {eq - cfg.pot:+.2f} ({(eq / cfg.pot - 1) * 100:+.1f}%) incl. open posities\n"
                     + _block(s, cfg.pot)
                     + (f"\n VERVANGEN? {', '.join(swap)}" if swap else ""))
    pot_tot = cfg.pot * len(cfg.traders)
    t = _stats(rows, shadow)
    days = (now_ms - start_ms) / DAY
    profit = tot_eq - pot_tot
    cost_pt = t["cost"] / t["trades"] if t["trades"] else 0.0
    their_pt = t["hun"] / t["trades"] if t["trades"] else 0.0
    go = {"≥ 14 dagen": days >= 14, "≥ 100 trades": t["trades"] >= 100, "totaal positief na kosten": profit > 0,
          "slippage + fees < ½ hun winst/trade": their_pt > 0 and cost_pt < their_pt / 2}
    lines.append(f"\nTOTAAL\n potjes {tot_eq:.2f}: {profit:+.2f} ({profit / pot_tot * 100 if pot_tot else 0:+.1f}%)\n"
                 + _block(t, cfg.pot)
                 + f"\n\nGO-check (dag {days:.1f}; slippage+fees per trade {cost_pt:.3f} "
                   f"vs hun winst per trade {their_pt:.3f})")
    lines += [f" {k}: {'ja' if v else 'nee'}" for k, v in go.items()]
    lines.append(f" => {'GO' if all(go.values()) else 'nog geen GO'}")
    return "\n".join(lines), go
