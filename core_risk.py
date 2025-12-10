from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from decimal import Decimal
from typing import Dict, Tuple, Optional, Protocol, Any, Sequence
import logging

logger = logging.getLogger(__name__)


# ======================================================================
#   Décision de risque
# ======================================================================


class RiskDecision(str, Enum):
    """
    Décision du moteur de risque pour un ordre donné :

    - ACCEPT : OK, on peut exécuter tel quel
    - ADJUST : OK mais avec une taille réduite
    - REJECT : ordre refusé pour ce wallet
    - EJECT  : arrêt global / circuit breaker (perte globale trop forte)
    """

    ACCEPT = "accept"
    ADJUST = "adjust"
    REJECT = "reject"
    EJECT = "eject"


# ======================================================================
#   Interface de métriques runtime (wallets)
# ======================================================================


class WalletMetricsIface(Protocol):
    """
    Interface vue par le RiskEngine pour lire l'état global des wallets.

    Implémentée par RuntimeWalletManager (bot.wallets.runtime_manager.RuntimeWalletManager).
    """

    def get_total_equity_usd(self) -> Decimal:
        ...

    def get_global_pnl_today_usd(self) -> Decimal:
        ...


# ======================================================================
#   Modèles de config (issus de config["risk"])
# ======================================================================


@dataclass
class WalletRiskConfig:
    """
    Configuration de risque spécifique à un wallet (sniper_sol, base_main, etc.).

    Les valeurs sont exprimées en % du capital du wallet ou en caps absolus.
    """

    # % de la balance du wallet max par trade
    max_pct_balance_per_trade: float = 2.0

    # perte journalière max en % sur ce wallet
    max_daily_loss_pct: float = 5.0

    # nb max de positions ouvertes
    max_open_positions: int = 10

    # 0 = pas de limite, sinon cap absolu (USD) sur le notional par asset
    max_notional_per_asset: float = 0.0


@dataclass
class GlobalRiskConfig:
    """
    Limites globales tous wallets confondus.
    """

    enabled: bool = True
    max_global_daily_loss_pct: float = 10.0  # perte journalière max globale (en %)
    max_consecutive_losing_trades: int = 5   # série max de trades perdants (globales ou par wallet)


@dataclass
class RiskConfig:
    """
    Config globale du moteur de risque.

    Structure attendue depuis config.json :

    "risk": {
      "global": {
        "enabled": true,
        "max_global_daily_loss_pct": 10.0,
        "max_consecutive_losing_trades": 5
      },
      "wallets": {
        "sniper_sol": {
          "max_pct_balance_per_trade": 2.0,
          "max_daily_loss_pct": 5.0,
          "max_open_positions": 20,
          "max_notional_per_asset": 0.0
        },
        ...
      }
    }
    """

    global_cfg: GlobalRiskConfig = field(default_factory=GlobalRiskConfig)
    wallets: Dict[str, WalletRiskConfig] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction depuis dict (config.json["risk"])
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict) -> "RiskConfig":
        if data is None:
            data = {}

        global_data = data.get("global", {}) or {}
        wallets_data = data.get("wallets", {}) or {}

        global_cfg = GlobalRiskConfig(
            enabled=bool(global_data.get("enabled", True)),
            max_global_daily_loss_pct=float(
                global_data.get("max_global_daily_loss_pct", 10.0)
            ),
            max_consecutive_losing_trades=int(
                global_data.get("max_consecutive_losing_trades", 5)
            ),
        )

        # Defaults sûrs issus de la dataclass WalletRiskConfig
        default_wallet_cfg = WalletRiskConfig()

        wallets_cfg: Dict[str, WalletRiskConfig] = {}
        for wid, wcfg in wallets_data.items():
            if not isinstance(wcfg, dict):
                continue

            wallets_cfg[wid] = WalletRiskConfig(
                max_pct_balance_per_trade=float(
                    wcfg.get(
                        "max_pct_balance_per_trade",
                        default_wallet_cfg.max_pct_balance_per_trade,
                    )
                ),
                max_daily_loss_pct=float(
                    wcfg.get(
                        "max_daily_loss_pct",
                        default_wallet_cfg.max_daily_loss_pct,
                    )
                ),
                max_open_positions=int(
                    wcfg.get(
                        "max_open_positions",
                        default_wallet_cfg.max_open_positions,
                    )
                ),
                max_notional_per_asset=float(
                    wcfg.get(
                        "max_notional_per_asset",
                        default_wallet_cfg.max_notional_per_asset,
                    )
                ),
            )

        return cls(global_cfg=global_cfg, wallets=wallets_cfg)

    # ------------------------------------------------------------------
    # Variante "ajustée" selon le SafetyMode (SAFE / NORMAL / DEGEN)
    # ------------------------------------------------------------------
    def adjusted_for_safety(self, safety_mode: str) -> "RiskConfig":
        """
        Retourne une copie de la config ajustée selon SAFETY_MODE.

        - SAFE   : plus conservateur (limites divisées)
        - NORMAL : tel quel
        - DEGEN  : plus agressif (limites augmentées)
        """
        mode = str(safety_mode or "").upper()
        if mode not in ("SAFE", "NORMAL", "DEGEN"):
            mode = "NORMAL"

        if mode == "NORMAL":
            return self

        factor_wallet = 1.0
        factor_global = 1.0

        if mode == "SAFE":
            factor_wallet = 0.5
            factor_global = 0.5
        elif mode == "DEGEN":
            factor_wallet = 1.5
            factor_global = 1.5

        # Clone global config
        new_global = replace(
            self.global_cfg,
            max_global_daily_loss_pct=(
                self.global_cfg.max_global_daily_loss_pct * factor_global
            ),
        )

        # Clone wallet configs
        new_wallets: Dict[str, WalletRiskConfig] = {}
        for wid, wcfg in self.wallets.items():
            new_wallets[wid] = WalletRiskConfig(
                max_pct_balance_per_trade=(
                    wcfg.max_pct_balance_per_trade * factor_wallet
                ),
                max_daily_loss_pct=wcfg.max_daily_loss_pct * factor_wallet,
                max_open_positions=wcfg.max_open_positions,
                max_notional_per_asset=wcfg.max_notional_per_asset,
            )

        return RiskConfig(global_cfg=new_global, wallets=new_wallets)


# ======================================================================
#   Contexte d'évaluation d'un ordre
# ======================================================================


@dataclass
class OrderRiskContext:
    """
    Contexte d'un ordre envoyé au moteur de risque.

    Champs typiques (tous en USD / % / compteurs déjà calculés par ailleurs).

    NB: la structure est alignée avec les tests :
    - scripts/test_risk_engine.py
    - scripts/test_risk_engine_basic.py
    - scripts/test_execution_risk_adapter.py
    """

    wallet_id: str                # ex: "sniper_sol", "base_main"
    symbol: str                   # ex: "ETHUSDT"
    side: str                     # "buy"/"sell"/"long"/"short"
    notional_usd: float           # taille demandée en USD

    wallet_equity_usd: float      # capital approximatif du wallet
    open_positions: int           # nb de positions ouvertes sur ce wallet
    wallet_daily_pnl_pct: float   # PnL du jour en % pour ce wallet
    global_daily_pnl_pct: float   # PnL global du jour en %
    consecutive_losing_trades: int  # série de trades perdants (global ou par wallet)


# ======================================================================
#   Moteur de risque
# ======================================================================


class RiskEngine:
    """
    Moteur principal de risk management.

    - Lit la config globale + par wallet (RiskConfig).
    - (Optionnel) consomme des métriques runtime via WalletMetricsIface
      pour apply_global_limits().
    - Donne pour chaque ordre une décision (ACCEPT / ADJUST / REJECT / EJECT).

    Expose en plus quelques états lisibles pour le dashboard / execution_runtime.json :
      - daily_drawdown_pct : drawdown global de la journée (>= 0, en %).
      - soft_stop_active   : seuil "warning" atteint (zone de prudence).
      - hard_stop_active   : seuil critique / hard-stop atteint.
    """

    def __init__(
        self,
        config: RiskConfig,
        wallet_metrics: Optional[WalletMetricsIface] = None,
    ) -> None:
        self.config = config
        self._wallet_metrics = wallet_metrics

        # Circuit breaker global : une fois `_ejected` à True, on EJECT tout le
        # reste de la journée (sauf reset explicite).
        self._ejected: bool = False

        # Exposition "lisible" pour le dashboard / execution_runtime.json
        self.daily_drawdown_pct: Optional[float] = None
        self.soft_stop_active: bool = False
        self.hard_stop_active: bool = False

        # Raison globale la plus récente (pour logs / debug / dashboard)
        self._last_global_reason: Optional[str] = None

        logger.info(
            "[RISK] RiskEngine initialisé — global_enabled=%s "
            "max_global_daily_loss_pct=%.4f max_consecutive_losing_trades=%d",
            self.config.global_cfg.enabled,
            self.config.global_cfg.max_global_daily_loss_pct,
            self.config.global_cfg.max_consecutive_losing_trades,
        )

    # ------------------------------------------------------------------ #
    # Wiring runtime
    # ------------------------------------------------------------------ #

    def set_wallet_metrics(self, wallet_metrics: WalletMetricsIface) -> None:
        """
        Injecte l'adaptateur de métriques runtime (RuntimeWalletStats / RuntimeWalletManager).
        """
        self._wallet_metrics = wallet_metrics

    # ------------------------------------------------------------------ #
    # Helpers internes : drawdown & flags soft/hard stop
    # ------------------------------------------------------------------ #

    def _update_drawdown_state(self, global_daily_pnl_pct: float) -> None:
        """
        Met à jour :
          - daily_drawdown_pct (>= 0),
          - soft_stop_active,
          - hard_stop_active.

        global_daily_pnl_pct est le PnL global de la journée en %, positif si gain,
        négatif si perte.
        """
        try:
            pnl_pct = float(global_daily_pnl_pct)
        except Exception:
            # On ne casse jamais le flux si les métriques sont bizarres.
            logger.debug(
                "[RISK] _update_drawdown_state: impossible de caster pnl_pct=%r",
                global_daily_pnl_pct,
            )
            return

        # Drawdown = -min(0, pnl_pct), en positif
        dd = max(0.0, -pnl_pct)
        self.daily_drawdown_pct = dd

        max_loss = float(self.config.global_cfg.max_global_daily_loss_pct or 0.0)
        if max_loss <= 0.0:
            # Pas de limites globales configurées : pas de soft/hard stop.
            self.soft_stop_active = False
            # Mais si on a déjà ejected manuellement, on le reflète quand même.
            self.hard_stop_active = self._ejected
            return

        # Hard stop si :
        #  - on a déjà déclenché un GLOBAL_DRAWDOWN (self._ejected)
        #  - OU drawdown >= max_loss configuré
        self.hard_stop_active = self._ejected or (dd >= max_loss)

        # Soft stop = zone de warning, par défaut à 50% de la limite.
        soft_threshold = max_loss * 0.5
        self.soft_stop_active = (dd >= soft_threshold) and not self.hard_stop_active

    # ------------------------------------------------------------------ #
    # Interface runtime M2/M3 : filtre global sur liste de signaux
    # ------------------------------------------------------------------ #

    def apply_global_limits(
        self, signals: Sequence[Any], safety_mode: Any
    ) -> Sequence[Any]:
        """
        Interface utilisée par le runtime (BotRuntime).

        - signals    : séquence de signaux générés par les stratégies
        - safety_mode: SafetyMode global (SAFE / NORMAL / DEGEN) — pour l'instant
                       utilisé seulement en logging.

        M2/M3 : on applique ici un **circuit breaker global** sur la perte
        journalière, basé sur les métriques fournies par wallet_metrics
        (RuntimeWalletManager).

        Si la perte dépasse max_global_daily_loss_pct, on :

          - déclenche un EJECT global,
          - drop tous les signaux,
          - continue à dropper tant que le runtime tourne (jusqu'à reboot / reset).
        """
        signals = list(signals)
        n = len(signals)

        if n == 0:
            return signals

        # Si on a déjà déclenché le circuit breaker, on drop tout.
        if self._ejected:
            logger.warning(
                "[RISK] apply_global_limits() — déjà en état EJECT, drop de %d signaux.",
                n,
            )
            return []

        global_cfg = self.config.global_cfg

        # Si global risk est désactivé ou qu'on n'a pas encore de métriques wallets,
        # on laisse passer pour l'instant.
        if not global_cfg.enabled or self._wallet_metrics is None:
            logger.debug(
                "[RISK] apply_global_limits() — pass-through (enabled=%s, has_wallet_metrics=%s), "
                "safety_mode=%s, n_signals=%d",
                global_cfg.enabled,
                self._wallet_metrics is not None,
                str(safety_mode),
                n,
            )
            return signals

        # Récup des métriques globales depuis les wallets (RuntimeWalletManager)
        try:
            total_equity = self._wallet_metrics.get_total_equity_usd()
            pnl_today = self._wallet_metrics.get_global_pnl_today_usd()
        except Exception:
            logger.exception(
                "[RISK] apply_global_limits() — erreur lors de la récupération "
                "des métriques wallets, pass-through pour ne pas bloquer."
            )
            return signals

        # Equity de début de journée approx = equity_now - pnl_today
        base_equity_today = total_equity - pnl_today
        if base_equity_today <= Decimal("0"):
            # Plus de marge pour trader => circuit breaker global.
            reason = (
                f"Base equity today <= 0 (equity={total_equity}, "
                f"pnl_today={pnl_today}), circuit breaker global déclenché."
            )
            logger.warning("[RISK] EJECT (apply_global_limits): %s", reason)
            self._ejected = True
            self._last_global_reason = reason
            # drawdown = 100% dans ce scénario extrême
            self._update_drawdown_state(-100.0)
            return []

        # PnL global du jour en %
        pnl_pct = (pnl_today / base_equity_today) * Decimal("100")
        self._update_drawdown_state(float(pnl_pct))

        logger.debug(
            "[RISK] apply_global_limits() — safety_mode=%s, equity=%.2f, "
            "pnl_today=%.2f (%.2f %%), n_signals=%d",
            str(safety_mode),
            float(total_equity),
            float(pnl_today),
            float(pnl_pct),
            n,
        )

        max_loss_pct = Decimal(str(global_cfg.max_global_daily_loss_pct))

        # Circuit breaker global basé sur la perte journalière en %
        if max_loss_pct > 0 and pnl_pct <= -abs(max_loss_pct):
            reason = (
                f"Global daily loss {pnl_pct:.2f}% <= "
                f"-max_global_daily_loss_pct={max_loss_pct:.2f}%, "
                "circuit breaker global déclenché."
            )
            logger.warning(
                "[RISK] EJECT (apply_global_limits): %s — drop de %d signaux.",
                reason,
                n,
            )
            self._ejected = True
            self._last_global_reason = reason
            return []

        # TODO (M10+): on pourra ici appliquer d'autres règles globales :
        #   - scaling des tailles en fonction du drawdown,
        #   - caps par stratégie, etc.

        return signals

    def on_tick(self) -> None:
        """
        Hook appelé à chaque tick du runtime.

        Utile plus tard pour :
        - mettre à jour des compteurs,
        - gérer des fenêtres glissantes,
        - recalculer des stats journalières, etc.

        En M10, on ne fait encore rien ici.
        """
        return None

    # ------------------------------------------------------------------
    # Evaluation par ordre (M10, par wallet)
    # ------------------------------------------------------------------

    def evaluate_order(self, ctx: OrderRiskContext) -> Tuple[RiskDecision, float, str]:
        """
        Evalue un ordre et renvoie (decision, size_usd, reason).

        - decision : RiskDecision
        - size_usd : taille (notional) acceptée par le moteur (peut être < ctx.notional_usd)
        - reason   : explication courte

        Cette méthode est utilisée par :
          - les tests (test_risk_engine*.py),
          - ExecutionRiskAdapter (avant l'exécution d'un ordre).
        """
        # Maintien des états de drawdown / soft/hard stop pour le dashboard
        self._update_drawdown_state(ctx.global_daily_pnl_pct)

        # 0) Global risk désactivé => pass-through
        if not self.config.global_cfg.enabled:
            logger.debug("[RISK] Global risk disabled => ACCEPT direct.")
            return RiskDecision.ACCEPT, ctx.notional_usd, "Global risk disabled"

        # 1) Circuit breaker global sur la perte journalière (EJECT)
        if self.config.global_cfg.max_global_daily_loss_pct > 0:
            if ctx.global_daily_pnl_pct <= -abs(
                self.config.global_cfg.max_global_daily_loss_pct
            ):
                reason = (
                    f"Global daily loss {ctx.global_daily_pnl_pct:.2f}% "
                    f"<= -max_global_daily_loss_pct="
                    f"{self.config.global_cfg.max_global_daily_loss_pct:.2f}%"
                )
                logger.warning("[RISK] EJECT: %s", reason)
                self._ejected = True
                self._last_global_reason = reason
                return RiskDecision.EJECT, 0.0, reason

        # 2) Circuit sur série de trades perdants (ADJUST)
        if self.config.global_cfg.max_consecutive_losing_trades > 0:
            if (
                ctx.consecutive_losing_trades
                >= self.config.global_cfg.max_consecutive_losing_trades
            ):
                # On réduit la taille de moitié pour ne pas tout couper brutalement
                adjusted_size = ctx.notional_usd * 0.5
                reason = (
                    f"Consecutive losing trades={ctx.consecutive_losing_trades} "
                    f">= max={self.config.global_cfg.max_consecutive_losing_trades}, "
                    "size halved."
                )
                logger.warning("[RISK] ADJUST: %s", reason)
                return RiskDecision.ADJUST, adjusted_size, reason

        # 3) Récup du config wallet
        wcfg = self.config.wallets.get(ctx.wallet_id)
        if wcfg is None:
            # Pas de config pour ce wallet => on accepte, mais on log.
            logger.warning(
                "[RISK] Wallet '%s' n'a pas de config de risque dédiée, ACCEPT fallback.",
                ctx.wallet_id,
            )
            return (
                RiskDecision.ACCEPT,
                ctx.notional_usd,
                "No wallet config, fallback ACCEPT",
            )

        # 4) Check perte journalière par wallet (REJECT)
        if wcfg.max_daily_loss_pct > 0:
            if ctx.wallet_daily_pnl_pct <= -abs(wcfg.max_daily_loss_pct):
                reason = (
                    f"Wallet {ctx.wallet_id} daily loss {ctx.wallet_daily_pnl_pct:.2f}% "
                    f"<= -max_daily_loss_pct={wcfg.max_daily_loss_pct:.2f}%"
                )
                logger.warning("[RISK] REJECT: %s", reason)
                return RiskDecision.REJECT, 0.0, reason

        # 5) Check nombre de positions ouvertes (REJECT)
        if wcfg.max_open_positions > 0:
            if ctx.open_positions >= wcfg.max_open_positions:
                reason = (
                    f"Wallet {ctx.wallet_id} open_positions={ctx.open_positions} "
                    f">= max_open_positions={wcfg.max_open_positions}"
                )
                logger.warning("[RISK] REJECT: %s", reason)
                return RiskDecision.REJECT, 0.0, reason

        # 6) Check taille par rapport à la balance (% du capital) (ADJUST)
        if ctx.wallet_equity_usd > 0 and wcfg.max_pct_balance_per_trade > 0:
            pct = (ctx.notional_usd / ctx.wallet_equity_usd) * 100.0
            if pct > wcfg.max_pct_balance_per_trade:
                # On réduit à la taille max autorisée
                allowed_notional = (
                    wcfg.max_pct_balance_per_trade / 100.0
                ) * ctx.wallet_equity_usd
                reason = (
                    f"Order {pct:.2f}% of equity > "
                    f"max_pct_balance_per_trade={wcfg.max_pct_balance_per_trade:.2f}%, "
                    "size adjusted."
                )
                logger.warning("[RISK] ADJUST: %s", reason)
                return RiskDecision.ADJUST, allowed_notional, reason

        # 7) Cap absolu par asset si défini (ADJUST)
        if wcfg.max_notional_per_asset > 0 and ctx.notional_usd > wcfg.max_notional_per_asset:
            reason = (
                f"Order notional {ctx.notional_usd:.2f} > asset cap "
                f"{wcfg.max_notional_per_asset:.2f}, size adjusted."
            )
            logger.warning("[RISK] ADJUST: %s", reason)
            return RiskDecision.ADJUST, wcfg.max_notional_per_asset, reason

        # 8) Tout est OK
        reason = "OK"
        logger.debug(
            "[RISK] ACCEPT: wallet=%s symbol=%s side=%s size=%.2f",
            ctx.wallet_id,
            ctx.symbol,
            ctx.side,
            ctx.notional_usd,
        )
        return RiskDecision.ACCEPT, ctx.notional_usd, reason

