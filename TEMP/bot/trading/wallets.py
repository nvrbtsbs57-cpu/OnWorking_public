# bot/trading/wallets.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass
class WalletState:
    address: str
    label: str = ""
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    alert_on_activity: bool = True

    tx_count: int = 0
    total_notional_usd: Decimal = Decimal("0")
    last_seen_ts: Optional[str] = None
    last_chain: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # JSON friendly pour Decimal
        d["total_notional_usd"] = float(self.total_notional_usd)
        return d


class WalletManager:
    """
    Gestion des wallets surveillés (watchlist).

    - persistance JSON (data/godmode/wallets.json)
    - MAJ des stats à chaque event pertinent
    - envoi d'alertes via AlertEngine si activity détectée
    """

    def __init__(
        self,
        path: str,
        alert_engine: Optional[Any] = None,
        autosave: bool = True,
    ) -> None:
        self.path = path
        self.alert_engine = alert_engine
        self.autosave = autosave

        self._wallets: Dict[str, WalletState] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # API publique                                                       #
    # ------------------------------------------------------------------ #

    def list_wallets(self) -> List[Dict[str, Any]]:
        """Retourne les wallets en dict (pour API / debug)."""
        return [w.to_dict() for w in self._wallets.values()]

    def add_wallet(
        self,
        address: str,
        label: str = "",
        tags: Optional[List[str]] = None,
        alert_on_activity: bool = True,
        enabled: bool = True,
    ) -> WalletState:
        addr = self._norm(address)
        w = self._wallets.get(addr)
        if w is None:
            w = WalletState(
                address=addr,
                label=label or addr,
                tags=tags or [],
                enabled=enabled,
                alert_on_activity=alert_on_activity,
            )
            self._wallets[addr] = w
        else:
            # mise à jour éventuelle du label / tags
            if label:
                w.label = label
            if tags:
                w.tags = tags
            w.alert_on_activity = alert_on_activity
            w.enabled = enabled

        if self.autosave:
            self._save()
        return w

    def process_event(
        self,
        *,
        chain: str,
        tx_hash: str,
        from_addr: Optional[str],
        to_addr: Optional[str],
        token: Optional[str],
        notional_usd: Decimal,
        raw_event: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Appelé par AgentEngine à chaque event pertinent.
        Met à jour les wallets s'ils sont dans la watchlist
        et envoie des alertes si nécessaire.
        """
        involved = set()

        if from_addr:
            involved.add(self._norm(from_addr))
        if to_addr:
            involved.add(self._norm(to_addr))

        for addr in involved:
            w = self._wallets.get(addr)
            if not w or not w.enabled:
                continue

            w.tx_count += 1
            w.total_notional_usd += notional_usd
            w.last_seen_ts = (raw_event or {}).get("ts") or (raw_event or {}).get(
                "timestamp"
            )
            w.last_chain = chain

            if self.alert_engine is not None and w.alert_on_activity:
                try:
                    msg = (
                        f"Activité sur wallet surveillé {w.label} ({w.address}) "
                        f"sur {chain}: ~{float(notional_usd):,.0f} USD "
                        f"{token or ''} — tx={tx_hash}"
                    )
                    self.alert_engine.info(
                        msg,
                        source="wallet_manager",
                        wallet_address=w.address,
                        wallet_label=w.label,
                        chain=chain,
                        token=token,
                        notional_usd=float(notional_usd),
                        tx_hash=tx_hash,
                        tags=list(w.tags),
                    )
                except Exception:
                    # on ne laisse jamais tomber à cause d'une alerte
                    pass

        if self.autosave:
            self._save()

    # ------------------------------------------------------------------ #
    # Persistance                                                        #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not os.path.exists(self.path):
            # pas grave si le fichier n'existe pas encore
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        wallets = data.get("wallets") if isinstance(data, dict) else data
        if not isinstance(wallets, list):
            return

        for w in wallets:
            if not isinstance(w, dict):
                continue
            addr = self._norm(w.get("address", ""))
            if not addr:
                continue
            state = WalletState(
                address=addr,
                label=w.get("label", addr),
                tags=w.get("tags", []) or [],
                enabled=bool(w.get("enabled", True)),
                alert_on_activity=bool(w.get("alert_on_activity", True)),
                tx_count=int(w.get("tx_count", 0)),
                total_notional_usd=Decimal(str(w.get("total_notional_usd", "0"))),
                last_seen_ts=w.get("last_seen_ts"),
                last_chain=w.get("last_chain"),
            )
            self._wallets[addr] = state

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "wallets": [w.to_dict() for w in self._wallets.values()],
        }
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception:
            # pas d'exception qui remonte dans le bot
            pass

    # ------------------------------------------------------------------ #
    # Utils                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _norm(addr: Optional[str]) -> str:
        if not addr:
            return ""
        addr = addr.strip()
        return addr.lower()
