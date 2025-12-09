# bot/wallets/models.py

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

# ============================================================================
# Enums
# ============================================================================


class WalletRole(str, Enum):
    """
    Rôle logique d'un wallet W0–W9.

    On colle au mapping métier défini dans la spec :

    - W0 : vault
    - W1/W2 : trade_memecoins
    - W3 : copy_trading
    - W4 : fees
    - W5 : profit_box
    - W6 : stables
    - W7 : emergency
    - W8 : sandbox
    - W9 : payout
    """

    VAULT = "vault"
    TRADE_MEMECOINS = "trade_memecoins"
    COPY_TRADING = "copy_trading"
    FEES = "fees"
    PROFIT_BOX = "profit_box"
    STABLES = "stables"
    EMERGENCY = "emergency"
    SANDBOX = "sandbox"
    PAYOUT = "payout"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "WalletRole":  # type: ignore[override]
        """
        Mapping tolérant des valeurs venant de config.json.

        - "SCALPING" / "scalping"  -> TRADE_MEMECOINS (wallets de trading memecoins)
        - "PROFITS" / "profits"    -> PROFIT_BOX (boîte à profits)
        - Tout le reste            -> OTHER

        On continue donc à ne jamais lever de ValueError lors du parsing
        des rôles de wallets : en cas de valeur inconnue, on tombe sur OTHER.
        """
        if isinstance(value, str):
            v = value.strip().lower()

            alias_map = {
                "scalping": cls.TRADE_MEMECOINS,
                "profits": cls.PROFIT_BOX,
            }

            if v in alias_map:
                return alias_map[v]

        # fallback par défaut : rôle inconnu → OTHER
        return cls.OTHER


# ============================================================================
# Configs / règles de flux
# ============================================================================


@dataclass
class ProfitSplitRule:
    """
    Règle de repartition de profits entre wallets logiques.

    - source_wallet_id : ex "W1", "W2"
    - target_wallet_id : ex "W0" (vault), "W5" (profit_box)
    - trigger_pct : seuil de déclenchement (ex: 10% de gain)
    - percent_of_profit: % du profit à transférer une fois le seuil atteint
    """

    source_wallet_id: str
    target_wallet_id: str
    trigger_pct: Decimal
    percent_of_profit: Decimal


@dataclass
class WalletConfig:
    """
    Configuration statique d'un wallet logique (W0–W9) issue de
    config.json["wallets"]["definitions"].

    Les montants sont en USD notionnels.
    """

    id: str
    role: WalletRole
    chain: str
    base_ccy: str
    initial_balance_usd: Decimal = Decimal("0")
    min_balance_usd: Decimal = Decimal("0")
    # % du capital max à risquer par trade
    max_risk_pct_per_trade: Decimal = Decimal("1")
    # Perte journalière max en % du capital (None = pas de limite wallet-level)
    max_daily_loss_pct: Optional[Decimal] = None
    # Autorise-t-on ce wallet à envoyer des fonds vers d'autres wallets ?
    allow_outflows: bool = True
    # Ce wallet peut-il recevoir les flux d'auto-fees (W4 par ex) ?
    is_auto_fees_target: bool = False


@dataclass
class WalletFlowsConfig:
    """
    Configuration globale des flux entre wallets logiques.
    """

    auto_fees_wallet_id: Optional[str]
    # Intervalle min / max d'auto-fees en % (indicatif pour plus tard).
    min_auto_fees_pct: Decimal
    max_auto_fees_pct: Decimal

    # Compounding global
    compounding_enabled: bool = True
    compounding_interval_days: int = 3

    # Règles de repartition (profit box, vault, payout, etc.)
    profit_split_rules: List[ProfitSplitRule] = field(default_factory=list)

    # --- Nouveau : policy du wallet de fees ---
    # Buffer minimal conseillé (nominal en USD). Utilisé pour l'alerting/UI.
    fees_min_buffer_usd: Decimal = Decimal("0")
    # Cap max du wallet de fees en % de l'equity totale (0.10 = 10 %)
    fees_max_equity_pct: Optional[Decimal] = None
    # Cible de sweep quand le cap est dépassé (ex: "vault")
    fees_over_cap_target_wallet_id: Optional[str] = None


# ============================================================================
# Runtime state + risk request/decision
# ============================================================================


@dataclass
class WalletState:
    """
    Etat runtime d'un wallet logique.

    - balance_usd : capital courant (paper)
    - realized_pnl_today_usd : PnL réalisé aujourd'hui
    - gross_pnl_today_usd : PnL brut (après fees)
    """

    id: str
    balance_usd: Decimal
    last_reset_date: date = field(default_factory=lambda: date.today())
    realized_pnl_today_usd: Decimal = Decimal("0")
    gross_pnl_today_usd: Decimal = Decimal("0")
    fees_paid_today_usd: Decimal = Decimal("0")
    consecutive_losing_trades: int = 0


@dataclass
class TradeRiskRequest:
    """
    Requête de validation d'un trade pour un wallet logique.

    - wallet_id : ex "W1"
    - symbol : marché concerné (optionnel, pour l'extension per-market)
    - requested_notional_usd : taille souhaitée en USD
    - timestamp : horodatage pour gestion du reset journalier
    """

    wallet_id: str
    requested_notional_usd: Decimal
    timestamp: datetime
    symbol: Optional[str] = None


@dataclass
class TradeRiskDecision:
    """
    Réponse du moteur de wallets pour un trade donné.

    - approved : True si OK
    - max_allowed_notional_usd : taille max autorisée
      (peut être < requested_notional_usd)
    - reason : raison de refus / réduction (ou None si OK)
    """

    approved: bool
    max_allowed_notional_usd: Decimal
    reason: Optional[str] = None


__all__ = [
    "WalletRole",
    "ProfitSplitRule",
    "WalletConfig",
    "WalletFlowsConfig",
    "WalletState",
    "TradeRiskRequest",
    "TradeRiskDecision",
]

