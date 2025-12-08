# INDEX – BOT_GODMODE M10 (LIVE_150, PAPER_ONCHAIN)

Dernière mise à jour : 2025-12-08  
Racine projet privé : `~/BOT_GODMODE/BOT_GODMODE`

Objectif M10 : faire tourner le bot **en PAPER_ONCHAIN avec exactement la tuyauterie LIVE**,  
juste avec `PaperTrader` + `tx_guard.hard_disable_send_tx = true`.

Pipeline complet :

1. `config.json` = vérité métier (wallets, finance, risk, stratégies, RPC, etc.).
2. `bot/wallets/*` = RuntimeWalletManager + WalletFlowsEngine (equity, PnL, flows, fees).
3. `bot/trading/*` = PaperTrader + ExecutionEngine (+ ExecutionWithRisk plus tard).
4. `bot/strategies/*` + `bot/agent/*` = signaux memecoin / copy, agent & risk.
5. `bot/api/godmode_dashboard.py` + `static/index.html` = dashboard GODMODE.
6. Scripts M10 (`start_godmode_m10.py`, `run_m10_memecoin_runtime.py`, tests) orchestrent le tout.

---

## 1. Config & données runtime

### 1.1 `config.json` – Source de vérité LIVE_150 

- Clés globales :
  - `"mode"`, `"BOT_MODE"`, `"RUN_MODE"`  
    - ex : `mode="GODMODE"`, `RUN_MODE="paper"` → M10 PAPER_ONCHAIN.
  - `"SAFETY_MODE"` : `safe` / `normal` / `degen`  
    → influence les tailles max, les strats autorisées, etc.
  - `"profile_id"` / `"finance.profile_id"` : `LIVE_150`.

- Section `"execution"` :
  - `"mode": "GODMODE"` : exécution encapsulée GODMODE.
  - `"tx_guard"` :
    - `"hard_disable_send_tx": true` → **aucune TX envoyée** en M10.
    - `"allowed_profiles": ["LIVE_150"]` → profils autorisés pour LIVE.
    - `"log_only"` : si `true` → log sans hard block.

- Section `"chains"` :
  - Déclaration des chaînes : `ethereum`, `base`, `bsc`, `solana`, `arbitrum`.
  - Pour chaque :
    - `chain_id`
    - `native_symbol` (ETH, SOL, BNB…)
    - `explorer_base_url` (Etherscan, Basescan, BscScan, Solscan…).:contentReference[oaicite:1]{index=1}

- Section `"rpc"` :
  - Map chaîne → variables d’environnement QuickNode :
    - `rpc.ethereum.http_url_env = "RPC_ETHEREUM_HTTP"` (etc)
    - `rpc.solana.http_url_env = "RPC_SOLANA_HTTP"`, `rpc.solana.ws_url_env = "RPC_SOLANA_WS"`, etc.
  - Utilisé par :
    - `bot/bot_core/indexer/rpc_client.py`
    - plus tard par `MemecoinDataProvider` on-chain.

- Section `"indexer"` (désactivée pour l’instant) :contentReference[oaicite:2]{index=2} :
  - `enabled`, `poll_interval_seconds`, `storage_path`.
  - Par chaîne :
    - `start_block` / `start_slot`
    - `step_blocks` / `step_slots`
    - `max_blocks_per_poll`.
  - Cible : scanner la vraie blockchain avec QuickNode → stocker dans `data/indexer/`.

- Section `"agent"` :
  - `"mode": "memecoin"` : agent en mode memecoin farming.
  - `tick_interval_seconds`, `max_signals_per_tick`, `min_confidence_for_signal`.
  - Utilisé par `bot/agent/engine.py`.

- Section `"api"` + `"dashboard"` :
  - Ports & host :
    - API (backend) : host 127.0.0.1, port 8000.
    - Dashboard : host 127.0.0.1, port 8001.

- Section `"risk"` :
  - `"global"` :
    - `max_global_daily_loss_pct`
    - `max_consecutive_losing_trades`
    → utilisé par `RiskEngine` et le kill switch.:contentReference[oaicite:3]{index=3}
  - `"wallets"` :
    - pour chaque wallet logique (`sniper_sol`, `copy_sol`, etc.) :
      - `max_pct_balance_per_trade`
      - `max_daily_loss_pct`
      - `max_open_positions`
      - `max_notional_per_asset`

- Section `"wallets"` (physiques / logiques, LIVE_150) :contentReference[oaicite:4]{index=4} :
  - 10 wallets :
    - `sniper_sol` (SCALPING, Solana)
    - `copy_sol` (COPY_TRADING, Solana)
    - `base_main` (SCALPING, Base)
    - `bsc_main` (SCALPING, BSC)
    - `vault` (VAULT, Base)
    - `profits_sol` (PROFITS, Solana)
    - `profits_base` (PROFITS, Base)
    - `profits_bsc` (PROFITS, BSC)
    - `fees` (FEES, Base)
    - `emergency` (EMERGENCY, Ethereum)
  - Pour chacun :
    - `role`, `chain`, `address`, `private_key_env`
    - sous-section `risk` (caps en USD + max_open_trades)
    - `tags` (sniper, copy, vault, fees…).

- Section `"wallet_roles"` :
  - Rôles → liste de wallets (SCALPING, COPY_TRADING, VAULT, PROFITS, FEES, EMERGENCY).

- Section `"finance"` (cœur LIVE_150)  :
  - `capital_usd = 150.0`
  - `wallets` :
    - mapping `wallet_id` → `initial_balance_usd`
    - ex : sniper_sol / copy_sol / base_main / bsc_main = 30$, vault = 10$, fees = 10$, emergency = 10$.
  - `"autofees"` :
    - min_gas_native / target_gas_native par chaîne
    → utilisé par le **module autofees** plus tard (renflouement gas).
  - `"sweep"` :
    - `enabled`, `min_amounts` par chaîne
    - `to_vault_pct`, `to_profits_pct`
    → politique de sweep automatique des profits vers vault + profits wallets.
  - `"compounding"` :
    - `min_pnl_usd`
    - `max_compound_pct`
    → combien des profits repart dans les wallets de trading.
  - `"policies"` :
    - `fees` : buffer fees (min / target / max)
    - `fees_sweeps` : partage des fees (profits vs vault) + cooldown
    - `profits` : partage vers vault vs trading
    - `trading` : max positions, % du wallet par trade, max daily loss, etc.
    - `safety_guards` : warning/critical drawdown, max consecutive losers, min_operational_capital.
  - `"fees_policy"` :
    - `fees_wallet_id` = `"fees"`
    - `min_buffer_usd`, `max_equity_pct`
    - `sweep_targets` (`fees_over_cap` → `vault`).
  - `"risk_wallets"` :
    - cap d’equity par wallet (sniper_sol, copy_sol, base_main, bsc_main, fees).

- Section `"wallet_flows"` :
  - `enabled`, `tick_interval_seconds`
  - `rules` :
    - `fees_sweep` : source `fees` → `vault` + `profits_base`.
    - `profits_sweep` : source `profits_*` → `vault`.

- Section `"strategies"` :
  - `"memecoin_farming"` :
    - `strategy_id = "memecoin_farming"`
    - `exit_after_ticks`
    - `entry_filters` : min_score, min_liquidity_usd, min_volume_24h_usd, max_token_age_minutes.
    - `provider.kind = "onchain_dry_run"` (aujourd’hui : stub random)
    - `pairs` : ex. `SOL/USDC` sur `sniper_sol`, min/max notional USD.
  - `"copy_trading"` :
    - `masters` : `stub_master_1` → `copy_sol`
    - `feed.kind = "stub_random"`.

---

### 1.2 Données runtime – `data/godmode/` 

- Dossier : `data/godmode/`
  - `wallets_runtime.json`
    - Snapshot runtime des wallets (produit par `RuntimeWalletManager`).
    - Contient :
      - `updated_at`, `wallets_count`, `equity_total_usd`
      - pour chaque wallet :
        - `balance_usd`
        - `realized_pnl_today_usd`
        - `gross_pnl_today_usd`
        - `fees_paid_today_usd`
        - `consecutive_losing_trades`
        - `last_reset_date`.
  - `trades.jsonl`
    - Journal des trades PAPER (memecoin & copy) → append-only JSONL.
    - Utilisé par `TradeStore` + API `/godmode/trades/runtime`.
  - `execution_runtime.json` (optionnel)
    - stats risk/execution : drawdown, kill_switch, risk_enabled, etc.
    - Utilisé pour les alertes risk dans le dashboard.

---

## 2. Scripts d’orchestration & runtime

### 2.1 Entrées principales M10

- `scripts/start_godmode_m10.py`   
  - Objectif : **entrée principale unique** M10.
  - Charge `config.json` (profil LIVE_150).
  - Démarre :
    - backend dashboard FastAPI (`godmode_dashboard.py`) sur 8001,
    - runtime memecoin M10 (via `run_m10_memecoin_runtime.py` ou équivalent),
    - éventuellement d’autres jobs (finance, alerts).
  - Remplacement à terme de `start_all.py`.

- `scripts/run_m10_memecoin_runtime.py`  
  - Démarre le **runtime memecoin M10** :
    - `from bot.strategies.memecoin_farming.runtime import build_default_runtime`
    - construit le runtime complet :
      - `RuntimeWalletManager.from_config(cfg)`
      - `PaperTrader` (profil LIVE_150)
      - `ExecutionEngine(inner_engine=PaperTrader, wallet_manager=RuntimeWalletManager)`
      - `MemecoinStrategyEngine` (provider stub/onchain_dry_run)
    - boucle infinie :
      - `wallet_manager.on_tick()`
      - `signals = memecoin_engine.next_signals()`
      - exécution des signaux via `ExecutionEngine`.

- `scripts/start_godmode_all.sh` / `scripts/start_all.py` :contentReference[oaicite:8]{index=8}  
  - Ancienne entrée “tout-en-un” :
    - démarre dashboard + runtime(s).
  - En M10, `start_godmode_m10.py` est la version **ciblée** LIVE_150.

### 2.2 Scripts de test / debug pipeline

- `scripts/test_memecoin_runtime_live150.py`   
  - Version test du runtime memecoin pour LIVE_150.
  - Pipeline :
    1. charge `config.json`
    2. construit `RuntimeWalletManager.from_config(cfg)`
    3. construit `PaperTraderConfig.from_env()` + `PaperTrader`
    4. crée `ExecutionEngine(inner_engine=paper_trader, wallet_manager=runtime_wallet_manager)`
    5. crée `MemecoinStrategyEngine` via `build_memecoin_strategy_from_config(cfg)`
    6. boucle :
       - `runtime_wallet_manager.on_tick()`
       - `signals = memecoin_engine.next_signals()`
       - `exec_engine.execute_signal(signal)`
       - relit `wallets_runtime.json` pour log rapide.
  - Résultat : trades PAPER memecoin dans `data/godmode/trades.jsonl`, equity 150$, 10 wallets.

- `scripts/smoke_test_m10_pipeline.py` :contentReference[oaicite:10]{index=10}  
  - Valide le pipeline M10 “de bout en bout” rapide.

- `scripts/test_agent_events_pipeline_with_wallets.py`
  - Teste la pipeline **Agent → Events → TradeSignals → ExecutionEngine → Wallets**.

- `scripts/test_memecoin_runtime.py`
  - Ancienne version du runtime memecoin (profil plus simple).

---

## 3. API & Dashboard GODMODE

### 3.1 Backend – FastAPI GODMODE 

- `bot/api/godmode_dashboard.py`
  - FastAPI app pour le dashboard runtime.
  - Endpoints principaux :
    - `GET /godmode/status` :
      - equity totale, PnL jour, profil, mode LIVE/PAPER, health.
    - `GET /godmode/wallets/runtime` :
      - snapshot complet des wallets (`wallets_runtime.json` + infos finance).
    - `GET /godmode/trades/runtime?limit=N` :
      - trades runtime (paper) + stats agrégées.
    - `GET /godmode/alerts/finance` :
      - alertes issues du Finance Engine / RiskEngine.
  - Sert les assets statiques :
    - `GET /static/index.html` (dashboard web).

- `bot/api/http_api.py`, `bot/api/server.py`, `bot/api/models.py`
  - Serveur HTTP générique & modèles de réponse (backend interne).

- `scripts/start_bot.py`  
  - Démarre uniquement l’API FastAPI / dashboard (sans runtime).

### 3.2 Frontend – Dashboard runtime

- `static/index.html` 
  - UI complète GODMODE :
    - Tiles : Equity totale, PnL jour, mode LIVE/PAPER, M10 status.
    - Tableau `Wallets runtime` : chain, rôle, balance, % equity, PnL jour.
    - Tableau des trades : heure, pair, side, notional, raison (meta.strategy, meta.exit_reason…).
    - Alertes finance : buffers fees, caps risk_wallets, safety_guards, kill switch.
  - Utilise `fetch()` vers :
    - `/godmode/status`
    - `/godmode/wallets/runtime`
    - `/godmode/trades/runtime?limit=200`
    - `/godmode/alerts/finance`.

---

## 4. Wallets & Finance

### 4.1 Modèles & engine wallets 

- `bot/wallets/models.py`
  - `WalletConfig` : config d’un wallet (role, chain, risk…).
  - `WalletState` : state runtime (balance, PnL, equity_pct…).
  - `WalletFlowsConfig`, `ProfitSplitRule` :
    - config des flows entre wallets (fees_sweep, profits_sweep).
  - Enum `WalletRole` :
    - SCALPING, COPY_TRADING, VAULT, PROFITS, FEES, EMERGENCY, etc.

- `bot/wallets/engine.py`
  - `WalletFlowsEngine` :
    - applique :
      - PnL par wallet,
      - fees,
      - sweeps (profits/fees),
      - compounding.
    - tient à jour les balances logiques des 10 wallets.

- `bot/wallets/flows.py`
  - Implémente les règles déclarées dans `config.finance.policies` & `config.wallet_flows.rules` :
    - `fees_sweeps` (source `fees` → `vault` + `profits_base`),
    - `profits_sweep` (profits_* → vault),
    - compounding (ré-injection d’une partie des profits dans les wallets de trading).

### 4.2 Factory & RuntimeManager

- `bot/wallets/factory.py` 
  - `build_wallet_engine_from_config(conf, logger=None) -> WalletFlowsEngine`
  - Branche :
    - `config["wallets"]` (liste/dict de wallets logiques),
    - `config["finance"]["wallets"]` (initial_balance_usd),
    - `config["risk"]["wallets"]`,
    - `config["finance"]["fees_policy"]` (buffers fees, caps & target wallet),
    - `config["finance"]["policies"]` (fees/profits/trading/safety_guards).

- `bot/wallets/runtime_manager.py`
  - `RuntimeWalletManager` :
    - wrap `WalletFlowsEngine`.
    - API principale pour le reste du bot :
      - `on_tick()` :
        - appelle les tâches périodiques (sweeps, compounding…)
        - écrit `data/godmode/wallets_runtime.json`.
      - `on_trade_closed(wallet_id, pnl_usd, fees_usd, meta)` :
        - applique PnL, met à jour equity et stats day.
    - Fournit un summary utilisé par :
      - API `/godmode/wallets/runtime`,
      - dashboard GODMODE.

### 4.3 Finance Engine & jobs 

- `bot/finance/engine.py`
  - Nouvel **engine finance** :
    - interprète `config.finance.policies.*` + `fees_policy` + `risk_wallets`.
    - produit des actions/flows high-level :
      - renflouer fees,
      - envoyer profits vers vault,
      - limiter l’equity des wallets de trading, etc.

- `bot/finance/live_policies.py`
  - Centralise les constantes LIVE (profil `LIVE_150`) :
    - buffers fees (min/target/max),
    - caps equity fees & risk_wallets,
    - safety_guards (warning/critical drawdown, losing streak, min_operational_capital).
  - Utilisé par :
    - `WalletFlowsEngine`,
    - alertes finance,
    - kill switch.

- `bot/finance/pipeline.py`
  - Branche :
    - snapshots wallets,
    - trades,
    - policies,
  - et planifie les actions (sweeps, compounding…).

- Scripts associés :
  - `scripts/run_finance_jobs.py`, `scripts/run_finance_jobs_vm.sh`
  - `scripts/monitor_m10_finance.py`
  - `scripts/test_finance_*` (snapshot, profit_split, pipeline, wallet_flows…).

---

## 5. Trading & Exécution (PAPER_ONCHAIN)

### 5.1 Trading core & store 

- `bot/trading/models.py`
  - Modèles de base trading :
    - `TradeSide`, `OrderType`, `ExecutionRequest`, `ExecutionResult`, etc.
  - Types partagés par `ExecutionEngine`, `PaperTrader`, `ExecutionWithRisk`.

- `bot/trading/paper_trader.py`
  - Moteur d’exécution PAPER :
    - prend des `TradeSignal` (voir `bot/core/signals.py`),
    - simule le prix et les fees,
    - écrit dans `TradeStore` (`data/godmode/trades.jsonl`).

- `bot/trading/store.py` (ou `bot/core/trade_store.py` selon version)  
  - `TradeStore` :
    - append-only JSONL,
    - calcule PnL par trade / par jour,
    - sert de base pour :
      - dashboard `/godmode/trades/runtime`,
      - analyses (scripts d’analyse PnL).

### 5.2 ExecutionEngine & Risk

- `bot/trading/execution.py` 
  - `ExecutionEngine` :
    - interface unique pour exécuter des `TradeSignal`.
    - encapsule :
      - `inner_engine` (PaperTrader en M10),
      - `RuntimeWalletManager`.
    - pipeline standard :
      1. vérifie le signal / input,
      2. appelle `inner_engine.execute_signal(...)`,
      3. mappe le résultat vers le wallet concerné,
      4. appelle `RuntimeWalletManager.on_trade_closed(...)`.

- `bot/trading/execution_with_risk.py`
  - Variante d’ExecutionEngine avec **RiskEngine** :
    - applique les règles globales & par wallet :
      - max daily loss global,
      - max daily loss par wallet,
      - max_consecutive_losers,
      - etc.
    - renvoie ACCEPT / ADJUST / REJECT avant l’appel au moteur interne.
  - Utilisé par :
    - `scripts/test_execution_risk_live150.py`,
    - les futurs runtimes LIVE.

- `bot/core/tx_guard.py` / `tx_guard.py` 
  - Garde-fou au-dessus des exécutions réelles :
    - check du profil (`LIVE_150`),
    - mode `hard_disable_send_tx` (M10),
    - autorisation de chaîne / DEX.
  - En M10 :
    - garde tout le pipeline identique au LIVE
    - mais bloque l’envoi réel des TX.

---

## 6. Agent & Stratégies

### 6.1 Agent “cerveau” 

- Dossier : `bot/agent/`
  - `engine.py` → `AgentEngine` :
    - lit des évènements (signaux memecoin, copy feed, alertes risk…),
    - construit des `TradeSignal`,
    - route vers `ExecutionEngine`.
  - `signals.py` :
    - types de signaux internes (memecoin_entry, memecoin_exit, copy_trade, etc.).
  - `modes.py` :
    - modes de l’agent (memecoin, copy_only, mixed…).
  - `state.py` :
    - état interne de l’agent (positions ouvertes, PnL, cooldowns).
  - `risk_engine.py` :
    - couche risk au niveau de l’agent (max signaux par tick, seuils de confiance…).
  - `alerts_engine.py` :
    - génère des alertes (over-risk, anomalies).
  - `router.py` :
    - dispatch des signaux vers les bons wallets / exécuteurs.
  - `position_profiles.py` :
    - profils de taille / gestion de position (taille fixe, % du wallet, etc.).

### 6.2 Stratégie memecoin_farming 

- `bot/strategies/memecoin_farming/agent.py`
  - Types :
    - `MemecoinPairConfig`, `MemecoinCandidate`, etc.
  - `MemecoinStrategyEngine` :
    - gère ENTRY/EXIT time-based (ex : `exit_after_ticks = 4`).
    - applique `entry_filters` :
      - min_score, min_liquidity_usd, min_volume_24h_usd, max_token_age_minutes.
  - Provider stub :
    - `StubRandomMemecoinProvider` (aujourd’hui pour M10).
    - support cible : provider réel `"onchain"` branché sur l’indexer.
  - `build_memecoin_strategy_from_config(raw_cfg, logger_)` :
    - lit `config.strategies.memecoin_farming`,
    - branche provider kind (`stub_random`, `onchain_dry_run`, futur `onchain`).

- `bot/strategies/memecoin_farming/runtime.py`
  - **Boucle principale** de la stratégie memecoin M10 :
    - construit `RuntimeWalletManager`, `PaperTrader`, `ExecutionEngine`, `MemecoinStrategyEngine`.
    - boucle :
      - tick wallets (`on_tick`),
      - génération de signaux memecoin,
      - exécution via `ExecutionEngine`.
  - Utilisé par :
    - `scripts/test_memecoin_runtime_live150.py`,
    - `scripts/run_m10_memecoin_runtime.py`.

### 6.3 Stratégie copy_trading

- `bot/strategies/copy_trading/agent.py` 
  - Feed stub (copy aléatoire) vers le wallet `copy_sol`.
  - Cible : suivre de vrais wallets on-chain (à brancher sur un indexer ou un provider copy réel).
  - Utilisé par :
    - `scripts/test_agent_events_pipeline_with_wallets.py`,
    - futurs runtimes M10 lorsque le copy sera activé.

---

## 7. Indexer & données on-chain (QuickNode)

- Dossier : `bot/bot_core/indexer/*` 
  - `engine.py` / `indexer_engine.py` :
    - boucle de scan on-chain (EVM + Solana).
  - `clients/evm_client.py` :
    - client EVM basé sur RPC QuickNode.
  - `rpc_client.py` :
    - wrapper générique RPC (utilise `config.rpc.*` + env QuickNode).
  - `storage.py` :
    - stockage des événements indexés dans `data/indexer/`.
  - `evm_log_decoder.py` :
    - décodage des logs EVM (transfer, swap…).
  - `token_metadata.py`, `whale_scanner.py` :
    - métadonnées tokens, scan des whales.

- Dossier : `bot/bot_core/normalizer/*`
  - `normalizer_engine.py`, `aggregator.py`, `patterns.py`, `liquidity_map.py`, `whale_normalizer.py`
  - Transforme les données brutes de l’indexer en signaux utilisables par :
    - `MemecoinStrategyEngine` (liquidité, volume, age, patterns),
    - `copy_trading` (whale moves).

Statut M10 actuel :
- indexer **prêt mais désactivé** (`indexer.enabled = false`),
- provider memecoin `onchain_dry_run` (stub),
- objectif : activer l’indexer en lecture seule, valider, puis passer le provider en `"onchain"`.

---

## 8. RiskEngine & Safety Guards

- `bot/core/risk.py` + `bot/agent/risk_engine.py` 
  - Règles de risque :
    - global (drawdown, losing streak global),
    - par wallet (caps notional/daily loss),
    - par stratégie (budget, max trades par période).
  - Interface typique :
    ```text
    risk_check(wallet_id, strategy_id, chain_id, notional, stop_loss, take_profit, ...)
      -> ACCEPT / ADJUST / REJECT
    ```

- `scripts/test_execution_risk_live150.py`, `scripts/test_kill_switch_live150.py`
  - Tests ciblés pour vérifier :
    - intégration `ExecutionWithRisk`,
    - kill switch en fonction du drawdown & losing streak.

- Kill switch / safety guards :
  - Variables définies dans `config.finance.policies.safety_guards` et/ou `live_policies.py` :
    - `warning_drawdown_pct`, `critical_drawdown_pct`,
    - `max_consecutive_losers_warning`, `max_consecutive_losers_critical`,
    - `min_operational_capital_usd`.
  - Consommées par :
    - RiskEngine,
    - Finance alerts,
    - dashboard (/godmode/alerts/finance).

---

## 9. TEMP & dépôt public BOT_GODMODE_PUBLIC 

Dans le repo public `OnWorking_public` (BOT_GODMODE_PUBLIC) :

- `TEMP/INDEX.md`
  - Ce fichier : **index des fichiers & scripts clés M10** (copie publique de celui-ci).

- `TEMP/PLAN_TRAVAIL.md`
  - Plan de travail détaillé M10 :
    - pipeline complet,
    - roadmap (“100% LIVE sans TX”),
    - tâches par module.

- `TEMP/BLOC_CONTINUITE.md`
  - Journal des sessions :
    - ce qui a été fixé,
    - snapshots runtime/dashboards,
    - décisions pour les prochaines sessions.

- `TEMP/bot/...`, `TEMP/scripts/...`
  - Exports publics de fichiers Python (runtime, engine, finance, tests…).
  - **Référence lecture seule** pour recoder / réparer localement quand nécessaire.

- `TEMP/bot_structure.db`, `TEMP/bot_structure_snapshot.md`
  - Dump de la structure du projet (fichiers & modules).
  - Utilisé pour reconstruire les fichiers cassés.

---

## 10. Cheatsheet commandes (VM Linux)

- Activer le venv :
  ```bash
  cd ~/BOT_GODMODE/BOT_GODMODE
  source ./venv/bin/activate



Lancer la stack M10 complète (cible) :

python scripts/start_godmode_m10.py


Lancer juste le runtime memecoin (test LIVE_150) :

python scripts/test_memecoin_runtime_live150.py


Vérifier rapidement wallets & PnL :

cat data/godmode/wallets_runtime.json | jq '.updated_at, .wallets_count, .equity_total_usd, .wallets.sniper_sol, .wallets.copy_sol'


Inspecter les derniers trades :

tail -n 20 data/godmode/trades.jsonl


Accéder au dashboard :

Backend : http://127.0.0.1:8001/godmode/status

Frontend : http://127.0.0.1:8001/static/index.html

