---

## 2025-12-07 – Runtime memecoin + Dashboard GODMODE en mode FULL PAPER

- Stack lancée :
  - backend dashboard FastAPI (`godmode_dashboard.py`) → endpoints `/godmode/status`, `/godmode/wallets/runtime`, `/godmode/trades/runtime`, `/godmode/alerts/finance`
  - frontend `index.html` ouvert dans le navigateur (profil LIVE_150 sélectionné)
  - script `scripts/test_memecoin_runtime_live150.py --sleep 5` en parallèle (boucle infinie).

- Résultats observés :
  - Le dashboard affiche :
    - equity totale ≈ **150$**
    - PnL jour ≈ **0$** (normal avec prix stub = 1.0)
    - MODE LIVE = **ON (autorisé)**, M10 encore marqué “UNKNOWN”
    - tableau `Wallets runtime` : 10 wallets, balances cohérentes avec `config.finance.wallets` (sniper_sol/copy_sol à 30$).
  - Logs backend :
    - les requêtes HTTP `/godmode/status`, `/godmode/wallets/runtime`, `/godmode/trades/runtime`, `/godmode/alerts/finance` passent bien en 200
    - `TradeStore` est initialisé sur `data/godmode/trades.jsonl`
    - les trades PAPER memecoin (buy/sell SOL/USDC sur sniper_sol) sont écrits régulièrement.

- Fichiers runtime confirmés :
  - `data/godmode/trades.jsonl` → contient des trades memecoin_farming avec `source: "memecoin_farming"` et `source: "stub_random"`.
  - `data/godmode/wallets_runtime.json` → equity_total_usd = 150.0, wallets_count = 10, snapshots corrects pour `sniper_sol` et `copy_sol`.

- Points encore à améliorer côté dashboard :
  - certains champs restent à `UNKNOWN` (`chain`, `role` générique)
  - footer “Wallet fees : aucune info (fees_wallet non trouvé)” → à aligner avec `finance.fees_policy.fees_wallet_id`
  - faire apparaître clairement les caps fees / risk_wallets et les alertes associées.

- Décision :
  - considérer cette stack (memecoin runtime + dashboard) comme **référence M10 PAPER_ONCHAIN**.
  - les prochains travaux portent surtout sur :
    - figer les règles LIVE_150 (fees, caps, safety_guards) dans `live_policies.py`
    - brancher les garde-fous risk / kill switch au-dessus de l’ExecutionEngine
    - préparer l’indexer on-chain (QuickNode) pour remplacer le provider stub à terme.

---

## 2025-12-07 – RPC QuickNode (rappel config)

- `config.json.rpc` définit pour chaque chain :
  - `http_url_env` (ex : `RPC_SOLANA_HTTP`)
  - `ws_url_env` (ex : `RPC_SOLANA_WS`)
- Sur la VM, ces variables d’environnement sont déjà renseignées avec les endpoints QuickNode (clé payante).
- À utiliser plus tard par :
  - l’indexer (`bot/bot_core/indexer/*`)
  - tout futur `MemecoinDataProvider` on-chain.

