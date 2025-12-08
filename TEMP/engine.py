# bot/wallets/engine.py

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Iterable, Optional, List

from .models import (
    ProfitSplitRule,
    TradeRiskDecision,
    TradeRiskRequest,
    WalletConfig,
    WalletFlowsConfig,
    WalletRole,
    WalletState,
)

logger = logging.getLogger(__name__)


class WalletFlowsEngine:
    """
    Moteur centralisé qui gère les wallets logiques W0–W9 :

      - état de chaque wallet (balance, PnL du jour, etc.),
      - limites par wallet (min_balance, max_risk_pct_per_trade, max_daily_loss_pct),
      - hooks périodiques pour compounding, auto-fees, profit splits (M4).

    Version "full" pour M4 :
      - Auto-fees : prélèvement automatique d'une fraction du PnL réalisé
        des wallets de trading vers un wallet de fees (ex: W4),
        bornée par min/max d'auto_fees_pct.
      - Profit split : routage d'une partie des profits vers vault/profit_box/payout
        selon des ProfitSplitRule, avec suivi d'un baseline par wallet pour ne
        pas re-splitter plusieurs fois le même profit.

    ⚠️ Important :
      - Tous les transferts restent PUREMENT "paper" (mouvements internes entre
        WalletState.balance_usd, aucune interaction on-chain).
      - La logique est volontairement conservatrice (respect de min_balance_usd,
        clamp sur 100% de profit, etc.).
    """

    def __init__(
        self,
        wallet_configs: Iterable[WalletConfig],
        flows_config: WalletFlowsConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._flows_config = flows_config

        self._configs: Dict[str, WalletConfig] = {c.id: c for c in wallet_configs}
        if not self._configs:
            raise ValueError("WalletFlowsEngine: aucun wallet configuré.")

        # Etats runtime initialisés depuis les configs
        self._states: Dict[str, WalletState] = {
            wid: WalletState(id=wid, balance_usd=cfg.initial_balance_usd)
            for wid, cfg in self._configs.items()
        }

        # Baseline de profit par wallet pour les ProfitSplitRule
        self._profit_baseline: Dict[str, Decimal] = {
            wid: cfg.initial_balance_usd for wid, cfg in self._configs.items()
        }

        # Auto-fees : suivi, par jour, de ce qui a déjà été prélevé par wallet.
        self._auto_fees_charged_today: Dict[str, Decimal] = {}

        # Compounding global
        self._last_compound_at: Optional[date] = None

        self._logger.info(
            "WalletFlowsEngine initialisé avec %d wallets logiques: %s",
            len(self._states),
            list(self._states.keys()),
        )

    # ------------------------------------------------------------------
    # Accès lecture
    # ------------------------------------------------------------------

    @property
    def configs(self) -> Dict[str, WalletConfig]:
        return self._configs

    @property
    def states(self) -> Dict[str, WalletState]:
        return self._states

    def get_state(self, wallet_id: str) -> WalletState:
        return self._states[wallet_id]

    # ------------------------------------------------------------------
    # Maintenance journalière / périodique
    # ------------------------------------------------------------------

    def _ensure_daily_reset(self, now: datetime) -> None:
        """
        Reset des compteurs journaliers lorsque la date change.
        """
        today = now.date()
        any_reset = False

        for state in self._states.values():
            if state.last_reset_date != today:
                any_reset = True
                self._logger.info(
                    "wallet.daily_reset",
                    extra={
                        "wallet_id": state.id,
                        "prev_date": state.last_reset_date.isoformat(),
                    },
                )
                state.last_reset_date = today
                state.realized_pnl_today_usd = Decimal("0")
                state.fees_paid_today_usd = Decimal("0")
                state.gross_pnl_today_usd = Decimal("0")
                state.consecutive_losing_trades = 0

        # Nouveau jour => on remet à zéro le suivi auto-fees
        if any_reset:
            self._auto_fees_charged_today.clear()

    def run_periodic_tasks(self, now: Optional[datetime] = None) -> None:
        """
        À appeler régulièrement par le runtime (ex : toutes les X secondes).

        Gère :
          - reset journalier,
          - hooks compounding,
          - auto-fees,
          - profit splits (M4).
        """
        now = now or datetime.utcnow()
        self._ensure_daily_reset(now)
        self._maybe_compound(now)
        # Cycle financier global (auto-fees + profit splits + policy fees)
        self.run_finance_cycle_all()

    # ------------------------------------------------------------------
    # Décision de prise de position
    # ------------------------------------------------------------------

    def evaluate_trade_request(self, req: TradeRiskRequest) -> TradeRiskDecision:
        """
        Vérifie si un trade est acceptable pour un wallet donné.
        """
        self._ensure_daily_reset(req.timestamp)

        if req.wallet_id not in self._states:
            return TradeRiskDecision(
                approved=False,
                max_allowed_notional_usd=Decimal("0"),
                reason=f"Wallet inconnu: {req.wallet_id}",
            )

        cfg = self._configs[req.wallet_id]
        state = self._states[req.wallet_id]

        # 1) Solde minimum
        if state.balance_usd <= cfg.min_balance_usd:
            return TradeRiskDecision(
                approved=False,
                max_allowed_notional_usd=Decimal("0"),
                reason="Solde en dessous du minimum autorisé",
            )

        # 2) Taille max autorisée en fonction du % de risque par trade
        max_notional = (state.balance_usd * cfg.max_risk_pct_per_trade) / Decimal("100")

        if max_notional <= Decimal("0"):
            return TradeRiskDecision(
                approved=False,
                max_allowed_notional_usd=Decimal("0"),
                reason="Taille max autorisée nulle (check max_risk_pct_per_trade)",
            )

        # 3) Limite de perte journalière pour ce wallet
        if cfg.max_daily_loss_pct is not None and state.gross_pnl_today_usd < Decimal("0"):
            max_daily_loss_value = (
                state.balance_usd * cfg.max_daily_loss_pct / Decimal("100")
            )
            if abs(state.gross_pnl_today_usd) >= max_daily_loss_value:
                return TradeRiskDecision(
                    approved=False,
                    max_allowed_notional_usd=Decimal("0"),
                    reason="Perte journalière max atteinte pour ce wallet",
                )

        approved = req.requested_notional_usd <= max_notional
        allowed = min(req.requested_notional_usd, max_notional)

        return TradeRiskDecision(
            approved=approved,
            max_allowed_notional_usd=allowed,
            reason=None if approved else "Taille demandée > taille autorisée pour ce wallet",
        )

    # ------------------------------------------------------------------
    # Mise à jour après exécution (PnL, fees, etc.)
    # ------------------------------------------------------------------

    def apply_realized_pnl(
        self,
        wallet_id: str,
        realized_pnl_usd: Decimal,
        fees_paid_usd: Decimal = Decimal("0"),
    ) -> None:
        """
        Helper compat : wrapper autour de register_fill().

        Utilisé par RuntimeWalletManager.on_trade_closed(...) et
        par certains scripts de test (ex: test_finance_profit_split).
        """
        self.register_fill(
            wallet_id=wallet_id,
            realized_pnl_usd=realized_pnl_usd,
            fees_paid_usd=fees_paid_usd,
        )

    def register_fill(
        self,
        wallet_id: str,
        realized_pnl_usd: Decimal,
        fees_paid_usd: Decimal = Decimal("0"),
    ) -> None:
        """
        A appeler par l'ExecutionEngine (ou un adapter) après la clôture d'une position.
        """
        if wallet_id not in self._states:
            self._logger.warning(
                "register_fill ignoré: wallet inconnu %s (pnl=%.2f, fees=%.2f)",
                wallet_id,
                float(realized_pnl_usd),
                float(fees_paid_usd),
            )
            return

        state = self._states[wallet_id]

        net_pnl = realized_pnl_usd - fees_paid_usd
        state.balance_usd += net_pnl
        state.realized_pnl_today_usd += realized_pnl_usd
        state.fees_paid_today_usd += fees_paid_usd
        state.gross_pnl_today_usd += net_pnl

        if realized_pnl_usd < 0:
            state.consecutive_losing_trades += 1
        elif realized_pnl_usd > 0:
            state.consecutive_losing_trades = 0

        self._logger.info(
            "wallet.register_fill",
            extra={
                "wallet_id": wallet_id,
                "realized_pnl_usd": f"{realized_pnl_usd:.2f}",
                "fees_paid_usd": f"{fees_paid_usd:.2f}",
                "new_balance_usd": f"{state.balance_usd:.2f}",
            },
        )

        # Hooks financiers après mise à jour du PnL :
        self.run_finance_cycle_for_wallet(wallet_id)

    # ------------------------------------------------------------------
    # Hooks compounding & auto-fees / profit-splits (M4)
    # ------------------------------------------------------------------

    def run_finance_cycle_all(self) -> None:
        """
        Applique le cycle financier M4 sur l'ensemble des wallets :

          - auto-fees global (wallets de trading -> wallet de fees),
          - profit splits selon les ProfitSplitRule configurées,
          - policy fees (cap d'equity + sweep vers vault/profits).

        Ne gère PAS :
          - le reset journalier (voir _ensure_daily_reset),
          - le compounding (voir _maybe_compound).

        Ces responsabilités restent à la charge du caller (ex: run_periodic_tasks
        ou un FinanceEngine dédié).
        """
        self._rebalance_auto_fees()
        self._apply_profit_splits_all()
        self._apply_fees_policy()

    def run_finance_cycle_for_wallet(self, wallet_id: str) -> None:
        """
        Applique le cycle financier M4 suite à un événement sur un wallet donné.

        Comportement :
          - auto-fees reste global (les fees dépendent du PnL journalier
            de tous les wallets de trading),
          - le profit split est ciblé sur le wallet concerné.

        Typiquement appelé après la clôture d'un trade via register_fill().
        """
        self._rebalance_auto_fees()
        self._apply_profit_splits_for_wallet(wallet_id)
        self._apply_fees_policy()

    def _maybe_compound(self, now: datetime) -> None:
        """
        Hook de compounding global (stub logué).
        """
        if not self._flows_config.compounding_enabled:
            return

        interval_days = max(int(self._flows_config.compounding_interval_days), 1)
        today = now.date()

        if self._last_compound_at is not None:
            delta = (today - self._last_compound_at).days
            if delta < interval_days:
                return

        self._logger.debug(
            "WalletFlowsEngine._maybe_compound() — compounding stub. "
            "interval_days=%d, last_compound_at=%s",
            interval_days,
            self._last_compound_at.isoformat() if self._last_compound_at else "None",
        )
        self._last_compound_at = today

    def _rebalance_auto_fees(self) -> None:
        """
        Auto-fees "full" M4, mais conservateur.

        Principe :
          - pour chaque wallet de trading autorisant les outflows
            (cfg.allow_outflows=True) et ayant un PnL réalisé positif sur la
            journée, on prélève une fraction target_pct du realized_pnl_today_usd
            vers auto_fees_wallet_id (ex: "fees"),
          - on respecte min_balance_usd pour ne jamais vider un wallet.
        """
        auto_wallet_id = self._flows_config.auto_fees_wallet_id
        if not auto_wallet_id:
            return
        if auto_wallet_id not in self._states:
            self._logger.warning(
                "WalletFlowsEngine._rebalance_auto_fees(): auto_fees_wallet_id=%s introuvable",
                auto_wallet_id,
            )
            return

        min_pct = self._flows_config.min_auto_fees_pct
        max_pct = self._flows_config.max_auto_fees_pct

        if min_pct < Decimal("0"):
            min_pct = Decimal("0")
        if max_pct < min_pct:
            max_pct = min_pct

        # On prend la médiane de [min, max] comme target (conservateur)
        target_pct = (min_pct + max_pct) / Decimal("2")

        if target_pct <= Decimal("0"):
            return

        for wid, state in self._states.items():
            # On ne prélève jamais sur le wallet de fees lui-même
            if wid == auto_wallet_id:
                continue

            cfg = self._configs[wid]

            # ⚠️ Important : on ne filtre plus par rôle, seulement par allow_outflows.
            # Cela permet de supporter les rôles de config actuels ("SCALPING",
            # "MAIN", etc.) tout en excluant les coffres/vaults configurés avec
            # allow_outflows=False.
            if not cfg.allow_outflows:
                continue

            # Autofees uniquement si PnL réalisé positif sur la journée
            if state.realized_pnl_today_usd <= Decimal("0"):
                continue

            # Total idéal de fees à prélever sur la journée pour ce wallet
            ideal_total = (
                state.realized_pnl_today_usd * target_pct / Decimal("100")
            )
            already = self._auto_fees_charged_today.get(wid, Decimal("0"))
            remaining = ideal_total - already

            if remaining <= Decimal("0"):
                continue

            # On ne prélève que dans la limite du surplus vs min_balance
            surplus = state.balance_usd - cfg.min_balance_usd
            if surplus <= Decimal("0"):
                continue

            amount = min(remaining, surplus)
            if amount <= Decimal("0"):
                continue

            transferred = self._transfer(
                source_wallet_id=wid,
                target_wallet_id=auto_wallet_id,
                amount_usd=amount,
                reason="auto_fees",
            )
            if transferred <= Decimal("0"):
                continue

            self._auto_fees_charged_today[wid] = already + transferred

        self._logger.debug(
            "WalletFlowsEngine._rebalance_auto_fees() — auto-fees appliqué. "
            "auto_fees_wallet_id=%s, target_pct=%s, rules=%d",
            auto_wallet_id,
            str(target_pct),
            len(self._flows_config.profit_split_rules or []),
        )

    def _apply_fees_policy(self) -> None:
        """
        Applique la policy du wallet de fees (cap d'equity + sweep) si configurée.

        - Si fees_max_equity_pct est défini et que le wallet de fees dépasse ce
          pourcentage de l'equity totale, on transfère l'excès vers
          fees_over_cap_target_wallet_id (ex: "vault").
        """
        fees_wallet_id = self._flows_config.auto_fees_wallet_id
        if not fees_wallet_id:
            return
        if fees_wallet_id not in self._states:
            return

        max_pct = self._flows_config.fees_max_equity_pct
        if max_pct is None or max_pct <= Decimal("0"):
            return

        target_wallet_id = self._flows_config.fees_over_cap_target_wallet_id
        if not target_wallet_id or target_wallet_id not in self._states:
            # Pas de cible de sweep valide : on se contente de logguer
            self._logger.debug(
                "WalletFlowsEngine._apply_fees_policy() — fees_over_cap configuré sans cible valide.",
            )
            return

        total_equity = sum(s.balance_usd for s in self._states.values())
        if total_equity <= Decimal("0"):
            return

        fees_state = self._states[fees_wallet_id]
        fees_balance = fees_state.balance_usd
        current_pct = fees_balance / total_equity

        if current_pct <= max_pct:
            return

        # On ramène le wallet de fees au cap max_pct
        cap_amount = (total_equity * max_pct)
        excess = fees_balance - cap_amount
        if excess <= Decimal("0"):
            return

        transferred = self._transfer(
            source_wallet_id=fees_wallet_id,
            target_wallet_id=target_wallet_id,
            amount_usd=excess,
            reason="fees_over_cap",
        )

        if transferred > Decimal("0"):
            self._logger.info(
                "WalletFlowsEngine._apply_fees_policy() — sweep fees_over_cap",
                extra={
                    "fees_wallet_id": fees_wallet_id,
                    "target_wallet_id": target_wallet_id,
                    "transferred_usd": f"{transferred:.2f}",
                    "max_equity_pct": str(max_pct),
                },
            )

    # ------------------------------------------------------------------
    # Profit splits (M4)
    # ------------------------------------------------------------------

    def _apply_profit_splits_all(self) -> None:
        """
        Applique les ProfitSplitRule pour tous les wallets.
        """
        rules = self._flows_config.profit_split_rules or []
        if not rules:
            return

        rules_by_source: Dict[str, List[ProfitSplitRule]] = {}

        for rule in rules:
            if rule.source_wallet_id not in self._states:
                self._logger.warning(
                    "ProfitSplitRule ignorée: source_wallet_id=%s introuvable",
                    rule.source_wallet_id,
                )
                continue
            if rule.target_wallet_id not in self._states:
                self._logger.warning(
                    "ProfitSplitRule ignorée: target_wallet_id=%s introuvable",
                    rule.target_wallet_id,
                )
                continue
            rules_by_source.setdefault(rule.source_wallet_id, []).append(rule)

        for source_wallet_id, rule_list in rules_by_source.items():
            self._apply_profit_splits_for_wallet(source_wallet_id, rule_list)

    def _apply_profit_splits_for_wallet(
        self,
        wallet_id: str,
        rules: Optional[Iterable[ProfitSplitRule]] = None,
    ) -> None:
        """
        Applique les règles de profit split pour un wallet donné.
        """
        if wallet_id not in self._states:
            return

        cfg = self._configs[wallet_id]
        state = self._states[wallet_id]

        if not cfg.allow_outflows:
            return

        if rules is None:
            rules = [
                r
                for r in (self._flows_config.profit_split_rules or [])
                if r.source_wallet_id == wallet_id
                and r.target_wallet_id in self._states
            ]

        rule_list = list(rules)
        if not rule_list:
            return

        base = self._profit_baseline.get(wallet_id, cfg.initial_balance_usd)
        current_balance = state.balance_usd

        profit_since_base = current_balance - base
        if profit_since_base <= Decimal("0"):
            return

        if base > Decimal("0"):
            profit_pct = (profit_since_base / base) * Decimal("100")
        else:
            profit_pct = Decimal("0")

        eligible_rules: List[ProfitSplitRule] = [
            r for r in rule_list if profit_pct >= r.trigger_pct
        ]
        if not eligible_rules:
            return

        sum_pct = sum(
            max(Decimal("0"), r.percent_of_profit) for r in eligible_rules
        )
        if sum_pct <= Decimal("0"):
            return

        scale = Decimal("1")
        if sum_pct > Decimal("100"):
            scale = Decimal("100") / sum_pct

        total_transferred = Decimal("0")

        for r in eligible_rules:
            rule_pct = max(Decimal("0"), r.percent_of_profit) * scale
            amount = profit_since_base * rule_pct / Decimal("100")
            if amount <= Decimal("0"):
                continue

            transferred = self._transfer(
                source_wallet_id=wallet_id,
                target_wallet_id=r.target_wallet_id,
                amount_usd=amount,
                reason=f"profit_split:{wallet_id}->{r.target_wallet_id}",
            )
            if transferred > Decimal("0"):
                total_transferred += transferred

        if total_transferred <= Decimal("0"):
            return

        self._profit_baseline[wallet_id] = base + profit_since_base

        self._logger.info(
            "wallet.profit_split",
            extra={
                "wallet_id": wallet_id,
                "profit_since_base_usd": f"{profit_since_base:.2f}",
                "profit_pct_since_base": f"{profit_pct:.2f}",
                "total_transferred_usd": f"{total_transferred:.2f}",
                "rules_applied": len(eligible_rules),
            },
        )

    # ------------------------------------------------------------------
    # Utils (transferts & snapshots)
    # ------------------------------------------------------------------

    def _transfer(
        self,
        source_wallet_id: str,
        target_wallet_id: str,
        amount_usd: Decimal,
        reason: str = "",
    ) -> Decimal:
        """
        Transfert interne sécurisé entre deux wallets logiques.
        """
        amount = Decimal(amount_usd)

        if amount <= Decimal("0"):
            return Decimal("0")

        if source_wallet_id == target_wallet_id:
            return Decimal("0")

        if source_wallet_id not in self._states or target_wallet_id not in self._states:
            self._logger.warning(
                "wallet.transfer ignoré: source=%s ou target=%s inconnu (amount=%.2f)",
                source_wallet_id,
                target_wallet_id,
                float(amount),
            )
            return Decimal("0")

        src_cfg = self._configs[source_wallet_id]
        if not src_cfg.allow_outflows:
            self._logger.warning(
                "wallet.transfer interdit: wallet source %s n'autorise pas les outflows.",
                source_wallet_id,
            )
            return Decimal("0")

        src_state = self._states[source_wallet_id]
        tgt_state = self._states[target_wallet_id]

        surplus = src_state.balance_usd - src_cfg.min_balance_usd
        if surplus <= Decimal("0"):
            self._logger.debug(
                "wallet.transfer impossible: aucun surplus sur %s "
                "(balance=%.2f, min_balance=%.2f)",
                source_wallet_id,
                float(src_state.balance_usd),
                float(src_cfg.min_balance_usd),
            )
            return Decimal("0")

        effective = min(amount, surplus)
        if effective <= Decimal("0"):
            return Decimal("0")

        src_state.balance_usd -= effective
        tgt_state.balance_usd += effective

        self._logger.info(
            "wallet.transfer",
            extra={
                "source_wallet_id": source_wallet_id,
                "target_wallet_id": target_wallet_id,
                "amount_usd": f"{effective:.2f}",
                "reason": reason or "n/a",
                "source_balance_after": f"{src_state.balance_usd:.2f}",
                "target_balance_after": f"{tgt_state.balance_usd:.2f}",
            },
        )

        return effective

    def debug_snapshot(self) -> Dict[str, Dict[str, str]]:
        """
        Retourne un snapshot lisible des wallets pour debug / monitoring.
        """
        snapshot: Dict[str, Dict[str, str]] = {}
        for wid, state in self._states.items():
            snapshot[wid] = {
                "balance_usd": f"{state.balance_usd:.2f}",
                "realized_pnl_today_usd": f"{state.realized_pnl_today_usd:.2f}",
                "gross_pnl_today_usd": f"{state.gross_pnl_today_usd:.2f}",
                "fees_paid_today_usd": f"{state.fees_paid_today_usd:.2f}",
                "consecutive_losing_trades": str(state.consecutive_losing_trades),
                "last_reset_date": state.last_reset_date.isoformat(),
            }
        return snapshot
