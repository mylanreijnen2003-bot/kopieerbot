"""Signaallogica met nep-fills. Geen netwerk."""

import pytest

from live.coins import map_coin
from live.config import Config
from live.engine import Copier
from live.signals import Grouper, group_history

A = "0x" + "a" * 40
INSTR = {"PF_XBTUSD": {"step": 0.0001, "contract": 1}, "PF_ETHUSD": {"step": 0.001, "contract": 1},
         "PF_PEPEUSD": {"step": 1000.0, "contract": 1}, "PF_DOGEUSD": {"step": 1.0, "contract": 1}}


class Book:
    def __init__(self, px):
        self.px = dict(px)

    def quote(self, sym):
        p = self.px.get(sym)
        return (p * 0.9995, p * 1.0005) if p else None   # spread 10 bp

    def mid(self, sym):
        return self.px.get(sym)


def fill(coin, start, signed, px, t, tid=None):
    return {"coin": coin, "start": start, "signed": signed, "after": start + signed, "px": px,
            "time": t, "tid": tid or t, "kind": "perp"}


def run(copier, book, fills, t0=1_000_000):
    rows = []
    for s in group_history({A: fills}):
        r, _, _, _ = copier.process(s, book, s.t_last + 2000)
        rows += r
    return rows


@pytest.fixture
def cop():
    return Copier(Config(traders={A: 4}, pot=100.0, max_leverage=2.0, stop_pct=-20.0), INSTR)


def test_schone_start_negeert_bestaande_positie(cop):
    book = Book({"PF_ETHUSD": 2000})
    rows = run(cop, book, [fill("ETH", 5, 1, 2000, 1_000), fill("ETH", 6, -6, 2000, 10_000),
                           fill("ETH", 0, 2, 2000, 20_000)])
    assert [r["actie"] for r in rows] == ["openen"]            # pas na plat wordt gekopieerd
    assert 23 < rows[0]["inzet"] <= 25           # potje / K = 100 / 4, afgerond op contractstap


def test_groepeert_fills_binnen_1_5_s():
    g = Grouper()
    g.add(A, fill("ETH", 0, 1, 2000, 1000), 1000)
    g.add(A, fill("ETH", 1, 1, 2010, 2000), 2000)
    assert g.flush(3000) == []
    (s,) = g.flush(3600)
    assert (s.start, s.after, s.n, s.t_last) == (0, 2, 2, 2000)
    assert s.px == pytest.approx(2005)


def test_fills_zelfde_ms_op_positieketen():
    # echte situatie: tid niet chronologisch binnen dezelfde milliseconde
    g = [fill("SUI", 224, -224, 1.18, 5000, tid=1), fill("SUI", 369.3, -145.3, 1.18, 5000, tid=2)]
    (s,) = group_history({A: g})
    assert (s.start, s.after) == (369.3, 0)


def test_bijkopen_en_afbouwen_naar_verhouding(cop):
    book = Book({"PF_ETHUSD": 2000})
    rows = run(cop, book, [fill("ETH", 0, 10, 2000, 1_000), fill("ETH", 10, 10, 2000, 10_000),
                           fill("ETH", 20, -15, 2000, 20_000), fill("ETH", 5, -5, 2000, 30_000)])
    assert [r["actie"] for r in rows] == ["openen", "bijkopen", "afbouwen", "sluiten"]
    q0 = rows[0]["hoeveelheid"]
    assert rows[1]["hoeveelheid"] == pytest.approx(q0, abs=0.001)          # trader verdubbelt -> wij ook
    assert rows[2]["hoeveelheid"] == pytest.approx(2 * q0 * 0.75, abs=0.002)  # 75% afgebouwd
    assert not cop.state["traders"][A]["pos"]


def test_blootstelling_max_2x(cop):
    book = Book({"PF_ETHUSD": 2000})
    rows = run(cop, book, [fill("ETH", 0, 1, 2000, 1_000), fill("ETH", 1, 99, 2000, 10_000)])
    assert cop.gross(A, book) <= 2 * cop.equity(A, book) + 1e-6
    assert rows[1]["status"] == "uitgevoerd"


def test_richtingwissel(cop):
    book = Book({"PF_ETHUSD": 2000})
    rows = run(cop, book, [fill("ETH", 0, 1, 2000, 1_000), fill("ETH", 1, -3, 2000, 10_000)])
    assert [(r["actie"], r["richting"]) for r in rows] == [("openen", "long"), ("sluiten", "long"),
                                                           ("wissel", "short")]
    assert cop.state["traders"][A]["pos"]["ETH"]["units"] < 0


def test_wissel_bij_bestaande_positie_activeert_kopie(cop):
    book = Book({"PF_ETHUSD": 2000})
    rows = run(cop, book, [fill("ETH", 4, -6, 2000, 1_000)])
    assert [r["actie"] for r in rows] == ["wissel"]


def test_kpepe_mapping():
    assert map_coin("kPEPE", INSTR) == ("PF_PEPEUSD", 1000.0)
    assert map_coin("BTC", INSTR) == ("PF_XBTUSD", 1.0)
    assert map_coin("FOO", INSTR) is None


def test_kpepe_hoeveelheid_x1000(cop):
    book = Book({"PF_PEPEUSD": 0.00001})                           # 1 kPEPE = 0,01
    rows = run(cop, book, [fill("kPEPE", 0, 500, 0.01, 1_000)])
    r = rows[0]
    assert r["symbool"] == "PF_PEPEUSD" and r["prijs_trader"] == pytest.approx(0.00001)
    assert r["hoeveelheid"] % 1000 == 0 and r["inzet"] == pytest.approx(25, rel=0.05)
    assert cop.state["traders"][A]["pos"]["kPEPE"]["ratio"] == pytest.approx(r["hoeveelheid"] / 500_000)


def test_te_klein(cop):
    book = Book({"PF_XBTUSD": 1_000_000})                         # 25 / 1M = 0,000025 < 0,0001
    rows = run(cop, book, [fill("BTC", 0, 1, 1_000_000, 1_000)])
    assert rows[0]["status"] == "te klein" and rows[0]["fee"] == 0


def test_niet_op_kraken(cop):
    rows = run(cop, Book({}), [fill("FOO", 0, 1, 1, 1_000)])
    assert rows[0]["status"] == "niet op Kraken"


def test_hip3_en_spot_overslaan(cop):
    book = Book({"PF_ETHUSD": 2000})
    rows = run(cop, book, [fill("xyz:TSLA", 0, 1, 300, 1_000), fill("@107", 0, 1, 30, 1_000)])
    assert rows == []


def test_stop_min_20_pct_pauzeert(cop):
    book = Book({"PF_ETHUSD": 2000})
    run(cop, book, [fill("ETH", 0, 1, 2000, 1_000)])
    run(cop, book, [fill("DOGE", 0, 1, 0.1, 2_000)])
    book.px["PF_ETHUSD"] = 2000 * 0.15                            # long ETH (25) -85% => potje ~ -21%
    rows, _, alerts = cop.check_stop(A, book, 50_000)
    assert cop.state["traders"][A]["paused"] and alerts and "PAUZE" in alerts[0]
    assert {r["actie"] for r in rows} == {"sluiten (stop)"} and not cop.state["traders"][A]["pos"]
    later = run(cop, book, [fill("ETH", 0, 1, 300, 60_000)])
    assert later[0]["status"] == "gepauzeerd"


def test_schaduw_zonder_kosten(cop):
    book = Book({"PF_ETHUSD": 2000})
    sigs = group_history({A: [fill("ETH", 0, 1, 2000, 1_000), fill("ETH", 1, -1, 2000, 10_000)]})
    own, shadow = [], []
    for s in sigs:
        r, sh, _, _ = cop.process(s, book, s.t_last + 2000)
        own += r
        shadow += sh
    assert sum(r["resultaat"] for r in shadow) == pytest.approx(0)
    assert sum(r["resultaat"] for r in own) < 0                  # spread + fees
