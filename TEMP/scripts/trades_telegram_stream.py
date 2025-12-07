from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests


# =====================================================
#  CONFIG TELEGRAM (MODE BOURRIN MAIS PRATIQUE)
# =====================================================

# ⚠️ Si ça marchait déjà, NE TOUCHE À RIEN ici.
# Sinon, mets ton vrai token / chat id.
DEFAULT_BOT_TOKEN = "TON_VRAI_BOT_TOKEN_ICI"
DEFAULT_CHAT_ID = "TON_VRAI_CHAT_ID_ICI"

# Fichier de logs trades produit par le bot
TRADES_LOG_PATH = Path("data/godmode/trades.jsonl")

# Seuil pour marquer les trades "gros" (warning)
NOTIONAL_WARNING_USD = 3.0


# =====================================================
#  Telegram notifier minimal
# =====================================================

class TelegramNotifier:
    """
    Petit helper pour envoyer des messages sur Telegram.

    Utilise par défaut :
      - les variables d'env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID si définies
      - SINON les constantes DEFAULT_BOT_TOKEN / DEFAULT_CHAT_ID
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        env_token = os.getenv("TELEGRAM_BOT_TOKEN")
        env_chat = os.getenv("TELEGRAM_CHAT_ID")

        self.bot_token = bot_token or env_token or DEFAULT_BOT_TOKEN
        self.chat_id = chat_id or env_chat or DEFAULT_CHAT_ID

        self.enabled = bool(enabled and self.bot_token and self.chat_id)

        print(
            f"[telegram] BOT_TOKEN={'OK' if self.bot_token else 'MISSING'}, "
            f"CHAT_ID={'OK' if self.chat_id else 'MISSING'}"
        )

        if not self.enabled:
            print(
                "[telegram] Désactivé : aucun BOT_TOKEN/CHAT_ID valide "
                "(ni env vars, ni defaults dans le script)."
            )

    def send_text(self, text: str, silent: bool = True) -> None:
        """Envoie un simple message texte sur Telegram."""
        if not self.enabled:
            print("[telegram] send_text appelé mais notifier désactivé.")
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_notification": silent,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
        except Exception as exc:
            print(f"[telegram] Erreur requête: {exc}")
            return

        if resp.status_code != 200:
            print(f"[telegram] HTTP {resp.status_code} : {resp.text}")
            return

        try:
            data = resp.json()
        except Exception:
            print("[telegram] Réponse non JSON")
            return

        if not data.get("ok", False):
            print(f"[telegram] ok=false : {data}")
        else:
            print("[telegram] Message envoyé avec succès.")


# =====================================================
#  Helpers trades
# =====================================================

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _detect_run_mode(meta: Dict[str, Any]) -> str:
    """
    Essaie de deviner le mode d'exécution pour tagger [PAPER] / [LIVE].
    """
    candidates = [
        meta.get("run_mode"),
        meta.get("mode"),
        meta.get("exec_mode"),
    ]
    value = None
    for c in candidates:
        if c:
            value = str(c).upper()
            break

    if not value:
        return ""

    if "PAPER" in value:
        return "[PAPER]"
    if "LIVE" in value:
        return "[LIVE]"
    return f"[{value}]"


def _format_trade_message(obj: Dict[str, Any]) -> str:
    """
    Formate un trade JSONL en message texte Telegram.

    Exemple de ligne (ton cas réel) :
      {
        "id": "solana:....",
        "chain": "solana",
        "symbol": "SOL/USDC",
        "side": "buy",
        "qty": "4.16000000",
        "price": "1",
        "notional": "4.16",
        "fee": "0",
        "status": "executed",
        "created_at": "...",
        "meta": {
          "debug": "paper_test_high_score",
          "strategy": "memecoin_farming",
          "chain": "SOL",
          "score": 0.9,
          "strategy_tag": "memecoin_farming",
          "wallet_id": "sniper_sol"
        }
      }
    """

    trade_id = obj.get("id") or "?"
    chain = obj.get("chain") or obj.get("network") or "?"
    symbol = obj.get("symbol") or "?"
    side = (obj.get("side") or "?").upper()

    qty = obj.get("qty") or obj.get("quantity") or "0"
    price = obj.get("price") or "0"
    notional_raw = (
        obj.get("notional_usd")
        or obj.get("notional")
        or obj.get("value_usd")
        or "0"
    )
    notional_f = _safe_float(notional_raw, 0.0)

    status = obj.get("status") or "?"
    created_at = obj.get("created_at") or obj.get("ts") or ""

    meta = obj.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    # wallet / strat / score
    wallet_id = obj.get("wallet_id") or meta.get("wallet_id") or "?"
    strategy = (
        meta.get("strategy")
        or meta.get("strategy_name")
        or meta.get("strategy_tag")
        or "-"
    )
    score = meta.get("score")
    score_str = ""
    try:
        if score is not None:
            score_str = f" • score {float(score):.2f}"
    except Exception:
        score_str = f" • score {score}"

    # Mode (PAPER/LIVE/...)
    mode_tag = _detect_run_mode(meta)

    # PnL si dispo (peut être vide pour l'instant)
    pnl_raw = (
        obj.get("pnl_usd")
        or obj.get("realized_pnl_usd")
        or obj.get("realized_pnl_today_usd")
        or meta.get("pnl_usd")
        or None
    )

    pnl_str = ""
    if pnl_raw is not None:
        try:
            pnl_f = float(pnl_raw)
            pnl_str = f"\nPnL: {pnl_f:+.2f} $"
        except Exception:
            pnl_str = f"\nPnL: {pnl_raw}"

    # Reason / description
    reason = obj.get("reason")
    if not reason and isinstance(meta, dict):
        tags = []
        for key in (
            "signal_type",
            "source",
            "pool_type",
            "pool_name",
            "token_symbol",
        ):
            val = meta.get(key)
            if val:
                tags.append(str(val))
        if tags:
            reason = " / ".join(tags)

    reason_str = f"\nReason: {reason}" if reason else ""

    # Warning taille
    warning_str = ""
    if notional_f >= NOTIONAL_WARNING_USD:
        warning_str = f"\n⚠️ Size {notional_f:.2f}$ >= {NOTIONAL_WARNING_USD:.2f}$"

    header = f"🚀 TRADE {status.upper()}"
    if mode_tag:
        header = f"🚀 {mode_tag} TRADE {status.upper()}"

    text = (
        f"{header}\n"
        f"{wallet_id} • {strategy}{score_str}\n"
        f"{side} {symbol} ({chain})\n"
        f"Notional: {notional_f:.2f} $ (qty {qty} @ {price})\n"
        f"Time: {created_at}\n"
        f"ID: {trade_id}"
        f"{pnl_str}"
        f"{reason_str}"
        f"{warning_str}"
    )

    return text


def _follow_file(path: Path):
    """
    Génère les nouvelles lignes ajoutées au fichier (tail -f simplifié).
    """
    # On attend que le fichier existe
    while not path.exists():
        print(f"[trades_stream] En attente de {path} ...")
        time.sleep(2)

    print(f"[trades_stream] Fichier trouvé: {path}")

    with path.open("r", encoding="utf-8") as f:
        # On se place à la fin : on ne spamme pas les vieux trades
        f.seek(0, 2)

        while True:
            line = f.readline()
            if not line:
                time.sleep(1.0)
                continue
            yield line


def _send_last_trades_snapshot(notifier: TelegramNotifier, max_lines: int = 5) -> None:
    """
    Au démarrage, envoie les derniers trades présents dans trades.jsonl (si le fichier existe),
    histoire de vérifier que tout passe bien.
    """
    if not TRADES_LOG_PATH.exists():
        print(f"[snapshot] Aucun fichier {TRADES_LOG_PATH}, rien à envoyer en historique.")
        return

    try:
        lines = TRADES_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        print(f"[snapshot] Impossible de lire {TRADES_LOG_PATH}: {exc}")
        return

    if not lines:
        print("[snapshot] Fichier trades.jsonl vide.")
        return

    tail = lines[-max_lines:]
    print(f"[snapshot] Envoi des {len(tail)} derniers trades en historique...")

    for raw in tail:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception as exc:
            print(f"[snapshot] ligne illisible, ignore: {exc} | {raw[:80]}...")
            continue
        if not isinstance(obj, dict):
            continue

        msg = _format_trade_message(obj)
        notifier.send_text("📜 HISTORIQUE\n" + msg, silent=True)
        time.sleep(0.3)  # pour éviter de spammer trop vite


def main() -> None:
    notifier = TelegramNotifier()

    if not notifier.enabled:
        print(
            "[trades_stream] TelegramNotifier désactivé : "
            "aucun BOT_TOKEN/CHAT_ID utilisable."
        )
        return

    # Message de test direct au démarrage
    notifier.send_text("✅ BOT_GODMODE : Telegram notifier opérationnel.", silent=True)

    # Snapshot des derniers trades déjà présents
    _send_last_trades_snapshot(notifier, max_lines=5)

    print(f"[trades_stream] Suivi de {TRADES_LOG_PATH} → Telegram (nouveaux trades uniquement).")

    # Mode temps réel : tail -f
    for line in _follow_file(TRADES_LOG_PATH):
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except Exception as exc:
            print(f"[trades_stream] ligne illisible, ignore: {exc} | {line[:80]}...")
            continue

        if not isinstance(obj, dict):
            continue

        msg = _format_trade_message(obj)
        notifier.send_text(msg, silent=False)
        print(f"[trades_stream] trade envoyé: {obj.get('id')}")


if __name__ == "__main__":
    main()
