# INDEX – Fichiers & scripts clés M10 GODMODE (LIVE_150)

Dernière mise à jour : 2025-12-06

Racine projet privé :  
`~/BOT_GODMODE/BOT_GODMODE`

---

## 1. Config & données runtime

### 1.1 Config globale

- `config.json`
  - `mode`, `BOT_MODE`, `RUN_MODE`, `SAFETY_MODE`
  - `profile_id` (ex. `LIVE_150`)
  - `risk` (global + wallets)
  - `wallets` (liste des wallets physiques)
  - `finance` :
    - `capital_usd`
    - `wallets` (mapping ID ↔ initial_balance_usd)
    - `policies` (fees, trading, safety_guards)
    - `fees_policy`
  - `wallet_flows` :
    - `enabled`
    - `rules` (`fees_sweep`, `profits_sweep`)
  - `strategies` :
    - `memecoin_farming`
    - `copy_trading` (stub pour plus tard)

### 1.2 Données runtime

- Dossier : `data/godmode/`
  - `wallets_runtime.json`
    - Snapshot runtime des wallets (par `RuntimeWalletManager`).
    - Contient equity totale, PnL jour, état détaillé par wallet.
  - `trades.jsonl`
    - Journal des trades papier (par `TradeStore` / `PaperTrader`).
  - (potentiellement d’autres fichiers de log/stat à venir).

---

## 2. Scripts

### 2.1 Orchestration & runtime

- `scripts/start_all.py`
  - Point d’entrée pour lancer :
    - le dashboard FastAPI (GODMODE),
    - le runtime memecoin.
  - Utilisé pour les runs 24/24 en PAPER_ONCHAIN.

- `scripts/start_bot.py`
  - Démarre l’API FastAPI (`bot/api/godmode_dashboard.py`) uniquement.

- `scripts/test_runtime_memecoin.py`
  - Lance la stratégie `memecoin_farming` en mode PAPER.
  - S’appuie sur `bot/strategies/memecoin_farming/runtime.py`.

### 2.2 Risk & debug

- `scripts/test_execution_risk_live150.py`
  - Initialise `ExecutionWithRisk` pour le profil LIVE_150.
  - Vérifie les RPC, les limites de risque globales et par wallet.

- `scripts/debug_wallets_paths.py`
  - Affiche :
    - chemin de `wallets_runtime.json`,
    - `equity_total_usd`,
    - balances & PnL par wallet.
  - Utilisé pour vérifier que tout le runtime finance tourne bien.

---

## 3. API & dashboard

### 3.1 FastAPI – GODMODE

- `bot/api/godmode_dashboard.py`
  - Router FastAPI dédié au dashboard runtime.
  - Endpoints principaux :
    - `GET /godmode/status`
      - Retourne : equity totale, PnL jour, profil, live gate, etc.
    - `GET /godmode/wallets/runtime`
      - Retourne : snapshot complet des wallets (via `wallets_runtime.json` + infos finance).
    - `GET /godmode/trades/runtime`
      - Retourne : trades runtime (paper), stats agrégées (volume, PnL, etc.).
    - `GET /godmode/alerts/finance`
      - Retourne : alertes Finance / Risk (guardrails).
  - Sert aussi les assets statiques :
    - `GET /static/index.html` (dashboard web).

### 3.2 Frontend – Dashboard runtime

- `static/index.html`
  - UI complète du dashboard runtime (HTML/CSS + JS).
  - Affiche :
    - Top tiles : Equity, Wallets, Live gate, PnL simulé.
    - Statut global (JSON /status).
    - Tableau des wallets (runtime).
    - Alertes finance.
    - Trades runtime + STAT JOUR.
  - Utilise `fetch()` vers :
    - `/godmode/status`
    - `/godmode/wallets/runtime`
    - `/godmode/trades/runtime?limit=200`
    - `/godmode/alerts/finance`

---

## 4. Wallets & finance

### 4.1 Modèles & engine

- `bot/wallets/models.py`
  - `WalletConfig`
  - `WalletState`
  - `WalletFlowsConfig`
  - `ProfitSplitRule`
  - Enum `WalletRole` (SCALPING, COPY_TRADING, VAULT, PROFITS, FEES, EMERGENCY, etc.)

- `bot/wallets/engine.py`
  - `WalletFlowsEngine`
    - Gestion des balances logiques.
    - Application des PnL par wallet.
    - Application des fees, splits de profits, caps, sweeps.

### 4.2 Factory & runtime manager

- `bot/wallets/factory.py`
  - `build_wallet_engine_from_config(conf, logger=None) -> WalletFlowsEngine`
  - Gère :
    - `config["wallets"]` (liste ou dict).
    - `config["finance"]["wallets"]` (liste ou dict) pour les `initial_balance_usd`.
    - `config["risk"]["wallets"]` pour la config de risque.
    - `config["finance"]["fees_policy"]` pour :
      - `fees_min_buffer_usd`
      - `fees_max_equity_pct`
      - `fees_over_cap_target_wallet_id`.

- `bot/wallets/runtime_manager.py`
  - `RuntimeWalletManager`
    - Construit `WalletFlowsEngine`.
    - Interface avec les stratégies (memecoin, copy trading, etc.).
    - Maintient `wallets_runtime.json` à jour.
    - Expose un résumé (equity, PnL, fees, violations) utilisé par l’API dashboard.

---

## 5. Stratégies & trading

### 5.1 Memecoin farming

- `bot/strategies/memecoin_farming/agent.py`
  - Génération de signaux memecoin (stub, provider `onchain_dry_run`).
  - Filtre avec `entry_filters` (score, liquidité, volume 24h, age token).

- `bot/strategies/memecoin_farming/runtime.py`
  - Boucle principale de la stratégie memecoin.
  - Lit la config :
    - `strategy_id = "memecoin_farming"`
    - `pairs` (ex: SOL/USDC, wallet_id `sniper_sol`, min/max notional USD).
  - Utilise :
    - `PaperTrader` (trades papier)
    - `TradeStore` (`bot/core/trade_store.py`)
    - `RuntimeWalletManager` pour propager les PnL.

### 5.2 Trading core

- `bot/core/trade_store.py`
  - Journal des trades (append-only JSONL).
  - Utilisé par :
    - `PaperTrader`
    - API `/godmode/trades/runtime`.

- `bot/trading/paper_trader.py`
  - Simule les trades (sans TX réelle).
  - Stocke dans `trades.jsonl`.
  - Retourne PnL simulé pour chaque trade.

- `bot/trading/execution_with_risk.py`
  - Moteur d’exécution encapsulé avec RiskEngine.
  - À intégrer dans le runtime memecoin (M10).

---

## 6. RiskEngine

- `bot/core/risk/...` (selon structure exacte du repo)
  - Logique de RiskEngine global :
    - Max daily loss global,
    - Max losing trades consécutifs,
    - Gestion des circuit breakers.
  - Logique de RiskEngine par wallet / stratégie.

- `scripts/test_execution_risk_live150.py`
  - Initialise l’ExecutionWithRisk pour le profil LIVE_150.
  - Test rapide de la config risk + RPC + exécution encapsulée.

---

## 7. TEMP & dépôt public

Dans le repo public `BOT_GODMODE_PUBLIC` :

- `TEMP/BLOC_CONTINUITE.md`
  - Ce fichier : journal de continuité / meta.

- `TEMP/PLAN_TRAVAIL.md`
  - Plan de travail M10 (tâches, statut).

- `TEMP/INDEX.md`
  - Index des fichiers et scripts importants.

- `TEMP/scripts/...`
  - Exports publics de certains scripts Python (`start_all.py`, `test_runtime_memecoin.py`, etc.).
  - Servent de référence “lecture seule” pour ce chat.

- `TEMP/data/godmode/wallets_runtime.json`
  - Snapshot public pour debug / partage (optionnel).

---

## 8. Cheatsheet rapide (commande & accès)

- Activer le venv + lancer bot complet :

```bash
cd ~/BOT_GODMODE/BOT_GODMODE
source ./venv/bin/activate
python scripts/start_all.py
Vérifier runtime wallets :

bash
Copier le code
python scripts/debug_wallets_paths.py
Accéder au dashboard :

URL : http://127.0.0.1:8001/static/index.html

Endpoints backend utiles (via curl, navigateur ou Postman) :

GET http://127.0.0.1:8001/godmode/status

GET http://127.0.0.1:8001/godmode/wallets/runtime

GET http://127.0.0.1:8001/godmode/trades/runtime?limit=50

GET http://127.0.0.1:8001/godmode/alerts/finance

yaml
Copier le code

---

Tu peux maintenant :

- remplacer les 3 fichiers dans `TEMP/` par ces contenus,
- pousser vers ton repo public si besoin.

Et quand tu veux, on reprend soit sur le **debug du dashboard vide** (front/API), soit on avance sur le **RiskEngine autour de la strat memecoin**.
::contentReference[oaicite:0]{index=0}
