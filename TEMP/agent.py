# bot/strategies/memecoin_farming/agent.py

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Protocol

from bot.core.signals import TradeSignal, SignalKind, SignalSide


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modèles de config & candidats
# ---------------------------------------------------------------------------


@dataclass
class MemecoinPairConfig:
    """
    Config pour un "pair" memecoin surveillé.

    M5-full :
      - symbol / chain / wallet_id : routage,
      - min/max_notional_usd : sizing,
      - min_liquidity_usd, min_volume_24h_usd : filtres de qualité,
      - max_token_age_minutes : évite les trop vieux,
      - min_score : score minimal issu du provider.
    """
    symbol: str
    chain: str
    wallet_id: str
    min_notional_usd: Decimal
    max_notional_usd: Decimal
    min_liquidity_usd: Decimal = Decimal("0")
    min_volume_24h_usd: Decimal = Decimal("0")
    max_token_age_minutes: int = 0
    min_score: float = 0.0


@dataclass
class MemecoinCandidate:
    """
    Représente un memecoin "candidat" issu d'un scan (on-chain ou stub).

    meta est volontairement libre :
      - "liq_usd"               -> Decimal or float
      - "volume_24h_usd"        -> Decimal or float
      - "token_age_minutes"     -> int
      - tout autre info utile (nb de holders, mc, etc.)
    """
    symbol: str
    chain: str
    score: float
    notional_usd: Decimal
    wallet_id: str
    meta: Dict[str, Any]


@dataclass
class OpenPositionInfo:
    """
    Position ouverte gérée côté stratégie (vue simplifiée).
    - key logique = f"{wallet_id}:{symbol}"
    - ticks_open : nb de ticks depuis l'ENTRY.
    """
    wallet_id: str
    symbol: str
    notional_usd: Decimal
    chain: str
    ticks_open: int = 0
    entry_signal_id: str = ""


# ---------------------------------------------------------------------------
# Provider on-chain (interface, M5-full) + stub
# ---------------------------------------------------------------------------


class MemecoinDataProvider(Protocol):
    """
    Interface pour un provider qui scanne la chain et renvoie des candidats.

    Impl M5-full typique :
      - lit la config (pairs, blocklists, thresholds),
      - appelle un indexer / RPC / API,
      - construit des MemecoinCandidate avec meta bien remplis.
    """

    def scan_candidates(
        self, pair_configs: Sequence[MemecoinPairConfig]
    ) -> Sequence[MemecoinCandidate]:
        ...


class StubRandomMemecoinProvider:
    """
    Provider stub qui génère des candidats aléatoires à partir des pair_configs.

    Objectif : pouvoir tester toute la chaîne M1–M4 / M5 / M7 sans aucun RPC.

    Utilisé quand config:

      "strategies": {
        "memecoin_farming": {
          ...
          "provider": {
            "kind": "stub_random",
            "max_candidates_per_tick": 3,
            "seed": 42
          }
        }
      }
    """

    def __init__(
        self,
        *,
        max_candidates_per_tick: int = 3,
        seed: Optional[int] = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._max = max(int(max_candidates_per_tick), 0)
        self._logger = logging.getLogger("StubRandomMemecoinProvider")

    def scan_candidates(
        self, pair_configs: Sequence[MemecoinPairConfig]
    ) -> Sequence[MemecoinCandidate]:
        if not pair_configs or self._max <= 0:
            return []

        # nombre de candidats aléatoires pour ce tick
        n = self._rng.randint(0, self._max)
        if n <= 0:
            return []

        # On peut générer plusieurs candidats, éventuellement sur les mêmes pairs.
        cfg_list = list(pair_configs)
        out: List[MemecoinCandidate] = []

        for _ in range(n):
            cfg = self._rng.choice(cfg_list)

            # score [0, 1]
            score = self._rng.random()

            # notional approximativement dans [min_notional_usd, max_notional_usd * 1.5]
            min_notional = float(cfg.min_notional_usd)
            max_base = float(cfg.max_notional_usd) if cfg.max_notional_usd > 0 else max(min_notional * 2, 1.0)
            raw_notional = self._rng.uniform(min_notional, max_base)
            notional = Decimal(str(round(raw_notional, 2)))

            # métriques "fake" mais réalistes pour tester les filtres
            # On se base sur les min_liq/min_vol pour fixer un upper bound correct.
            liq_upper = float(max(cfg.min_liquidity_usd * 2, Decimal("1000")))
            vol_upper = float(max(cfg.min_volume_24h_usd * 2, Decimal("5000")))
            liq_usd = self._rng.uniform(0.0, liq_upper)
            vol_24h = self._rng.uniform(0.0, vol_upper)

            max_age_cfg = cfg.max_token_age_minutes or 240
            age_min = int(self._rng.randint(5, max(max_age_cfg, 5)))

            meta: Dict[str, Any] = {
                "liq_usd": liq_usd,
                "volume_24h_usd": vol_24h,
                "token_age_minutes": age_min,
                "source": "stub_random",
            }

            out.append(
                MemecoinCandidate(
                    symbol=cfg.symbol,
                    chain=cfg.chain,
                    score=score,
                    notional_usd=notional,
                    wallet_id=cfg.wallet_id,
                    meta=meta,
                )
            )

        self._logger.debug(
            "StubRandomMemecoinProvider — généré %d candidats.",
            len(out),
        )
        return out


# ---------------------------------------------------------------------------
# Moteur de stratégie memecoin (M5-lite -> M5-lite++)
# ---------------------------------------------------------------------------


class MemecoinStrategyEngine:
    """
    Moteur de stratégie memecoin.

    Pour coller au runtime (StrategyEngineIface), il expose :
      - next_signals() -> Sequence[TradeSignal]
      - on_tick()

    Comportement M5-lite++ :
      - ENTRY :
          * soit à partir de feed_candidates() (tests, stubs),
          * soit via un MemecoinDataProvider qui scanne à chaque tick.
        filtres appliqués :
          * min_score,
          * min/max_notional_usd,
          * min_liquidity_usd, min_volume_24h_usd,
          * max_token_age_minutes,
          * 1 seule position par (wallet_id, symbol).
      - EXIT :
          * toujours time-based via ticks_open / exit_after_ticks (pour l’instant).
    """

    def __init__(
        self,
        pair_configs: Sequence[MemecoinPairConfig],
        strategy_id: str = "memecoin_farming",
        exit_after_ticks: int = 5,
        provider: Optional[MemecoinDataProvider] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger_ or logging.getLogger(__name__)
        self._pair_cfg_by_symbol: Dict[str, MemecoinPairConfig] = {
            cfg.symbol: cfg for cfg in pair_configs
        }
        self._pending_candidates: List[MemecoinCandidate] = []
        self._strategy_id = strategy_id
        self._exit_after_ticks = max(int(exit_after_ticks), 1)
        self._provider = provider

        # positions ouvertes côté stratégie (clé = "wallet_id:symbol")
        self._open_positions: Dict[str, OpenPositionInfo] = {}

        self._logger.info(
            "MemecoinStrategyEngine initialisé avec %d pairs : %s",
            len(self._pair_cfg_by_symbol),
            list(self._pair_cfg_by_symbol.keys()),
        )

    # ------------------------------------------------------------------
    # Alimentation en candidats (provider + feed manual)
    # ------------------------------------------------------------------

    def feed_candidates(self, candidates: Sequence[MemecoinCandidate]) -> None:
        """
        Ajoute une liste de candidats à la file interne.
        Utilisé par :
          - les tests unitaires,
          - ou un pipe externe si tu ne veux pas utiliser un provider.
        """
        self._pending_candidates.extend(candidates)
        self._logger.debug(
            "feed_candidates() — ajout de %d candidats, total=%d",
            len(candidates),
            len(self._pending_candidates),
        )

    def _pull_from_provider(self) -> None:
        """
        Si un MemecoinDataProvider est configuré, on le laisse remplir
        les candidats à chaque tick (M5-full).
        """
        if self._provider is None:
            return

        try:
            new_candidates = list(
                self._provider.scan_candidates(
                    list(self._pair_cfg_by_symbol.values())
                )
            )
        except Exception:
            self._logger.exception(
                "Erreur lors de provider.scan_candidates(), provider désactivé pour ce tick."
            )
            return

        if not new_candidates:
            return

        self._pending_candidates.extend(new_candidates)
        self._logger.debug(
            "_pull_from_provider() — provider a ajouté %d candidats, total=%d",
            len(new_candidates),
            len(self._pending_candidates),
        )

    # ------------------------------------------------------------------
    # Génération ENTRY
    # ------------------------------------------------------------------

    def _candidate_to_signal(
        self, candidate: MemecoinCandidate
    ) -> Optional[TradeSignal]:
        """
        Convertit un candidat en TradeSignal ENTRY si possible.

        Filtres M5-full (si la config les active) :
          - pair configurée,
          - score >= min_score,
          - 1 position max par (wallet_id, symbol),
          - notional dans [min_notional_usd, max_notional_usd],
          - liq_usd >= min_liquidity_usd (si présent dans meta),
          - volume_24h_usd >= min_volume_24h_usd (si présent),
          - token_age_minutes <= max_token_age_minutes (si présent).
        """
        cfg = self._pair_cfg_by_symbol.get(candidate.symbol)
        if cfg is None:
            self._logger.debug(
                "Candidat ignoré, symbol non configuré: %s", candidate.symbol
            )
            return None

        # score minimal (par pair)
        if candidate.score < cfg.min_score:
            self._logger.debug(
                "Candidat %s ignoré (score=%.2f < min_score=%.2f)",
                candidate.symbol,
                candidate.score,
                cfg.min_score,
            )
            return None

        key = f"{candidate.wallet_id}:{candidate.symbol}"
        if key in self._open_positions:
            # On a déjà une position ouverte sur ce wallet/symbol : on ignore.
            self._logger.debug(
                "Candidat ignoré, position déjà ouverte pour %s", key
            )
            return None

        # Métriques supplémentaires du provider (optionnelles)
        liq_usd = Decimal(str(candidate.meta.get("liq_usd", "0")))
        vol_24h_usd = Decimal(str(candidate.meta.get("volume_24h_usd", "0")))
        token_age_minutes = int(candidate.meta.get("token_age_minutes", 0))

        if cfg.min_liquidity_usd > 0 and liq_usd < cfg.min_liquidity_usd:
            self._logger.debug(
                "Candidat %s ignoré (liq=%.2f < min_liquidity=%.2f)",
                candidate.symbol,
                float(liq_usd),
                float(cfg.min_liquidity_usd),
            )
            return None

        if cfg.min_volume_24h_usd > 0 and vol_24h_usd < cfg.min_volume_24h_usd:
            self._logger.debug(
                "Candidat %s ignoré (vol24h=%.2f < min_volume_24h=%.2f)",
                candidate.symbol,
                float(vol_24h_usd),
                float(cfg.min_volume_24h_usd),
            )
            return None

        if (
            cfg.max_token_age_minutes > 0
            and token_age_minutes > cfg.max_token_age_minutes
        ):
            self._logger.debug(
                "Candidat %s ignoré (age=%d min > max_token_age=%d)",
                candidate.symbol,
                token_age_minutes,
                cfg.max_token_age_minutes,
            )
            return None

        # Clamp notionnel
        notional = candidate.notional_usd
        if notional < cfg.min_notional_usd:
            self._logger.debug(
                "Candidat %s ignoré (notional=%.2f < min=%.2f)",
                candidate.symbol,
                float(notional),
                float(cfg.min_notional_usd),
            )
            return None

        if notional > cfg.max_notional_usd:
            notional = cfg.max_notional_usd

        sig_id = f"meme:{candidate.symbol}:{candidate.score:.2f}"

        meta = dict(candidate.meta)
        meta.setdefault("strategy", self._strategy_id)
        meta.setdefault("chain", candidate.chain)
        meta.setdefault("score", candidate.score)

        signal = TradeSignal(
            id=sig_id,
            strategy_id=self._strategy_id,
            wallet_id=candidate.wallet_id,
            symbol=candidate.symbol,
            side=SignalSide.BUY,
            notional_usd=float(notional),
            kind=SignalKind.ENTRY,
            meta=meta,
        )

        # On enregistre la position ouverte côté stratégie
        self._open_positions[key] = OpenPositionInfo(
            wallet_id=candidate.wallet_id,
            symbol=candidate.symbol,
            notional_usd=notional,
            chain=candidate.chain,
            ticks_open=0,
            entry_signal_id=sig_id,
        )

        return signal

    def generate_signals(self) -> List[TradeSignal]:
        """
        Génère des TradeSignal ENTRY à partir des candidats en attente.
        (Utilisé par les scripts de test unitaires simples.)
        """
        if not self._pending_candidates:
            return []

        candidates = self._pending_candidates
        self._pending_candidates = []

        signals: List[TradeSignal] = []
        for c in candidates:
            sig = self._candidate_to_signal(c)
            if sig is None:
                continue
            signals.append(sig)

        if signals:
            self._logger.info(
                "generate_signals() — %d signaux ENTRY générés.",
                len(signals),
            )
        else:
            self._logger.info(
                "generate_signals() — aucun signal généré à partir de %d candidats.",
                len(candidates),
            )

        return signals

    # ------------------------------------------------------------------
    # Génération EXIT (time-based, M5-lite++)
    # ------------------------------------------------------------------

    def _generate_exit_signals(self) -> List[TradeSignal]:
        """
        Génère des signaux EXIT pour les positions ouvertes depuis
        au moins `exit_after_ticks` ticks.
        """
        if not self._open_positions:
            return []

        exits: List[TradeSignal] = []
        to_delete: List[str] = []

        for key, pos in self._open_positions.items():
            pos.ticks_open += 1
            if pos.ticks_open < self._exit_after_ticks:
                continue

            sig_id = (
                f"{self._strategy_id}:exit:"
                f"{pos.wallet_id}:{pos.symbol}:{pos.ticks_open}"
            )

            meta = {
                "strategy": self._strategy_id,
                "chain": pos.chain,
                "exit_reason": "time_based_stub",
                "ticks_open": pos.ticks_open,
                "entry_signal_id": pos.entry_signal_id,
            }

            exit_sig = TradeSignal(
                id=sig_id,
                strategy_id=self._strategy_id,
                wallet_id=pos.wallet_id,
                symbol=pos.symbol,
                side=SignalSide.SELL,
                notional_usd=float(pos.notional_usd),
                kind=SignalKind.EXIT,
                meta=meta,
            )
            exits.append(exit_sig)
            to_delete.append(key)

        for key in to_delete:
            del self._open_positions[key]

        if exits:
            self._logger.info(
                "generate_exit_signals() — %d signaux EXIT générés.",
                len(exits),
            )

        return exits

    # ------------------------------------------------------------------
    # API runtime (StrategyEngineIface)
    # ------------------------------------------------------------------

    def next_signals(self) -> Sequence[TradeSignal]:
        """
        Méthode appelée par BotRuntime à chaque tick.

        - Optionnel : appelle le provider pour remplir de nouveaux candidats.
        - Consomme les candidats en attente → ENTRY.
        - Avance le temps des positions ouvertes → EXIT au bout
          de `exit_after_ticks`.
        """
        # 1) Provider on-chain (si configuré)
        self._pull_from_provider()

        # 2) ENTRY à partir des candidats (provider + feed_manual)
        entry_signals = self.generate_signals()

        # 3) EXIT time-based
        exit_signals = self._generate_exit_signals()
        return [*entry_signals, *exit_signals]

    def on_tick(self) -> None:
        """
        Hook appelé après next_signals() par le runtime.
        Pour M5-lite++ : no-op.
        """
        return None


# ---------------------------------------------------------------------------
# Helpers pour tests / scripts
# ---------------------------------------------------------------------------


def make_default_pair_configs() -> List[MemecoinPairConfig]:
    """
    Config minimale pour M5-lite de test : une seule pair SOL/USDC.
    """
    return [
        MemecoinPairConfig(
            symbol="SOL/USDC",
            chain="solana",
            wallet_id="sniper_sol",
            min_notional_usd=Decimal("50"),
            max_notional_usd=Decimal("300"),
        )
    ]


def build_memecoin_strategy_from_config(
    raw_cfg: Dict[str, Any],
    logger_: Optional[logging.Logger] = None,
) -> MemecoinStrategyEngine:
    """
    Construit un MemecoinStrategyEngine à partir de config.json.

    Compatible avec une config de ce style :

      "strategies": {
        "memecoin_farming": {
          "strategy_id": "memecoin_farming",
          "exit_after_ticks": 5,
          "entry_filters": {
            "min_score": 0.6,
            "min_liquidity_usd": 1000,
            "min_volume_24h_usd": 5000,
            "max_token_age_minutes": 120
          },
          "provider": {
            "kind": "stub_random",
            "max_candidates_per_tick": 3,
            "seed": 42
          },
          "pairs": [
            {
              "symbol": "SOL/USDC",
              "chain": "solana",
              "wallet_id": "sniper_sol",
              "min_notional_usd": 50,
              "max_notional_usd": 300
            }
          ]
        }
      }

    Si la section n'est pas présente, on fallback sur make_default_pair_configs()
    et provider=None.
    """
    log = logger_ or logging.getLogger("MemecoinStrategy")

    strategies = raw_cfg.get("strategies", {}) or {}
    meme_raw = (
        strategies.get("memecoin_farming")
        or strategies.get("memecoin")
        or {}
    )

    # --- filtres globaux (entry_filters) -------------------------------
    filters = meme_raw.get("entry_filters", {}) or {}
    global_min_liq = filters.get("min_liquidity_usd", "0")
    global_min_vol = filters.get("min_volume_24h_usd", "0")
    global_max_age = filters.get("max_token_age_minutes", 0)
    global_min_score = filters.get("min_score", 0.0)

    # --- pairs ---------------------------------------------------------
    pairs_raw = meme_raw.get("pairs", []) or []

    pair_cfgs: List[MemecoinPairConfig] = []
    for p in pairs_raw:
        try:
            symbol = str(p["symbol"])
        except KeyError as exc:
            log.warning(
                "Pair memecoin invalide dans config (clé manquante: %s) : %r",
                exc,
                p,
            )
            continue

        chain = str(p.get("chain", "solana"))
        wallet_id = str(p.get("wallet_id", "sniper_sol"))

        min_notional = Decimal(str(p.get("min_notional_usd", "50")))
        max_notional = Decimal(str(p.get("max_notional_usd", "300")))

        min_liq = Decimal(str(p.get("min_liquidity_usd", global_min_liq)))
        min_vol = Decimal(str(p.get("min_volume_24h_usd", global_min_vol)))
        max_age = int(p.get("max_token_age_minutes", global_max_age))
        min_score = float(p.get("min_score", global_min_score))

        cfg = MemecoinPairConfig(
            symbol=symbol,
            chain=chain,
            wallet_id=wallet_id,
            min_notional_usd=min_notional,
            max_notional_usd=max_notional,
            min_liquidity_usd=min_liq,
            min_volume_24h_usd=min_vol,
            max_token_age_minutes=max_age,
            min_score=min_score,
        )
        pair_cfgs.append(cfg)

    if not pair_cfgs:
        log.warning(
            "Aucune pair memecoin configurée dans config.json, "
            "fallback sur make_default_pair_configs()."
        )
        pair_cfgs = make_default_pair_configs()

    exit_after_ticks = int(meme_raw.get("exit_after_ticks", 5))
    strategy_id = str(meme_raw.get("strategy_id", "memecoin_farming"))

    # --- provider ------------------------------------------------------
    provider_cfg = meme_raw.get("provider", {}) or {}
    provider_kind = str(provider_cfg.get("kind", "")).lower()

    provider: Optional[MemecoinDataProvider] = None
    if provider_kind == "stub_random":
        max_cands = int(provider_cfg.get("max_candidates_per_tick", 3))
        seed = provider_cfg.get("seed")
        provider = StubRandomMemecoinProvider(
            max_candidates_per_tick=max_cands,
            seed=seed,
        )
        log.info(
            "Memecoin provider: StubRandomMemecoinProvider "
            "(max_candidates_per_tick=%d, seed=%r)",
            max_cands,
            seed,
        )
    else:
        if provider_kind:
            log.warning(
                "Provider memecoin kind '%s' non reconnu, aucun provider configuré.",
                provider_kind,
            )
        else:
            log.info(
                "Aucun provider memecoin configuré (provider.kind absent ou vide)."
            )

    engine = MemecoinStrategyEngine(
        pair_configs=pair_cfgs,
        strategy_id=strategy_id,
        exit_after_ticks=exit_after_ticks,
        provider=provider,
        logger_=log,
    )
    return engine
