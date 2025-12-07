from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

import requests

from bot.agent.whale_brain import WhaleDecision

logger = logging.getLogger(__name__)


# ============================================================================
# Dataclasses de config
# ============================================================================

@dataclass
class ConsoleChannelConfig:
    enabled: bool = True


@dataclass
class TelegramChannelConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""  # string pour être safe


@dataclass
class AlertEngineConfig:
    """
    Config globale de l'AlertEngine.

    - min_pressure_for_alert : score de pression whales minimum
    - min_usd_for_alert      : taille USD minimum du mouvement whales
    """

    enabled: bool = True

    min_pressure_for_alert: float = 50.0
    min_usd_for_alert: float = 500_000.0

    console: ConsoleChannelConfig = field(default_factory=ConsoleChannelConfig)
    telegram: TelegramChannelConfig = field(default_factory=TelegramChannelConfig)


# ============================================================================
# Modèle d'alerte
# ============================================================================

@dataclass
class AlertEvent:
    chain: str
    block: int
    label: str
    direction: str  # "buy" / "sell" / "neutral"
    pressure: float
    confidence: float

    total_usd: float
    whale_count: int
    inflow_usd: float
    outflow_usd: float
    netflow_usd: float

    raw_decision: dict


# ============================================================================
# Channels d'alerte
# ============================================================================

class BaseAlertChannel:
    def send_alert(self, alert: AlertEvent) -> None:
        raise NotImplementedError


class ConsoleAlertChannel(BaseAlertChannel):
    """
    Channel simple qui logge les alertes en clair.
    """

    def send_alert(self, alert: AlertEvent) -> None:
        msg = (
            f"[ALERT][{alert.chain}] {alert.label.upper()} "
            f"pressure={alert.pressure:.1f} conf={alert.confidence:.2f} "
            f"whales={alert.whale_count} total_usd={alert.total_usd:,.0f} "
            f"net={alert.netflow_usd:,.0f} (block {alert.block})"
        )
        logger.warning(msg)


class TelegramAlertChannel(BaseAlertChannel):
    """
    Channel Telegram (optionnel) — envoie un message via l'API Telegram.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_alert(self, alert: AlertEvent) -> None:
        if not self.bot_token or not self.chat_id:
            logger.warning("[TelegramAlertChannel] bot_token ou chat_id manquant, alerte ignorée.")
            return

        text = (
            f"🐳 *Whale Alert* on *{alert.chain}*\n"
            f"`{alert.label}` — pressure={alert.pressure:.1f}, conf={alert.confidence:.2f}\n"
            f"Whales: *{alert.whale_count}*\n"
            f"Total: *{alert.total_usd:,.0f}$* | Net: *{alert.netflow_usd:,.0f}$*\n"
            f"Block: `{alert.block}`"
        )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code != 200:
                logger.warning(
                    "[TelegramAlertChannel] Erreur HTTP %s: %s",
                    resp.status_code,
                    resp.text,
                )
        except Exception as exc:
            logger.exception("[TelegramAlertChannel] Exception lors de l'envoi Telegram: %s", exc)


# ============================================================================
# Alert Engine
# ============================================================================

class AlertEngine:
    """
    AlertEngine PRO

    - reçoit des WhaleDecision
    - applique des règles simples
    - envoie les AlertEvent aux channels activés
    """

    def __init__(self, config: AlertEngineConfig) -> None:
        self.config = config
        self.channels: List[BaseAlertChannel] = []

        if self.config.console.enabled:
            self.channels.append(ConsoleAlertChannel())

        if self.config.telegram.enabled:
            self.channels.append(
                TelegramAlertChannel(
                    bot_token=self.config.telegram.bot_token,
                    chat_id=self.config.telegram.chat_id,
                )
            )

        logger.info(
            {
                "module": "AlertEngine",
                "event": "initialized",
                "config": asdict(self.config),
            }
        )

    # ------------------------------------------------------------------ #
    # PUBLIC
    # ------------------------------------------------------------------ #

    def handle_decision(self, decision: WhaleDecision) -> Optional[AlertEvent]:
        """
        Point d'entrée principal : on reçoit une WhaleDecision,
        on regarde si ça mérite une alerte, et on envoie si oui.
        """
        if not self.config.enabled:
            return None

        alert = self._build_alert_if_needed(decision)
        if alert is None:
            return None

        for ch in self.channels:
            try:
                ch.send_alert(alert)
            except Exception as exc:
                logger.exception(
                    {
                        "module": "AlertEngine",
                        "event": "channel_error",
                        "channel": ch.__class__.__name__,
                        "error": str(exc),
                    }
                )

        logger.info(
            {
                "module": "AlertEngine",
                "event": "alert_sent",
                "alert": asdict(alert),
            }
        )

        return alert

    # ------------------------------------------------------------------ #
    # RULES
    # ------------------------------------------------------------------ #

    def _build_alert_if_needed(self, decision: WhaleDecision) -> Optional[AlertEvent]:
        ctx = decision.context or {}

        total_usd = float(ctx.get("total_usd", 0.0))
        whale_count = int(ctx.get("whale_count", 0))
        inflow_usd = float(ctx.get("inflow_usd", 0.0))
        outflow_usd = float(ctx.get("outflow_usd", 0.0))
        netflow_usd = float(ctx.get("netflow_usd", 0.0))

        pressure = float(decision.pressure)
        confidence = float(decision.confidence)
        label = decision.label or "unknown"
        chain = decision.chain
        block = decision.block

        # Règle de base : volume + pression
        if total_usd < self.config.min_usd_for_alert:
            return None

        if pressure < self.config.min_pressure_for_alert:
            return None

        # Déterminer la direction
        if "bearish" in label or netflow_usd < 0:
            direction = "sell"
        elif "bullish" in label or netflow_usd > 0:
            direction = "buy"
        else:
            direction = "neutral"

        alert = AlertEvent(
            chain=chain,
            block=block,
            label=label,
            direction=direction,
            pressure=pressure,
            confidence=confidence,
            total_usd=total_usd,
            whale_count=whale_count,
            inflow_usd=inflow_usd,
            outflow_usd=outflow_usd,
            netflow_usd=netflow_usd,
            raw_decision=decision.to_json(),
        )

        return alert
