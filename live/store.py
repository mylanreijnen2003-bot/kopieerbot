"""CSV-logs en toestand op schijf (live/data/, staat in .gitignore)."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .engine import FIELDS


class Store:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.own = self.folder / "kopie_trades.csv"
        self.shadow = self.folder / "trader_prijzen.csv"
        self.state_file = self.folder / "state.json"

    def append(self, rows: list[dict], shadow: list[dict]) -> None:
        for path, rs in ((self.own, rows), (self.shadow, shadow)):
            if not rs:
                continue
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerows(rs)

    def read(self, which: str = "own") -> list[dict]:
        path = self.own if which == "own" else self.shadow
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def load_state(self) -> dict | None:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return None

    def save_state(self, state: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_file)
