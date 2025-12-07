from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

# ----------------------------------------------------------------------
# Bootstrap du projet : ajouter la racine (BOT_GODMODE) au sys.path
# ----------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.core.logging import get_logger
from bot.trading.store import TradeStore, TradeStoreConfig  # type: ignore[import]

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = ROOT_DIR / "config.json"


# ======================================================================
# Helpers config / TradeStore
# ======================================================================


def load_global_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise SystemExit(f"Unable to read config file {path}: {exc}") from exc


def _extract_trade_store_section(global_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Essaie de retrouver la section de config liée au TradeStore.

    On garde ça très défensif / tolérant : plusieurs layout possibles.
    """
    if "trade_store" in global_cfg:
        raw = global_cfg["trade_store"] or {}
        if isinstance(raw, dict):
            return raw

    trading = global_cfg.get("trading") or {}
    if isinstance(trading, dict):
        store = trading.get("store") or {}
        if isinstance(store, dict):
            return store

    # Fallback : rien de spécifique, on renvoie un dict vide.
    return {}


def build_trade_store(global_cfg: Dict[str, Any]) -> TradeStore:
    """
    Construit un TradeStore de manière la plus générique possible
    en fonction de TradeStoreConfig.
    """
    cfg_section = _extract_trade_store_section(global_cfg)

    cfg_obj: Optional[TradeStoreConfig] = None

    # 1) Classmethods éventuels
    if hasattr(TradeStoreConfig, "from_dict"):
        try:
            cfg_obj = TradeStoreConfig.from_dict(cfg_section)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("TradeStoreConfig.from_dict() failed, fallback to other constructors.")
            cfg_obj = None

    if cfg_obj is None and hasattr(TradeStoreConfig, "from_config"):
        try:
            cfg_obj = TradeStoreConfig.from_config(global_cfg)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("TradeStoreConfig.from_config() failed, fallback to direct init.")
            cfg_obj = None

    # 2) Tentative via constructeur direct avec kwargs
    if cfg_obj is None:
        try:
            if isinstance(cfg_section, dict) and cfg_section:
                cfg_obj = TradeStoreConfig(**cfg_section)
        except Exception:
            logger.exception(
                "TradeStoreConfig(**cfg_section) failed, will try parameter-less constructor."
            )
            cfg_obj = None

    # 3) Fallback ultime : constructeur sans argument
    if cfg_obj is None:
        try:
            cfg_obj = TradeStoreConfig()  # type: ignore[call-arg]
        except Exception as exc:  # configuration réellement cassée
            raise SystemExit(
                "Unable to build TradeStoreConfig automatically, please adapt "
                "scripts/reset_trades.py à la signature réelle de TradeStoreConfig: "
                f"{exc}"
            ) from exc

    try:
        logger.info("TradeStoreConfig built for reset_trades: %s", asdict(cfg_obj))
    except Exception:
        # asdict peut planter si ce n'est pas une dataclass, on log juste le repr
        logger.info("TradeStoreConfig built for reset_trades: %r", cfg_obj)

    # 4) Construction du TradeStore
    store: Optional[TradeStore] = None

    if hasattr(TradeStore, "from_config"):
        try:
            store = TradeStore.from_config(cfg_obj)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("TradeStore.from_config() failed, fallback to TradeStore(cfg_obj).")
            store = None

    if store is None:
        try:
            # Constructeur qui accepte l’objet config directement
            store = TradeStore(cfg_obj)  # type: ignore[call-arg]
        except Exception:
            logger.exception("TradeStore(cfg_obj) failed, fallback to TradeStore().")
            store = None

    if store is None:
        try:
            store = TradeStore()  # type: ignore[call-arg]
        except Exception as exc:
            raise SystemExit(
                "Unable to instantiate TradeStore, please adapt build_trade_store() "
                f"à ton implémentation réelle de TradeStore: {exc}"
            ) from exc

    return store


# ======================================================================
# Helpers reset (dry-run + exécution)
# ======================================================================


def _find_method(obj: Any, candidates: Tuple[str, ...]) -> Tuple[Optional[Any], Optional[str]]:
    for name in candidates:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn, name
    return None, None


def _prepare_kwargs_for_scope(
    fn: Any,
    scope: str,
    wallet: Optional[str],
    pair: Optional[str],
) -> Dict[str, Any]:
    import inspect

    sig = inspect.signature(fn)
    params = sig.parameters.keys()

    kwargs: Dict[str, Any] = {}

    if scope in ("wallet", "pair"):
        # On cherche un param wallet
        wallet_param = None
        for cand in ("wallet_id", "wallet", "account_id"):
            if cand in params:
                wallet_param = cand
                break
        if wallet_param is None:
            raise SystemExit(
                f"Reset method {fn.__name__} does not support wallet filtering "
                f"(scope={scope}), please adapt scripts/reset_trades.py."
            )
        if not wallet:
            raise SystemExit("Scope 'wallet' or 'pair' requires --wallet.")
        kwargs[wallet_param] = wallet

    if scope == "pair":
        symbol_param = None
        for cand in ("symbol", "pair", "market", "instrument"):
            if cand in params:
                symbol_param = cand
                break
        if symbol_param is None:
            raise SystemExit(
                f"Reset method {fn.__name__} does not support pair/symbol filtering "
                f"(scope={scope}), please adapt scripts/reset_trades.py."
            )
        if not pair:
            raise SystemExit("Scope 'pair' requires --pair (ex: SOL/USDC).")
        kwargs[symbol_param] = pair

    return kwargs


def _match_record_for_dry_run(
    data: Dict[str, Any],
    scope: str,
    wallet: Optional[str],
    pair: Optional[str],
) -> bool:
    """
    Reproduit la logique de TradeStore.reset_trades pour le comptage dry-run.
    """
    wallet_id = wallet
    symbol = pair if scope == "pair" else None

    # Global : tous les trades sont concernés
    if scope == "global":
        return True

    # Filtre wallet
    if wallet_id is not None:
        meta = data.get("meta") or {}
        candidates = [
            meta.get("wallet_id"),
            meta.get("wallet"),
            meta.get("wallet_name"),
            meta.get("logical_wallet_id"),
        ]
        ok_wallet = False
        for c in candidates:
            if c is None:
                continue
            if str(c) == str(wallet_id):
                ok_wallet = True
                break
        if not ok_wallet:
            return False

    # Filtre symbol (scope=pair)
    if symbol is not None:
        sym_raw = data.get("symbol") or data.get("market")
        if str(sym_raw) != str(symbol):
            return False

    return True


def dry_run_count(
    store: TradeStore,
    scope: str,
    wallet: Optional[str],
    pair: Optional[str],
) -> int:
    """
    Compte le nombre de trades qui seraient supprimés, en lisant directement
    le fichier JSONL (sans dépendre d'une méthode de listing filtrée).
    """
    path = store.config.path  # type: ignore[attr-defined]
    if not path.exists():
        return 0

    removed = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                # Ligne illisible => on la considère non matchée
                continue

            if _match_record_for_dry_run(data, scope, wallet, pair):
                removed += 1

    return removed


def perform_reset(
    store: TradeStore,
    scope: str,
    wallet: Optional[str],
    pair: Optional[str],
    *,
    dry_run: bool,
) -> None:
    """
    Applique le reset sur le TradeStore.

    Méthodes candidates pour un vrai reset :
      - reset_trades
      - delete_trades
      - purge_trades
      - clear_trades
    """
    reset_fn, name = _find_method(
        store,
        (
            "reset_trades",
            "delete_trades",
            "purge_trades",
            "clear_trades",
        ),
    )
    if reset_fn is None:
        raise SystemExit(
            "TradeStore has no reset/delete method (reset_trades/delete_trades/purge_trades/"
            "clear_trades). Please adapt perform_reset() to your TradeStore API."
        )

    kwargs = _prepare_kwargs_for_scope(reset_fn, scope, wallet, pair)

    if dry_run:
        count = dry_run_count(store, scope, wallet, pair)
        scope_desc = scope
        if scope == "wallet":
            scope_desc = f"wallet={wallet}"
        elif scope == "pair":
            scope_desc = f"wallet={wallet}, pair={pair}"
        logger.info("[DRY-RUN] Would reset %d trades (%s).", count, scope_desc)
        return

    # Exécution réelle
    logger.warning(
        "Resetting trades on TradeStore using %s(scope=%s, wallet=%s, pair=%s)",
        name,
        scope,
        wallet,
        pair,
    )
    try:
        result = reset_fn(**kwargs)  # type: ignore[misc]
    except TypeError as exc:
        raise SystemExit(
            f"Reset method {name} could not be called with kwargs={kwargs}: {exc}. "
            "Please adapt scripts/reset_trades.py to your TradeStore API."
        ) from exc
    except Exception as exc:
        raise SystemExit(
            f"Error while calling TradeStore.{name} for reset: {exc}"
        ) from exc

    if isinstance(result, int):
        logger.info("Reset complete, %d trades affected.", result)
    else:
        logger.info("Reset complete (result type=%s).", type(result).__name__)


# ======================================================================
# CLI
# ======================================================================


def _parse_args(argv: Optional[Any] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset trades from the GODMODE TradeStore (global / per wallet / per pair)."
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config.json (default: {DEFAULT_CONFIG_PATH})",
    )

    parser.add_argument(
        "--scope",
        choices=("global", "wallet", "pair"),
        required=True,
        help=(
            "Reset scope:\n"
            "  - global : all trades\n"
            "  - wallet : trades for a given wallet\n"
            "  - pair   : trades for a given wallet + pair"
        ),
    )

    parser.add_argument(
        "--wallet",
        type=str,
        help="Wallet id/name (required for scope=wallet or scope=pair, ex: sniper_sol).",
    )

    parser.add_argument(
        "--pair",
        type=str,
        help="Pair / symbol (required for scope=pair, ex: SOL/USDC).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not actually delete anything, only log what would be done.",
    )

    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Confirm reset without interactive prompt.",
    )

    return parser.parse_args(argv)


def main(argv: Optional[Any] = None) -> None:
    args = _parse_args(argv)

    # Logging simple sur stdout
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    global_cfg = load_global_config(args.config)

    scope = args.scope
    wallet = args.wallet
    pair = args.pair

    if scope in ("wallet", "pair") and not wallet:
        raise SystemExit("Scope 'wallet' or 'pair' requires --wallet.")

    if scope == "pair" and not pair:
        raise SystemExit("Scope 'pair' requires --pair (ex: SOL/USDC).")

    dry_run = bool(args.dry_run)

    if not dry_run and not args.yes:
        scope_desc = scope
        if scope == "wallet":
            scope_desc = f"wallet={wallet}"
        elif scope == "pair":
            scope_desc = f"wallet={wallet}, pair={pair}"

        answer = input(
            f"⚠️  Confirm reset of trades ({scope_desc}) on GODMODE TradeStore? [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    store = build_trade_store(global_cfg)
    perform_reset(store, scope, wallet, pair, dry_run=dry_run)

    print("Done.")


if __name__ == "__main__":
    main(sys.argv[1:])
