# bot/finance/pipeline.py

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, getcontext
from typing import Any, Dict, List, Optional, Mapping

from bot.core.logging import get_logger

getcontext().prec = 50
logger = get_logger(__name__)


# ======================================================================
# Dataclasses de base (snapshots & plans)
# ======================================================================


@dataclass
class WalletSnapshot:
    """
    Snapshot simple d'un wallet au moment où on planifie la finance.

    On reste volontairement générique :
    - balance_native : sol / eth / bnb... pour le gas / fees
    - balance_usd    : valeur totale approximative en USD (stables + tokens)
    - realized_profit_usd : profits réalisés (depuis le début ou dans une
      fenêtre) selon ta logique. Ce module ne fait qu'utiliser la valeur.
    """

    name: str
    chain: str
    role: Optional[str] = None
    balance_native: Decimal = Decimal("0")
    balance_usd: Decimal = Decimal("0")
    realized_profit_usd: Decimal = Decimal("0")
    tags: List[str] = field(default_factory=list)


@dataclass
class TransferPlan:
    """
    Plan de transfert "logique" — aucune exécution RPC ici.

    type:
      - "autofees"   : mouvements pour garantir du gas
      - "sweep"      : envoi d'une partie des profits vers savings/vault
      - "compound"   : redistribution du vault vers les wallets de trading
    """

    type: str
    from_wallet: Optional[str]
    to_wallet: str
    chain: Optional[str]
    amount_native: Decimal = Decimal("0")
    amount_usd: Decimal = Decimal("0")
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# ======================================================================
# Config Finance
# ======================================================================


@dataclass
class AutoFeesConfig:
    enabled: bool = True
    min_gas_native: Dict[str, Decimal] = field(default_factory=dict)
    target_gas_native: Dict[str, Decimal] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> AutoFeesConfig:
        enabled = bool(d.get("enabled", True))
        min_gas_raw = d.get("min_gas_native", {})
        target_gas_raw = d.get("target_gas_native", {})

        def _to_decimal_map(m: Mapping[str, Any]) -> Dict[str, Decimal]:
            out: Dict[str, Decimal] = {}
            for k, v in m.items():
                try:
                    out[str(k)] = Decimal(str(v))
                except Exception:
                    continue
            return out

        return AutoFeesConfig(
            enabled=enabled,
            min_gas_native=_to_decimal_map(min_gas_raw),
            target_gas_native=_to_decimal_map(target_gas_raw),
        )


@dataclass
class SweepConfig:
    enabled: bool = True
    min_profit_usd: Decimal = Decimal("50")
    sweep_pct: Decimal = Decimal("0.5")  # 50%

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> SweepConfig:
        enabled = bool(d.get("enabled", True))
        try:
            min_profit_usd = Decimal(str(d.get("min_profit_usd", "50")))
        except Exception:
            min_profit_usd = Decimal("50")
        try:
            sweep_pct = Decimal(str(d.get("sweep_pct", "0.5")))
        except Exception:
            sweep_pct = Decimal("0.5")
        return SweepConfig(
            enabled=enabled,
            min_profit_usd=min_profit_usd,
            sweep_pct=sweep_pct,
        )


@dataclass
class CompoundingConfig:
    enabled: bool = True
    compound_pct_from_vault: Decimal = Decimal("0.3")  # 30% du vault
    distribution_weights: Dict[str, Decimal] = field(default_factory=dict)
    # on ne compound que la partie du vault au-dessus de ce seuil
    vault_min_balance_usd: Decimal = Decimal("0")
    # cap optionnel en USD pour un cycle de compounding
    max_compound_usd_per_run: Optional[Decimal] = None

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CompoundingConfig":
        enabled = bool(d.get("enabled", True))

        # pct à prélever sur la partie "excess" du vault
        try:
            compound_pct = Decimal(str(d.get("compound_pct_from_vault", "0.3")))
        except Exception:
            compound_pct = Decimal("0.3")

        # distribution des poids par wallet (sniper_sol, copy_sol, base_main, ...)
        weights_raw = d.get("distribution", {}) or {}
        weights: Dict[str, Decimal] = {}
        total = Decimal("0")

        for name, raw in weights_raw.items():
            try:
                w = Decimal(str(raw))
            except Exception:
                logger.warning(
                    "CompoundingConfig: poids invalide '%s' pour '%s', ignoré.",
                    raw,
                    name,
                )
                continue
            if w <= 0:
                continue
            weights[name] = w
            total += w

        # normalisation pour que la somme des poids fasse 1
        if total > 0:
            for k in list(weights.keys()):
                weights[k] = (weights[k] / total).quantize(Decimal("0.0001"))

        # seuil mini du vault avant tout compounding
        try:
            vault_min_balance_usd = Decimal(str(d.get("vault_min_balance_usd", "0")))
        except Exception:
            vault_min_balance_usd = Decimal("0")

        # cap max en USD par run (optionnel)
        max_compound_usd_per_run_raw = d.get("max_compound_usd_per_run")
        max_compound_usd_per_run: Optional[Decimal]
        if max_compound_usd_per_run_raw is None:
            max_compound_usd_per_run = None
        else:
            try:
                max_compound_usd_per_run = Decimal(str(max_compound_usd_per_run_raw))
            except Exception:
                max_compound_usd_per_run = None

        return CompoundingConfig(
            enabled=enabled,
            compound_pct_from_vault=compound_pct,
            distribution_weights=weights,
            vault_min_balance_usd=vault_min_balance_usd,
            max_compound_usd_per_run=max_compound_usd_per_run,
        )


@dataclass
class FinanceConfig:
    """
    Config globale Finance, dérivée de config.json.

    On lit la section "finance", mais si elle n'existe pas on met des
    valeurs par défaut raisonnables.
    """

    autofees: AutoFeesConfig
    sweep: SweepConfig
    compounding: CompoundingConfig

    @staticmethod
    def from_global_config(global_cfg: Mapping[str, Any]) -> FinanceConfig:
        finance_raw: Mapping[str, Any] = global_cfg.get("finance", {}) or {}
        autofees_cfg = AutoFeesConfig.from_dict(finance_raw.get("autofees", {}))
        sweep_cfg = SweepConfig.from_dict(finance_raw.get("sweep", {}))
        comp_cfg = CompoundingConfig.from_dict(finance_raw.get("compounding", {}))
        return FinanceConfig(
            autofees=autofees_cfg,
            sweep=sweep_cfg,
            compounding=comp_cfg,
        )


# ======================================================================
# FinancePipeline : génère les plans AutoFees / Sweep / Compounding
# ======================================================================


class FinancePipeline:
    """
    Moteur de planning "Finance" (AutoFees, Sweep, Compounding).

    IMPORTANT :
    - cette classe NE fait AUCUNE requête RPC, AUCUNE écriture on-chain.
    - elle ne dépend ni de Web3 ni de solana rpc.
    - elle ne fait que générer des TransferPlan à partir de snapshots.

    Intégration typique :
      - tu récupères les balances/profits (via WalletManager + TradeStore)
      - tu construis des WalletSnapshot
      - tu appelles plan_*() ou plan_all()
      - et plus tard, un autre module exécutera ces plans on-chain.
    """

    def __init__(
        self,
        config: FinanceConfig,
        wallet_roles: Mapping[str, Any],
        wallets_cfg: List[Mapping[str, Any]],
    ) -> None:
        self.config = config
        self.wallet_roles = wallet_roles
        self.wallet_meta: Dict[str, Dict[str, Any]] = {}

        for w in wallets_cfg:
            name = str(w.get("name", "")).strip()
            if not name:
                continue
            self.wallet_meta[name] = {
                "chain": str(w.get("chain", "")).lower(),
                "role": str(w.get("role", "")).upper() or None,
                "tags": list(w.get("tags", [])) if isinstance(w.get("tags"), list) else [],
            }

        logger.info(
            "FinancePipeline initialisé (autofees=%s, sweep=%s, compounding=%s)",
            self.config.autofees.enabled,
            self.config.sweep.enabled,
            self.config.compounding.enabled,
        )

    # ------------------------------------------------------------------
    # Helpers sur la config wallets / roles
    # ------------------------------------------------------------------

    def _get_wallet_chain(self, wallet_name: str) -> Optional[str]:
        meta = self.wallet_meta.get(wallet_name)
        if not meta:
            return None
        return meta.get("chain")

    def _get_wallet_role(self, wallet_name: str) -> Optional[str]:
        meta = self.wallet_meta.get(wallet_name)
        if not meta:
            return None
        return meta.get("role")

    def _get_fees_wallet_for_chain(self, chain: str) -> Optional[str]:
        """
        Cherche le wallet de 'fees' pour une chain :

        - d'abord dans wallet_roles["fees"][chain]
        - sinon "evm" (utile pour eth/base/bsc), sinon "all"
        - sinon on cherche le 1er wallet avec role AUTO_FEES
        """
        chain = chain.lower()
        fees_roles = (
            self.wallet_roles.get("fees", {})
            if isinstance(self.wallet_roles, dict)
            else {}
        )
        if isinstance(fees_roles, dict):
            if chain in fees_roles:
                return fees_roles[chain]
            if "evm" in fees_roles and chain in (
                "ethereum",
                "base",
                "arb",
                "arbitrum",
                "bsc",
            ):
                return fees_roles["evm"]
            if "all" in fees_roles:
                return fees_roles["all"]

        # fallback : chercher un wallet dont le role = AUTO_FEES
        for name, meta in self.wallet_meta.items():
            if meta.get("role") == "AUTO_FEES":
                return name
        return None

    def _get_profits_wallet_for_chain(self, chain: str) -> Optional[str]:
        """
        Cherche le wallet "profits" pour une chain donnée.
        """
        chain = chain.lower()
        profits_roles = (
            self.wallet_roles.get("profits", {})
            if isinstance(self.wallet_roles, dict)
            else {}
        )
        if isinstance(profits_roles, dict):
            if chain in profits_roles:
                return profits_roles[chain]

        # fallback : aucun profits_* spécifique, on renvoit éventuellement le vault
        vault_roles = (
            self.wallet_roles.get("vault", {})
            if isinstance(self.wallet_roles, dict)
            else {}
        )
        if isinstance(vault_roles, dict):
            if "all" in vault_roles:
                return vault_roles["all"]
        return None

    def _get_vault_wallet(self) -> Optional[str]:
        vault_roles = (
            self.wallet_roles.get("vault", {})
            if isinstance(self.wallet_roles, dict)
            else {}
        )
        if isinstance(vault_roles, dict):
            if "all" in vault_roles:
                return vault_roles["all"]
        # fallback : 1er wallet taggé 'vault' ou 'savings'
        for name, meta in self.wallet_meta.items():
            tags = meta.get("tags") or []
            if isinstance(tags, list) and ("vault" in tags or "savings" in tags):
                return name
        return None

    # ------------------------------------------------------------------
    # AutoFees
    # ------------------------------------------------------------------

    def plan_autofees(
        self,
        snapshots: Mapping[str, WalletSnapshot],
    ) -> List[TransferPlan]:
        """
        Génère des plans de transferts pour garantir du gas sur les wallets
        importants (roles MAIN / SCALPING / COPYTRADING).

        On prélève le gas sur le wallet 'fees' correspondant à la chain.
        """
        plans: List[TransferPlan] = []
        if not self.config.autofees.enabled:
            return plans

        min_gas = self.config.autofees.min_gas_native
        target_gas = self.config.autofees.target_gas_native

        important_roles = {"MAIN", "SCALPING", "COPYTRADING"}

        for name, snap in snapshots.items():
            role = snap.role or self._get_wallet_role(name)
            if role not in important_roles:
                continue

            chain = (snap.chain or self._get_wallet_chain(name) or "").lower()
            if not chain:
                continue

            min_needed = min_gas.get(chain)
            if min_needed is None or min_needed <= 0:
                continue

            tgt = target_gas.get(chain, min_needed)
            current = snap.balance_native

            if current >= min_needed:
                # déjà assez de gas
                continue

            required = tgt - current
            if required <= 0:
                continue

            fees_wallet_name = self._get_fees_wallet_for_chain(chain)
            if not fees_wallet_name:
                logger.warning(
                    "AutoFees: aucun wallet de fees pour chain=%s (wallet=%s)",
                    chain,
                    name,
                )
                continue

            fees_snap = snapshots.get(fees_wallet_name)
            if not fees_snap or fees_snap.balance_native <= 0:
                logger.warning(
                    "AutoFees: wallet de fees '%s' introuvable ou sans gas (chain=%s)",
                    fees_wallet_name,
                    chain,
                )
                continue

            # On limite au solde du wallet de fees
            amount = min(required, fees_snap.balance_native)
            if amount <= 0:
                continue

            plan = TransferPlan(
                type="autofees",
                from_wallet=fees_wallet_name,
                to_wallet=name,
                chain=chain,
                amount_native=amount,
                amount_usd=Decimal("0"),
                reason=f"AutoFees: top-up gas on {chain} for {name}",
                metadata={
                    "target_gas": str(tgt),
                    "current_gas": str(current),
                },
            )
            plans.append(plan)

        return plans

    # ------------------------------------------------------------------
    # Sweep profits vers savings / vault
    # ------------------------------------------------------------------

    def plan_sweep_profits(
        self,
        snapshots: Mapping[str, WalletSnapshot],
    ) -> List[TransferPlan]:
        """
        Génère des transferts 'sweep' : on envoie une partie des profits
        réalisés depuis les wallets de trading vers les wallets de savings
        (profits_* ou vault).

        L'unité pour amount_usd est simplement "USD logique" — ce module ne
        gère pas la conversion vers un token réel.
        """
        plans: List[TransferPlan] = []
        if not self.config.sweep.enabled:
            return plans

        min_profit = self.config.sweep.min_profit_usd
        sweep_pct = self.config.sweep.sweep_pct

        for name, snap in snapshots.items():
            profit = snap.realized_profit_usd
            if profit <= 0 or profit < min_profit:
                continue

            chain = (snap.chain or self._get_wallet_chain(name) or "").lower()
            target_wallet = self._get_profits_wallet_for_chain(chain)
            if not target_wallet:
                logger.warning(
                    "Sweep: aucun wallet de profits/vault pour chain=%s (from=%s)",
                    chain,
                    name,
                )
                continue

            amount_usd = (profit * sweep_pct).quantize(Decimal("0.01"))
            if amount_usd <= 0:
                continue

            plan = TransferPlan(
                type="sweep",
                from_wallet=name,
                to_wallet=target_wallet,
                chain=None,  # à déterminer par la logique d'exécution
                amount_native=Decimal("0"),
                amount_usd=amount_usd,
                reason=f"Sweep profits from {name} to {target_wallet}",
                metadata={
                    "profit_usd": str(profit),
                    "sweep_pct": str(sweep_pct),
                },
            )
            plans.append(plan)

        return plans

    # ------------------------------------------------------------------
    # Compounding depuis le vault
    # ------------------------------------------------------------------

    def plan_compounding(
        self,
        snapshots: Mapping[str, WalletSnapshot],
    ) -> List[TransferPlan]:
        """
        Compounding simple : on prend un pourcentage du solde USD
        du vault et on le répartit sur les wallets de trading.

        Optimisation LIVE_150 :
          - on ne touche qu'à la partie du vault AU-DESSUS
            d'un seuil `vault_min_balance_usd` (si configuré),
          - on peut limiter le montant total distribué par run
            via `max_compound_usd_per_run`.

        Si aucune distribution n'est définie dans la config, on répartit
        uniformément entre les wallets de role MAIN/SCALPING/COPYTRADING.
        """
        plans: List[TransferPlan] = []

        cfg = self.config.compounding
        if not cfg.enabled:
            return plans

        vault_name = self._get_vault_wallet()
        if not vault_name:
            logger.warning("Compounding: aucun wallet de vault défini.")
            return plans

        vault_snap = snapshots.get(vault_name)
        if not vault_snap:
            logger.warning(
                "Compounding: snapshot manquant pour vault '%s'.",
                vault_name,
            )
            return plans

        base_amount = vault_snap.balance_usd
        if base_amount <= 0:
            return plans

        # floor sur le vault
        vault_min = cfg.vault_min_balance_usd or Decimal("0")
        if base_amount <= vault_min:
            logger.info(
                "Compounding: vault balance %s <= min %s, aucun compounding.",
                base_amount,
                vault_min,
            )
            return plans

        # on ne compound que la partie "excess" au-dessus du floor
        excess = base_amount - vault_min
        if excess <= 0:
            return plans

        compound_pct = cfg.compound_pct_from_vault
        total_to_distribute = excess * compound_pct

        # cap global par run
        max_per_run = cfg.max_compound_usd_per_run
        if max_per_run is not None and total_to_distribute > max_per_run:
            total_to_distribute = max_per_run

        total_to_distribute = total_to_distribute.quantize(Decimal("0.01"))
        if total_to_distribute <= 0:
            return plans

        # Détermination des poids
        weights = cfg.distribution_weights
        targets: List[str] = []

        if weights:
            targets = list(weights.keys())
        else:
            # fallback : tous les wallets de trading
            important_roles = {"MAIN", "SCALPING", "COPYTRADING"}
            for name, snap in snapshots.items():
                if name == vault_name:
                    continue
                role = snap.role or self._get_wallet_role(name)
                if role in important_roles:
                    targets.append(name)

            # distribution uniforme
            if targets:
                w = Decimal("1") / Decimal(len(targets))
                weights = {t: w for t in targets}
            else:
                return plans

        for tgt, w in weights.items():
            amount_usd = (total_to_distribute * w).quantize(Decimal("0.01"))
            if amount_usd <= 0:
                continue

            plan = TransferPlan(
                type="compound",
                from_wallet=vault_name,
                to_wallet=tgt,
                chain=None,
                amount_native=Decimal("0"),
                amount_usd=amount_usd,
                reason=f"Compounding from vault {vault_name} to {tgt}",
                metadata={
                    "compound_pct_from_vault": str(compound_pct),
                    "weight": str(w),
                    "vault_min_balance_usd": str(vault_min),
                    "total_to_distribute_usd": str(total_to_distribute),
                },
            )
            plans.append(plan)

        return plans

    # ------------------------------------------------------------------
    # Planning global
    # ------------------------------------------------------------------

    def plan_all(
        self,
        snapshots: Mapping[str, WalletSnapshot],
    ) -> List[TransferPlan]:
        """
        Génère tous les plans (AutoFees + Sweep + Compounding).
        """
        plans: List[TransferPlan] = []
        plans.extend(self.plan_autofees(snapshots))
        plans.extend(self.plan_sweep_profits(snapshots))
        plans.extend(self.plan_compounding(snapshots))
        return plans
