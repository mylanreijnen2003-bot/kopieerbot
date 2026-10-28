"""F3-8 overlay op groep G: vol-factor, tegen-candle, 5m-instap, funding, muntkoppeling en de G-selectie.
Handgemaakte voorbeelden; regels in docs/prereg-vol-tegenbar.md."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from bt import rot3
from overlay.kern import (
    funding_r,
    g_selectie,
    instap_eerste_5m,
    instap_tegen,
    koppel,
    perioden_rendement,
    schaal,
    sigma_dag,
    sigma_ref,
    vol_factor,
)

M5 = 300_000
DAG = 86_400_000
T0 = pd.Timestamp("2026-01-10").value // 10**6


def kaarsen(rijen, start=T0):
    """rijen = [(open, close), ...] vanaf `start` (ms), elke 5 minuten."""
    idx = [start + i * M5 for i in range(len(rijen))]
    return pd.DataFrame(rijen, index=idx, columns=["open", "close"], dtype=float)


# 1. K0 = rotatie3-G ------------------------------------------------------------------

def nep_stats(seed=0):
    rng = np.random.default_rng(seed)
    rij = []
    for T in rot3.TS[:6]:
        for a in range(40):
            rij.append({"address": f"0x{a:040x}", "T": T, "L": 84, "fills_per_dag": 10, "K": 2, "kraken_pct": 90,
                        "dagen_sinds_laatste": 1, "per_maand_pct": rng.uniform(0.1, 5),
                        "top3_aandeel": rng.uniform(0, 0.7), "winstweken_pct": rng.uniform(40, 90),
                        "cons_maand_pct": rng.uniform(-1, 4), "houdtijd_uur": 5, "gem_r_pct": 0.5,
                        "v14_pct": rng.normal(1, 5)})
    return pd.DataFrame(rij)


def test_g_selectie_geeft_zelfde_uitkomst_als_rot3():
    s = nep_stats()
    verwacht, df = rot3.simuleer(s, "basis", "consistent", "buffer", 84)
    held = g_selectie(s)
    assert len(held) == len(df)
    rets = [np.mean([s[(s["T"] == T) & (s.address == a)].v14_pct.iloc[0] for a in hs]) for T, hs in held.items()]
    assert np.allclose(rets, df.top_pct.to_numpy())
    assert perioden_rendement(rets)["per_maand_pct"] == pytest.approx(verwacht["per_maand_pct"], abs=0.005)


STATS = os.environ.get("VT_STATS")


@pytest.mark.skipif(not STATS, reason="echte r3-stats niet opgegeven (VT_STATS)")
def test_k0_reproduceert_rotatie3_g_binnen_0_3_pp():
    import glob
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{STATS}/**/r3_*.parquet", recursive=True)])
    r, _ = rot3.simuleer(s, "basis", "consistent", "buffer", 84)
    assert abs(r["per_maand_pct"] - 5.69) <= 0.3


# 2. Vol-factor -------------------------------------------------------------------------

def test_vol_factor_clip():
    assert vol_factor(0.04, 0.02) == pytest.approx(2.0)
    assert vol_factor(0.04, 0.01) == pytest.approx(2.0)     # 4 -> geknipt op 2
    assert vol_factor(0.01, 0.04) == pytest.approx(0.5)     # 0,25 -> 0,5
    assert vol_factor(0.03, 0.04) == pytest.approx(0.75)
    assert vol_factor(0.03, np.nan) == pytest.approx(1.0)   # geen sigma -> 1


def test_sigma_dag_alleen_dagen_tot_en_met_t_min_1():
    dagen = pd.date_range("2026-01-01", periods=60, freq="D")
    rng = np.random.default_rng(1)
    slot = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 60))), index=dagen)
    t = pd.Timestamp("2026-02-10 13:00").value // 10**6
    s = sigma_dag(slot, t)
    # dagrendementen van 2026-01-11 .. 2026-02-09 (30 stuks), slot t-31 .. t-1
    r = slot.pct_change().loc["2026-01-11":"2026-02-09"]
    assert len(r) == 30 and s == pytest.approx(r.std(ddof=1))
    anders = slot.copy()
    anders.loc["2026-02-10":] *= 50                           # dag t en later mogen niets uitmaken
    assert sigma_dag(anders, t) == pytest.approx(s)
    assert np.isnan(sigma_dag(slot.loc[:"2026-01-15"], t))    # te weinig dagen


def test_sigma_ref_alleen_84_dagen_voor_de_herbalans():
    T = pd.Timestamp("2026-03-01").value // 10**6
    tr = pd.DataFrame({"open_t": [T - 90 * DAG, T - 80 * DAG, T - 10 * DAG, T + DAG],
                       "sigma": [9.0, 0.02, 0.04, 7.0]})
    assert sigma_ref(tr, T) == pytest.approx(0.03)            # alleen de twee binnen [T-84, T)


# 3. Tegen-candle ---------------------------------------------------------------------------

def test_tegen_candle_long():
    t = T0 + 60_000                                           # fill 1 min na de start van candle 0
    k = kaarsen([(10, 9), (10, 11), (11, 12), (12, 11.5), (11.6, 11.8), (11.8, 12), (12, 12.5), (12.4, 13)])
    # candle 0 begint vóór t en telt niet; candle 3 (12 -> 11,5) is de eerste tegen-candle: instap open candle 4
    tijd, prijs = instap_tegen(k, t, richting=1, exit_t=T0 + DAG)
    assert tijd == T0 + 4 * M5 and prijs == pytest.approx(11.6)


def test_tegen_candle_short():
    t = T0 - 1
    k = kaarsen([(10, 9), (9, 9.5), (9.5, 9.6), (9.6, 9.4)])
    tijd, prijs = instap_tegen(k, t, richting=-1, exit_t=T0 + DAG)
    assert tijd == T0 + 2 * M5 and prijs == pytest.approx(9.5)    # candle 1 (9 -> 9,5) gaat tegen een short in


def test_tegen_candle_niet_binnen_6_of_trader_eerder_dicht():
    t = T0 - 1
    k = kaarsen([(10, 11)] * 6 + [(11, 10), (10, 10)])         # tegen-candle pas als 7e
    assert instap_tegen(k, t, richting=1, exit_t=T0 + DAG) == "geen tegen-candle"
    k2 = kaarsen([(10, 11), (11, 12), (12, 11), (11, 11)])
    assert instap_tegen(k2, t, richting=1, exit_t=T0 + 2 * M5) == "trader eerder dicht"
    assert instap_tegen(k2, t, richting=1, exit_t=T0 + 3 * M5) == "trader eerder dicht"   # instap = exit-moment
    assert instap_tegen(k2, t, richting=1, exit_t=T0 + 3 * M5 + 1)[0] == T0 + 3 * M5


# 4. K0-B / K1-B ---------------------------------------------------------------------------------

def test_instap_eerste_5m_na_t():
    k = kaarsen([(10, 11), (11, 12), (12, 13)])
    assert instap_eerste_5m(k, T0 + 1, exit_t=T0 + DAG) == (T0 + M5, 11.0)
    assert instap_eerste_5m(k, T0 - 1, exit_t=T0 + DAG) == (T0, 10.0)
    assert instap_eerste_5m(k, T0 + 1, exit_t=T0 + M5) == "trader eerder dicht"


# 5. Funding --------------------------------------------------------------------------------------

def test_funding_teken_long_en_short():
    uur = 3_600_000
    rates = pd.Series([0.0001, 0.0001, 0.0001, -0.0002, 0.0005], index=[T0 + i * uur for i in range(5)])
    # instap T0 + 10 min, exit T0 + 3 u 10 min: tijdstippen 1, 2, 3 tellen (0,0001 + 0,0001 - 0,0002)
    assert funding_r(rates, T0 + 600_000, T0 + 3 * uur + 600_000, richting=1) == pytest.approx(0.0)
    assert funding_r(rates, T0, T0 + 3 * uur, richting=1) == pytest.approx(-0.0003)     # long betaalt
    assert funding_r(rates, T0, T0 + 3 * uur, richting=-1) == pytest.approx(0.0003)     # short ontvangt
    assert funding_r(None, T0, T0 + 3 * uur, richting=1) == 0.0


# 6. Muntkoppeling --------------------------------------------------------------------------------

def test_koppeling():
    bestaat = {"1000PEPEUSDT", "BTCUSDT", "1000SATSUSDT"}.__contains__
    assert koppel("kPEPE", bestaat) == "1000PEPEUSDT"
    assert koppel("BTC", bestaat) == "BTCUSDT"
    assert koppel("SATS", bestaat) == "1000SATSUSDT"
    assert koppel("HYPE", bestaat) is None                     # geen perp -> skip "geen 5m"
    assert schaal(0.0123, 0.0124) == pytest.approx(1.0)
    assert schaal(0.0000123, 0.0124) == pytest.approx(0.001)   # 1000-contract
    assert schaal(1.0, 1.6) is None                            # > 25 % verschil: geen goede koppeling
