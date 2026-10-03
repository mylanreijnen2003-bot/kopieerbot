"""Meldingen via ntfy. Zonder NTFY_TOPIC alleen naar de log."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("kopieerbot")


class Notifier:
    def __init__(self, topic: str, enabled: bool = True):
        self.topic, self.enabled = topic, enabled and bool(topic)

    def send(self, text: str, title: str = "Kopieerbot", priority: str = "default") -> None:
        log.info("MELDING %s: %s", title, text.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            requests.post(f"https://ntfy.sh/{self.topic}", data=text.encode("utf-8"), timeout=15,
                          headers={"Title": title.encode("utf-8"), "Priority": priority})
        except requests.RequestException as exc:
            log.warning("ntfy faalt: %s", exc)


def trade_text(r: dict) -> str:
    slip = f" ({float(r['slippage_pct']):+.3f}%)" if r["slippage_pct"] != "" else ""
    return (f"{r['trader']} {r['munt']} {r['richting']} {r['actie']}\n"
            f"trader {float(r['prijs_trader']):.6g} vs eigen {float(r['eigen_prijs']):.6g}{slip}\n"
            f"vertraging {float(r['vertraging_s']):.1f} s, inzet {float(r['inzet']):.2f}, "
            f"resultaat {float(r['resultaat']):+.2f}")
