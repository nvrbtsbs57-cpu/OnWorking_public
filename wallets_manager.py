# bot/wallet/manager.py

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ============================================================================
# Enums & Dataclasses
# ============================================================================


class WalletRole(str, Enum):
    MAIN = "MAIN"              # Wallet principal de trading
    COPYTRADING = "COPYTRADING"  # Suivi d'une adresse / d'un trader
    SAVINGS = "SAVINGS"        # Epargne / long terme
    AUTO_FEES = "AUTO_FEES"    # Gas fees / frais
    SCALPING = "SCALPING"      # Stratégies rapides
    SWING = "SWING"            # Swing trading
    TEST = "TEST"              # Petits tests
    AIRDROP = "AIRDROP"        # Farming / airdrops
    NFT = "NFT"                # NFT / spéciaux
    BACKUP = "BACKUP"          # Secours / réserve


@dataclass
class WalletRiskLimits:
    """
    Limites de risque par wallet. Toutes les valeurs sont en USD ou unités simples.
    0 = pas de limite.
    """
    max_notional_usd_per_trade: float = 0.0
    max_daily_loss_usd: float = 0.0
    max_open_trades: int = 0
    enabled: bool = True


@dataclass
class WalletConfig:
    """
    Configuration statique d'un wallet, issue de config.json.
    """
    name: str
    role: WalletRole
    chain: str
    address: str
    private_key_env: str  # nom de la variable d'environnement qui contient la clé privée
    risk: WalletRiskLimits = field(default_factory=WalletRiskLimits)
    tags: List[str] = field(default_factory=list)


@dataclass
class WalletState:
    """
    Etat runtime d'un wallet (mise à jour par l'engine / monitoring).
    """
    name: str
    role: WalletRole
    chain: str
    address: str
    balance_cache: Dict[str, float] = field(default_factory=dict)
    open_trades: int = 0
    daily_pnl_usd: float = 0.0  # >0 gain, <0 perte

    def can_open_new_trade(self, risk_limit: WalletRiskLimits, notional_usd: float) -> bool:
        """
        Retourne True si le wallet accepte d'ouvrir un nouveau trade selon ses limites.
        """
        if not risk_limit.enabled:
            logger.warning(
                "[WalletState] %s désactivé (risk.enabled = False), refus d'ouverture de trade.",
                self.name,
            )
            return False

        if risk_limit.max_notional_usd_per_trade > 0 and notional_usd > risk_limit.max_notional_usd_per_trade:
            logger.warning(
                "[WalletState] %s refuse trade: notional=%.2f > max_notional_usd_per_trade=%.2f",
                self.name, notional_usd, risk_limit.max_notional_usd_per_trade,
            )
            return False

        if risk_limit.max_open_trades > 0 and self.open_trades >= risk_limit.max_open_trades:
            logger.warning(
                "[WalletState] %s refuse trade: open_trades=%d >= max_open_trades=%d",
                self.name, self.open_trades, risk_limit.max_open_trades,
            )
            return False

        if risk_limit.max_daily_loss_usd > 0 and self.daily_pnl_usd <= -abs(risk_limit.max_daily_loss_usd):
            logger.warning(
                "[WalletState] %s refuse trade: daily_pnl_usd=%.2f <= -max_daily_loss_usd=%.2f",
                self.name, self.daily_pnl_usd, risk_limit.max_daily_loss_usd,
            )
            return False

        return True


# ============================================================================
# WalletManager
# ============================================================================


class WalletManager:
    """
    Gère l'ensemble des wallets (EVM, Solana, BSC, etc.) et choisit lequel utiliser
    pour une demande d'exécution donnée.

    Il lit la config dans config["wallets"] (dict issu du JSON brut) et, si présent,
    la matrice config["wallet_roles"] pour le routing "purpose-based".
    """

    def __init__(self, wallets: List[WalletConfig], wallet_roles: Optional[Dict[str, Any]] = None) -> None:
        self._wallets_config: Dict[str, WalletConfig] = {w.name: w for w in wallets}
        self._wallets_state: Dict[str, WalletState] = {
            w.name: WalletState(
                name=w.name,
                role=w.role,
                chain=w.chain,
                address=w.address,
            )
            for w in wallets
        }
        # Matrice optionnelle issue de config["wallet_roles"]
        # Exemple:
        # {
        #   "trading": {"solana": "sniper_sol", "base": "base_main"},
        #   "fees": {"evm": "fees"},
        #   "profits": {"solana": "profits_sol", "base": "profits_base"},
        #   "vault": {"all": "vault"},
        #   "emergency": {"all": "emergency"},
        # }
        self._wallet_roles_cfg: Dict[str, Any] = wallet_roles or {}

        logger.info(
            "[WalletManager] Initialisé avec %d wallets: %s",
            len(wallets),
            list(self._wallets_config.keys()),
        )
        if self._wallet_roles_cfg:
            logger.info("[WalletManager] Matrice wallet_roles chargée: keys=%s", list(self._wallet_roles_cfg.keys()))

    # ----------------------------------------------------------------------
    # Construction depuis config.json (dict brut)
    # ----------------------------------------------------------------------
    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "WalletManager":
        wallets_cfg: List[WalletConfig] = []

        wallets_data = config.get("wallets", [])
        wallet_roles_cfg = config.get("wallet_roles", {}) or {}

        if not wallets_data:
            logger.warning("[WalletManager] Aucun wallet configuré dans config['wallets']")
            return cls([], wallet_roles=wallet_roles_cfg)

        logger.info("[WalletManager] Chargement de %d wallets depuis la config", len(wallets_data))

        for i, w in enumerate(wallets_data, start=1):
            try:
                role = WalletRole(w["role"])
            except Exception:
                logger.error(
                    "[WalletManager] Wallet #%d: rôle inconnu '%s' — ignoré",
                    i, w.get("role"),
                )
                continue

            risk_data = w.get("risk", {})
            risk = WalletRiskLimits(
                max_notional_usd_per_trade=float(risk_data.get("max_notional_usd_per_trade", 0.0)),
                max_daily_loss_usd=float(risk_data.get("max_daily_loss_usd", 0.0)),
                max_open_trades=int(risk_data.get("max_open_trades", 0)),
                enabled=bool(risk_data.get("enabled", True)),
            )

            try:
                wc = WalletConfig(
                    name=str(w["name"]),
                    role=role,
                    chain=str(w["chain"]),
                    address=str(w["address"]),
                    private_key_env=str(w["private_key_env"]),
                    risk=risk,
                    tags=list(w.get("tags", [])),
                )
            except KeyError as exc:
                logger.error(
                    "[WalletManager] Wallet #%d mal configuré, champ manquant: %s — ignoré",
                    i, exc,
                )
                continue

            wallets_cfg.append(wc)

        return cls(wallets_cfg, wallet_roles=wallet_roles_cfg)

    # ----------------------------------------------------------------------
    # Accès de base
    # ----------------------------------------------------------------------
    def list_wallets(self) -> List[str]:
        return list(self._wallets_config.keys())

    def get_wallet_config(self, name: str) -> Optional[WalletConfig]:
        return self._wallets_config.get(name)

    def get_wallet_state(self, name: str) -> Optional[WalletState]:
        return self._wallets_state.get(name)

    # ----------------------------------------------------------------------
    # Gestion des clés privées
    # ----------------------------------------------------------------------
    def get_private_key(self, wallet_name: str) -> Optional[str]:
        cfg = self._wallets_config.get(wallet_name)
        if not cfg:
            logger.error("[WalletManager] get_private_key: wallet '%s' introuvable", wallet_name)
            return None

        pk = os.getenv(cfg.private_key_env)
        if not pk:
            logger.error(
                "[WalletManager] Clé privée introuvable pour wallet '%s' (env %s)",
                wallet_name, cfg.private_key_env,
            )
            return None

        return pk

    # ----------------------------------------------------------------------
    # Sélection d'un wallet pour exécution (logique "stratégie")
    # ----------------------------------------------------------------------
    def choose_wallet_for_trade(
        self,
        *,
        chain: str,
        strategy_tag: str,
        notional_usd: float,
        prefer_role: Optional[WalletRole] = None,
        require_tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Logique simple mais extensible :

        1) Filtre par chain
        2) Filtre par rôle si prefer_role
        3) Filtre par tags si require_tags
        4) Vérifie les limites de risque
        5) Retourne le premier wallet éligible

        Retourne:
            - nom du wallet (str) si trouvé
            - None sinon
        """
        require_tags = require_tags or []

        chain_norm = self._normalize_chain(chain)

        candidates: List[WalletConfig] = [
            w for w in self._wallets_config.values()
            if self._normalize_chain(w.chain) == chain_norm
        ]

        if prefer_role:
            candidates = [w for w in candidates if w.role == prefer_role]

        if require_tags:
            candidates = [
                w for w in candidates
                if all(t in w.tags for t in require_tags)
            ]

        if not candidates:
            logger.error(
                "[WalletManager] Aucun wallet candidat pour chain=%s, role=%s, tags=%s",
                chain_norm, prefer_role, require_tags,
            )
            return None

        logger.debug(
            "[WalletManager] %d candidats pour chain=%s, strategy=%s, prefer_role=%s, tags=%s",
            len(candidates), chain_norm, strategy_tag, prefer_role, require_tags,
        )

        for w in candidates:
            state = self._wallets_state[w.name]
            if state.can_open_new_trade(w.risk, notional_usd):
                logger.info(
                    "[WalletManager] Wallet choisi: %s (role=%s) pour strategy=%s, notional=%.2f USD",
                    w.name, w.role.value, strategy_tag, notional_usd,
                )
                return w.name

        logger.warning(
            "[WalletManager] Aucun wallet n'a validé les contraintes de risque (strategy=%s, notional=%.2f)",
            strategy_tag, notional_usd,
        )
        return None

    # ----------------------------------------------------------------------
    # Routing via config["wallet_roles"] (si disponible)
    # ----------------------------------------------------------------------
    def _route_via_wallet_roles(self, chain: str, purpose: str) -> Optional[str]:
        """
        Utilise la matrice config["wallet_roles"] si elle est présente.

        Exemple de matrice (extrait) :

            "wallet_roles": {
              "trading": {
                "solana": "sniper_sol",
                "base": "base_main",
                "bsc": "bsc_main"
              },
              "copy_trading": {
                "solana": "copy_sol"
              },
              "fees": {
                "evm": "fees"
              },
              "profits": {
                "solana": "profits_sol",
                "base": "profits_base",
                "bsc": "profits_bsc"
              },
              "vault": {
                "all": "vault"
              },
              "emergency": {
                "all": "emergency"
              }
            }

        Retourne le nom du wallet ciblé, ou None si rien ne match.
        """
        if not self._wallet_roles_cfg:
            return None

        c = self._normalize_chain(chain)
        p_raw = str(purpose).lower()

        # Liste de clés de purpose possibles, du plus spécifique au plus générique
        purpose_keys: List[str] = [p_raw]
        if p_raw in ("savings", "vault", "treasury"):
            purpose_keys.append("vault")
            purpose_keys.append("profits")
        elif p_raw in ("profit", "profits"):
            purpose_keys.append("profits")
        elif p_raw in ("fees", "gas"):
            purpose_keys.append("fees")
        elif p_raw in ("backup", "emergency"):
            purpose_keys.append("emergency")
        elif p_raw in ("copy", "copytrading", "copy_trading"):
            purpose_keys.append("copy_trading")
        elif p_raw == "trading":
            purpose_keys.append("trading")

        mapping_for_purpose: Optional[Dict[str, Any]] = None
        for pk in purpose_keys:
            m = self._wallet_roles_cfg.get(pk)
            if isinstance(m, dict):
                mapping_for_purpose = m
                break

        if not mapping_for_purpose:
            return None

        # Priorité : mapping par chain, puis "evm", puis "all"
        chain_keys: List[str] = [c]
        if c in ("ethereum", "arbitrum", "base", "bsc"):
            chain_keys.append("evm")
        chain_keys.append("all")

        candidate_name: Optional[str] = None
        for ck in chain_keys:
            if ck in mapping_for_purpose:
                candidate_name = str(mapping_for_purpose[ck])
                break

        if not candidate_name:
            return None

        cfg = self._wallets_config.get(candidate_name)
        if not cfg:
            logger.warning(
                "[WalletManager] wallet_roles: wallet '%s' n'existe pas dans wallets_config",
                candidate_name,
            )
            return None

        if not cfg.risk.enabled:
            logger.warning(
                "[WalletManager] wallet_roles: wallet '%s' est désactivé (risk.enabled = False)",
                candidate_name,
            )
            return None

        return candidate_name

    # ----------------------------------------------------------------------
    # Routing simplifié : chain + purpose -> wallet (pour AgentEngine / flows)
    # ----------------------------------------------------------------------
    def get_wallet_for_chain(
        self,
        chain: str,
        purpose: str = "trading",
    ) -> Optional[str]:
        """
        Détermine quel wallet utiliser pour une chain donnée et un "purpose".

        - chain   : ex "ethereum", "base", "bsc", "solana", ...
        - purpose :
             * "trading"  : trading normal
             * "copy_trading" : éventuellement copy-trading
             * "fees"     : paiement des gas
             * "savings" / "profits" / "vault" : vault / profits
             * "backup"   : emergency

        Priorité:
        1) config["wallet_roles"] si présente (routing explicite)
        2) heuristiques basées sur les WalletRole
        """
        # 1) Routing explicite via config["wallet_roles"] si disponible
        via_roles = self._route_via_wallet_roles(chain, purpose)
        if via_roles:
            return via_roles

        # 2) Heuristiques de fallback
        c = self._normalize_chain(chain)
        p = str(purpose).lower()

        # Filtre par chain + risk.enabled
        candidates: List[WalletConfig] = [
            w
            for w in self._wallets_config.values()
            if self._normalize_chain(w.chain) == c and w.risk.enabled
        ]

        if not candidates:
            logger.warning(
                "[WalletManager] Aucun wallet actif pour chain=%s purpose=%s",
                c,
                p,
            )
            return None

        # 1) Purpose "fees" -> rôle AUTO_FEES sur cette chain
        if p == "fees":
            for w in candidates:
                if w.role == WalletRole.AUTO_FEES:
                    return w.name

        # 2) Purpose "savings"/"vault"/"profits" -> SAVINGS puis BACKUP
        if p in ("savings", "vault", "profits", "treasury"):
            for w in candidates:
                if w.role == WalletRole.SAVINGS:
                    return w.name
            for w in candidates:
                if w.role == WalletRole.BACKUP:
                    return w.name

        # 3) Purpose "backup"
        if p == "backup":
            for w in candidates:
                if w.role == WalletRole.BACKUP:
                    return w.name

        # 4) Purpose "trading" (par chain)

        # Solana : SCALPING > COPYTRADING
        if c == "solana":
            for w in candidates:
                if w.role == WalletRole.SCALPING:
                    return w.name
            for w in candidates:
                if w.role == WalletRole.COPYTRADING:
                    return w.name

        # Ethereum/Base/BSC/etc : MAIN en priorité
        for w in candidates:
            if w.role == WalletRole.MAIN:
                return w.name

        # Fallback : premier candidat
        chosen = candidates[0]
        logger.info(
            "[WalletManager] Routing default: chain=%s purpose=%s -> wallet=%s",
            c,
            p,
            chosen.name,
        )
        return chosen.name

    # ----------------------------------------------------------------------
    # Helper haut niveau : exécution
    # ----------------------------------------------------------------------
    def choose_wallet_for_execution(
        self,
        *,
        chain: str,
        notional_usd: float,
        strategy_tag: str,
        purpose: str = "trading",
        prefer_role: Optional[WalletRole] = None,
        require_tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Helper haut niveau pour l'ExecutionEngine / AgentEngine.

        1) Tente de router via get_wallet_for_chain(chain, purpose) (config["wallet_roles"])
           + vérifie les limites de risque.
        2) Si ce wallet ne passe pas les contraintes, fallback sur choose_wallet_for_trade()
           avec les filtres optionnels (role, tags).
        """
        # 1) Routing purpose-based
        primary_name = self.get_wallet_for_chain(chain, purpose=purpose)
        if primary_name:
            cfg = self._wallets_config.get(primary_name)
            state = self._wallets_state.get(primary_name)
            if cfg and state and state.can_open_new_trade(cfg.risk, notional_usd):
                logger.info(
                    "[WalletManager] choose_wallet_for_execution -> %s via purpose=%s",
                    primary_name,
                    purpose,
                )
                return primary_name

        # 2) Fallback : routing par stratégie
        return self.choose_wallet_for_trade(
            chain=chain,
            strategy_tag=strategy_tag,
            notional_usd=notional_usd,
            prefer_role=prefer_role,
            require_tags=require_tags,
        )

    # ----------------------------------------------------------------------
    # Mise à jour de l'état (à appeler depuis l'engine / monitoring)
    # ----------------------------------------------------------------------
    def register_new_open_trade(self, wallet_name: str) -> None:
        ws = self._wallets_state.get(wallet_name)
        if ws:
            ws.open_trades += 1

    def register_closed_trade(self, wallet_name: str, pnl_usd: float) -> None:
        """
        pnl_usd > 0 : gain
        pnl_usd < 0 : perte (diminue daily_pnl_usd)
        """
        ws = self._wallets_state.get(wallet_name)
        if ws:
            ws.open_trades = max(0, ws.open_trades - 1)
            ws.daily_pnl_usd += pnl_usd

    def reset_daily_pnl(self) -> None:
        """
        A appeler 1x / jour (cron / scheduler) pour remettre les compteurs à zéro.
        """
        for ws in self._wallets_state.values():
            ws.daily_pnl_usd = 0.0

    def update_balance_cache(self, wallet_name: str, token_symbol: str, balance: float) -> None:
        ws = self._wallets_state.get(wallet_name)
        if ws:
            ws.balance_cache[token_symbol] = balance

    # ----------------------------------------------------------------------
    # Utils
    # ----------------------------------------------------------------------
    @staticmethod
    def _normalize_chain(chain_raw: Any) -> str:
        """
        Normalise le nom de la chain.
        """
        if not chain_raw:
            return "ethereum"

        c = str(chain_raw).strip().lower()

        # Ethereum
        if c in ("eth", "ethereum", "eth-mainnet", "mainnet"):
            return "ethereum"

        # BSC
        if c in ("bsc", "binance-smart-chain", "bnb", "bsc-mainnet"):
            return "bsc"

        # Solana
        if c in ("sol", "solana", "sol-mainnet-beta", "sol-mainnet"):
            return "solana"

        # Arbitrum
        if c in ("arb", "arbitrum", "arbitrum-one"):
            return "arbitrum"

        # Base
        if c in ("base", "base-mainnet"):
            return "base"

        return 
