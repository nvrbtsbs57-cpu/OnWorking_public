# scripts/analyze_trades_feedback.py

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bot.trading.store import TradeStore, TradeStoreConfig


CONFIG_PATH = Path("config.json")


# ---------------------------------------------------------------------------
#  Models pour les suggestions
# ---------------------------------------------------------------------------

@dataclass
class ConfigPatch:
    """
    Représente une proposition de changement sur un champ de config.
    Exemple de path: "risk.wallets.sniper_sol.max_pct_balance_per_trade"
    """
    path: str
    current: Any
    suggested: Any
    reason: str


@dataclass
class WalletStats:
    wallet_id: str
    n_trades: int
    win_rate: float
    total_pnl_usd: float
    avg_pnl_usd: float
    max_win_usd: float
    max_loss_usd: float


@dataclass
class WalletSuggestion:
    wallet_id: str
    comments: List[str]
    patches: List[ConfigPatch]


@dataclass
class AnalysisResult:
    generated_at: str
    window_days: int
    global_comments: List[str]
    wallets: List[WalletSuggestion]


# ---------------------------------------------------------------------------
#  Utils
# ---------------------------------------------------------------------------

def _d(x: Any) -> Decimal:
    if x is None:
        return Decimal("0")
    return Decimal(str(x))


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_store() -> TradeStore:
    # on réutilise la config par défaut du reste du projet (data/godmode/trades.jsonl)
    return TradeStore(TradeStoreConfig())


# ---------------------------------------------------------------------------
#  Analyse des trades
# ---------------------------------------------------------------------------

def load_trades_since(store: TradeStore, since: datetime):
    """
    Retourne tous les trades depuis `since`.
    On passe par get_recent_trades(limit=...) puis on filtre côté Python.
    Si tu veux optimiser plus tard, tu pourras ajouter une méthode dédiée
    dans TradeStore.
    """
    # on prend large (ex: 10000) et on filtrera
    trades = store.get_recent_trades(limit=10000)
    out = []
    for t in trades:
        # on suppose un attrib t.closed_at ou t.opened_at (à adapter si besoin)
        dt = getattr(t, "closed_at", None) or getattr(t, "opened_at", None)
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except Exception:
                dt = None
        if not isinstance(dt, datetime):
            # si pas de timestamp exploitable, on garde quand même
            out.append(t)
            continue
        if dt >= since:
            out.append(t)
    return out


def compute_wallet_stats(trades) -> Dict[str, WalletStats]:
    """
    Agrège des stats simples par wallet_id.
    On suppose que chaque Trade a un champ wallet_id (sinon adapter à ta structure).
    """
    by_wallet: Dict[str, List[Any]] = {}

    for t in trades:
        wallet_id = getattr(t, "wallet_id", None) or getattr(t, "account_id", "unknown")
        by_wallet.setdefault(wallet_id, []).append(t)

    stats: Dict[str, WalletStats] = {}

    for wid, w_trades in by_wallet.items():
        n = len(w_trades)
        if n == 0:
            continue

        pnls: List[Decimal] = []
        wins = 0
        max_win = Decimal("0")
        max_loss = Decimal("0")

        for t in w_trades:
            pnl = _d(
                getattr(t, "realized_pnl_usd", None)
                or getattr(t, "realized_pnl", None)
                or getattr(t, "pnl_usd", None)
                or getattr(t, "pnl", None)
            )
            pnls.append(pnl)

            if pnl > 0:
                wins += 1
                if pnl > max_win:
                    max_win = pnl
            if pnl < 0 and pnl < max_loss:
                max_loss = pnl

        total_pnl = sum(pnls) if pnls else Decimal("0")
        avg_pnl = total_pnl / Decimal(len(pnls)) if pnls else Decimal("0")
        win_rate = (wins / n) * 100 if n > 0 else 0.0

        stats[wid] = WalletStats(
            wallet_id=wid,
            n_trades=n,
            win_rate=float(win_rate),
            total_pnl_usd=float(total_pnl),
            avg_pnl_usd=float(avg_pnl),
            max_win_usd=float(max_win),
            max_loss_usd=float(max_loss),
        )

    return stats


# ---------------------------------------------------------------------------
#  Génération de suggestions à partir des stats + config
# ---------------------------------------------------------------------------

def generate_suggestions(
    stats_by_wallet: Dict[str, WalletStats],
    cfg: Dict[str, Any],
) -> AnalysisResult:
    wallets_cfg = cfg.get("finance", {}).get("wallets", {}).get("initial_balances_usd", {}) or {}
    risk_wallets_cfg = cfg.get("risk", {}).get("wallets", {}) or {}

    global_comments: List[str] = []
    wallet_suggestions: List[WalletSuggestion] = []

    # petit commentaire global
    total_pnl_global = sum(s.total_pnl_usd for s in stats_by_wallet.values())
    total_trades_global = sum(s.n_trades for s in stats_by_wallet.values())
    if total_trades_global > 0:
        global_comments.append(
            f"[GLOBAL] {total_trades_global} trades analysés, PnL cumulé ≈ {total_pnl_global:.2f} USD."
        )
    else:
        global_comments.append("[GLOBAL] Aucun trade dans la fenêtre analysée.")

    for wid, s in stats_by_wallet.items():
        comments: List[str] = []
        patches: List[ConfigPatch] = []

        initial_capital = float(wallets_cfg.get(wid, 0.0))
        risk_cfg = risk_wallets_cfg.get(wid, {}) or {}
        max_pct_per_trade = float(risk_cfg.get("max_pct_balance_per_trade", 0.0))
        max_daily_loss_pct = float(risk_cfg.get("max_daily_loss_pct", 0.0))

        # 1) Commentaire de base
        comments.append(
            f"[{wid}] {s.n_trades} trades, winrate {s.win_rate:.1f}%, "
            f"PnL total {s.total_pnl_usd:.2f} USD (avg {s.avg_pnl_usd:.2f} USD/trade)."
        )

        # 2) Heuristiques simples

        #   a) Wallet performant et sous-exposé
        if s.n_trades >= 10 and s.win_rate >= 60.0 and s.total_pnl_usd > 0:
            comments.append(
                f"[{wid}] Performant sur la période (winrate >= 60% et PnL positif). "
                "On peut envisager d'augmenter légèrement le risque par trade."
            )
            if max_pct_per_trade > 0 and max_pct_per_trade < 30.0:
                new_val = min(max_pct_per_trade + 5.0, 30.0)
                patches.append(
                    ConfigPatch(
                        path=f"risk.wallets.{wid}.max_pct_balance_per_trade",
                        current=max_pct_per_trade,
                        suggested=new_val,
                        reason="Wallet performant sur la période (winrate élevé + PnL > 0).",
                    )
                )

        #   b) Wallet en difficulté
        if s.n_trades >= 10 and s.win_rate < 35.0 and s.total_pnl_usd < 0:
            comments.append(
                f"[{wid}] Faible performance (winrate < 35% et PnL négatif). "
                "À surveiller : soit ajuster la stratégie, soit réduire le risque, voire désactiver."
            )
            if max_pct_per_trade > 0:
                new_val = max(max_pct_per_trade - 5.0, 5.0)
                if new_val < max_pct_per_trade:
                    patches.append(
                        ConfigPatch(
                            path=f"risk.wallets.{wid}.max_pct_balance_per_trade",
                            current=max_pct_per_trade,
                            suggested=new_val,
                            reason="Réduire l'exposition sur un wallet perdant.",
                        )
                    )

        #   c) Grosses pertes ponctuelles vs capital
        if initial_capital > 0 and s.max_loss_usd < 0:
            loss_ratio = abs(s.max_loss_usd) / initial_capital * 100
            if loss_ratio > 20.0:
                comments.append(
                    f"[{wid}] Perte max {s.max_loss_usd:.2f} USD "
                    f"({loss_ratio:.1f}% du capital de départ) : drawdown agressif."
                )
                if max_daily_loss_pct > 0:
                    new_val = max(min(max_daily_loss_pct, 15.0), 5.0)
                    if new_val < max_daily_loss_pct:
                        patches.append(
                            ConfigPatch(
                                path=f"risk.wallets.{wid}.max_daily_loss_pct",
                                current=max_daily_loss_pct,
                                suggested=new_val,
                                reason="Limiter le drawdown journalier sur ce wallet.",
                            )
                        )

        #   d) Trades trop petits par rapport au capital
        if initial_capital > 0 and s.n_trades >= 5:
            # si PnL moyen/trade est très petit (< 0.10 USD) mais capital correct
            if abs(s.avg_pnl_usd) < 0.10 and initial_capital >= 20:
                comments.append(
                    f"[{wid}] PnL moyen par trade très faible ({s.avg_pnl_usd:.2f} USD) "
                    "→ ordres probablement trop petits par rapport au capital disponible."
                )
                # on pourrait proposer d'augmenter min_notional_usd côté stratégie,
                # mais la config est dans strategies.* ; on se contente d'un commentaire.

        wallet_suggestions.append(
            WalletSuggestion(
                wallet_id=wid,
                comments=comments,
                patches=patches,
            )
        )

    return AnalysisResult(
        generated_at=datetime.utcnow().isoformat(),
        window_days=0,  # rempli dans main()
        global_comments=global_comments,
        wallets=wallet_suggestions,
    )


# ---------------------------------------------------------------------------
#  Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyse les trades GODMODE et génère des suggestions de config."
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=3,
        help="Nombre de jours de trades à analyser (par défaut: 3).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/godmode/config_suggestions.json",
        help="Chemin du fichier JSON de sortie pour les suggestions.",
    )
    args = parser.parse_args()

    since = datetime.utcnow() - timedelta(days=args.window_days)
    store = build_store()
    cfg = load_config()

    trades = load_trades_since(store, since=since)
    stats_by_wallet = compute_wallet_stats(trades)
    result = generate_suggestions(stats_by_wallet, cfg)
    result.window_days = args.window_days

    # Print résumé lisible
    print("=" * 72)
    print(f"Analyse GODMODE – fenêtre {args.window_days} jours (depuis {since.isoformat()})")
    print("=" * 72)
    for line in result.global_comments:
        print(line)
    print()

    for ws in result.wallets:
        print("-" * 72)
        for c in ws.comments:
            print(c)
        if ws.patches:
            print("  Patches suggérés :")
            for p in ws.patches:
                print(
                    f"    - {p.path}: {p.current} → {p.suggested} "
                    f"(raison: {p.reason})"
                )
        else:
            print("  Aucun patch config suggéré (observation uniquement).")
        print()

    # Sauvegarde JSON
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    json_data = {
        "generated_at": result.generated_at,
        "window_days": result.window_days,
        "global_comments": result.global_comments,
        "wallets": [
            {
                "wallet_id": ws.wallet_id,
                "comments": ws.comments,
                "patches": [asdict(p) for p in ws.patches],
            }
            for ws in result.wallets
        ],
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print("=" * 72)
    print(f"Suggestions sauvegardées dans : {out_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
