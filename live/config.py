"""Instellingen uit .env (staat in .gitignore). Volledige adressen en topic nooit loggen: gebruik short()."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"

FEE = 0.0005              # taker-fee Kraken Futures per kant
GROUP_S = 1.5             # fills per (trader, munt) binnen 1,5 s = één signaal
MAX_OPEN_DELAY_S = 300    # openen na >5 min (bv. na herstart) = te laat; sluiten gebeurt altijd


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else addr


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    traders: dict[str, int] = field(default_factory=dict)   # adres (lowercase) -> K
    pot: float = 100.0
    ntfy_topic: str = ""
    max_leverage: float = 2.0
    stop_pct: float = -20.0


def load(env_file: Path | None = None) -> Config:
    _load_env(env_file or Path(os.environ.get("KOPIEERBOT_ENV", ROOT / ".env")))
    traders = {}
    for part in os.environ.get("TRADERS", "").split(","):
        part = part.strip()
        if not part:
            continue
        addr, _, k = part.partition(":")
        traders[addr.strip().lower()] = max(int(k or 1), 1)
    return Config(traders=traders,
                  pot=float(os.environ.get("POT_PER_TRADER") or 100),
                  ntfy_topic=os.environ.get("NTFY_TOPIC", "").strip(),
                  max_leverage=float(os.environ.get("MAX_LEVERAGE") or 2),
                  stop_pct=float(os.environ.get("TRADER_STOP_PCT") or -20))
