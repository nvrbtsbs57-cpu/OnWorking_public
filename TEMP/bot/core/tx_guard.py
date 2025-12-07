# file: bot/core/tx_guard.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Set

from bot.core.runtime import ExecutionMode  # PAPER_ONCHAIN / LIVE

logger = logging.getLogger(__name__)


@dataclass
class TxGuardConfig:
    """
    Configuration centrale du garde-fou TX (M10).

    hard_disable_send_tx :
        - True  => aucune TX réelle n'est autorisée, peu importe le reste.
        - False => on applique les autres règles (mode, profil, etc.).

    allowed_profiles :
        - Optionnel : liste de profils texte (ex: ["LIVE_150", "LIVE_1K"])
          autorisés à envoyer des TX réelles quand on passera en LIVE.
        - En M10, on peut laisser vide pour tout bloquer de toute façon.

    log_only :
        - prévu pour le futur : si True, on loggue la décision mais on ne
          bloque pas effectivement (à n'activer qu'en M11+).
        - En M10, on ignore ce flag et on bloque vraiment.
    """

    hard_disable_send_tx: bool = True
    allowed_profiles: Optional[Set[str]] = None
    log_only: bool = False

    @classmethod
    def from_global_config(cls, raw_cfg: Mapping[str, Any]) -> "TxGuardConfig":
        """
        Construit la config depuis le dict global (config.json déjà chargé).

        On s'attend à une section optionnelle :

        "execution": {
          "tx_guard": {
            "hard_disable_send_tx": true,
            "allowed_profiles": ["LIVE_150"],
            "log_only": false
          }
        }
        """
        if not isinstance(raw_cfg, Mapping):
            raw_cfg = {}

        exec_cfg = raw_cfg.get("execution", {}) or {}
        guard_cfg = exec_cfg.get("tx_guard", {}) or {}

        allowed_profiles_raw = guard_cfg.get("allowed_profiles") or []
        allowed_profiles = {str(p).strip() for p in allowed_profiles_raw if str(p).strip()}

        return cls(
            hard_disable_send_tx=bool(guard_cfg.get("hard_disable_send_tx", True)),
            allowed_profiles=allowed_profiles or None,
            log_only=bool(guard_cfg.get("log_only", False)),
        )


def can_send_real_tx(
    cfg: TxGuardConfig,
    execution_mode: ExecutionMode,
    *,
    profile: Optional[str] = None,
    context: Optional[str] = None,
) -> bool:
    """
    Garde-fou central M10/M11.

    En M10 (LIVE désactivé), le comportement est volontairement ultra strict :

    - si hard_disable_send_tx == True  => toujours False,
    - si execution_mode != LIVE        => False,
    - si allowed_profiles est défini   => le profil doit être dans la liste,
    - sinon                            => True.

    Le paramètre `context` permet d'indiquer la provenance
    (ex: "memecoin_farming_entry", "rebalance_fees_wallet", ...).
    """
    reason = context or "n/a"

    # 1) M10 : kill-switch global tant que le passage LIVE n'est pas acté
    if cfg.hard_disable_send_tx:
        logger.warning(
            "[TX_GUARD] TX réelle BLOQUÉE — hard_disable_send_tx=True "
            "(mode=%s, profile=%s, context=%s)",
            getattr(execution_mode, "value", execution_mode),
            profile,
            reason,
        )
        return False

    # 2) ExecutionMode doit être LIVE pour envisager une TX réelle
    if execution_mode is not ExecutionMode.LIVE:
        logger.info(
            "[TX_GUARD] TX bloquée — execution_mode != LIVE "
            "(mode=%s, profile=%s, context=%s)",
            getattr(execution_mode, "value", execution_mode),
            profile,
            reason,
        )
        return False

    # 3) Filtre optionnel par profil (prévu pour M11+)
    if cfg.allowed_profiles is not None and profile is not None:
        if profile not in cfg.allowed_profiles:
            logger.info(
                "[TX_GUARD] TX bloquée — profile '%s' non autorisé "
                "(allowed=%s, context=%s)",
                profile,
                sorted(cfg.allowed_profiles),
                reason,
            )
            return False

    # 4) Log-only (futur) : en M10 on ne l'utilise pas encore pour bypass
    if cfg.log_only:
        logger.warning(
            "[TX_GUARD] TX potentiellement autorisée (log_only=True) "
            "(mode=%s, profile=%s, context=%s)",
            getattr(execution_mode, "value", execution_mode),
            profile,
            reason,
        )
    else:
        logger.info(
            "[TX_GUARD] TX autorisée (mode=%s, profile=%s, context=%s)",
            getattr(execution_mode, "value", execution_mode),
            profile,
            reason,
        )

    return True


__all__ = [
    "TxGuardConfig",
    "can_send_real_tx",
]

