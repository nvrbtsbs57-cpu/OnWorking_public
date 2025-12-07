## 10. Pipeline M10 “de bout en bout” (chemins & dépendances)

### 10.1. Vue globale

Objectif M10 : PAPER_ONCHAIN qui utilise **exactement la même tuyauterie que le LIVE**, sauf que :

- le moteur d’exécution est **PaperTrader** (pas d’envoi on-chain),
- le `tx_guard` reste en mode **hard_disable_send_tx = true**,
- les wallets sont runtime/virtuels (capital_usd = 150$ profil LIVE_150).

La chaîne complète ressemble à ça :

1. **Script de démarrage / tests**
   - Scripts utilisés aujourd’hui :
     - `scripts/smoke_test_m10_pipeline.py`
     - `scripts/test_agent_events_pipeline_with_wallets.py`
     - `scripts/test_memecoin_runtime_live150.py`
     - `godmode_dashboard.py` (backend FastAPI du dashboard).
   - Plus tard : `scripts/start_godmode_m10.py` devra devenir **l’entrée principale** qui orchestre tout.

2. **Config & finance (source de vérité)**
   - Fichier : `config.json`
   - Sections importantes :
     - `mode`, `BOT_MODE`, `RUN_MODE`, `SAFETY_MODE`
     - `profile_id` / `finance.profile_id` (LIVE_150)
     - `finance.capital_usd`, `finance.wallets[...]` (répartition des 150$)
     - `finance.policies.*` et `finance.fees_policy`, `finance.risk_wallets`
     - `wallet_flows.rules` (fees_sweep, profits_sweep…)
     - `strategies.memecoin_farming` + `strategies.copy_trading`
     - `rpc.*` (env `RPC_SOLANA_HTTP`, `RPC_SOLANA_WS`, etc. pour QuickNode)
     - `indexer.*` (activable plus tard pour du vrai on-chain). 

3. **Wallets & flows**
   - Dossier : `bot/wallets/`
   - Fichiers clefs :
     - `factory.py` → construit un `WalletFlowsEngine` à partir de `config.finance` + `wallet_flows`.
     - `engine.py` + `flows.py` → logiques d’allocations, sweeps, compounding, buffers, vault, fees.
     - `runtime_manager.py` → `RuntimeWalletManager` :
       - wrap `WalletFlowsEngine`
       - expose :
         - `on_tick()` → `engine.run_periodic_tasks()` + snapshot JSON
         - `on_trade_closed(wallet_id, pnl_usd)` → applique PnL & fees
       - écrit `data/godmode/wallets_runtime.json` (format consommé par le dashboard). 

   - Dépend de :
     - `config.json` (finance + wallet_flows)
     - `bot/finance/live_policies.py` pour les caps / profils LIVE (dont LIVE_150).

4. **Trading & exécution PAPER**
   - Dossier : `bot/trading/`
   - Fichiers clefs :
     - `paper_trader.py` → moteur d’exécution PAPER :
       - accepte `bot.core.signals.TradeSignal`
       - gère prix (Decimal), qty, PnL simulé, fees simulées
       - écrit dans `TradeStore` (`data/godmode/trades.jsonl` via `store.py`).
     - `execution.py` → `ExecutionEngine` :
       - reçoit des `TradeSignal` venant de l’agent / des stratégies
       - appelle `inner_engine.execute_signal(signal, prices=...)` (PaperTrader en M10)
       - après chaque trade, appelle `RuntimeWalletManager.on_trade_closed(...)`.
     - `store.py` → `TradeStore` (JSONL + calculs PnL). 

   - Dépend de :
     - `bot/core/signals.py`
     - `RuntimeWalletManager` (`bot/wallets/runtime_manager.py`).

5. **Agent & stratégies (memecoin + copy_trading)**
   - Dossier : `bot/agent/`
     - `engine.py` → `AgentEngine` :
       - lit des events (copy feed, signaux memecoin, alerts risk…)
       - construit des `TradeSignal`
       - route vers `ExecutionEngine`.
     - `signals.py`, `modes.py`, `state.py`, `risk_engine.py`, `alerts_engine.py`
       → modes, état, risk, alertes. :contentReference[oaicite:4]{index=4}

   - Dossier : `bot/strategies/memecoin_farming/`
     - `agent.py` :
       - `MemecoinPairConfig`, `MemecoinCandidate`
       - `MemecoinStrategyEngine` (ENTRY/EXIT time-based)
       - provider stub `StubRandomMemecoinProvider`
       - `build_memecoin_strategy_from_config(raw_cfg, logger_)`
         - lit `config.strategies.memecoin_farming`
         - supporte `provider.kind = "stub_random"` et `"onchain_dry_run"` (stub aléatoire aujourd’hui). :contentReference[oaicite:5]{index=5}

   - Dossier : `bot/strategies/copy_trading/`
     - `agent.py` (copy feed stub + signaux pour `copy_sol`).

   - Utilisé par :
     - `scripts/test_agent_events_pipeline_with_wallets.py`
     - `scripts/test_memecoin_runtime_live150.py`
     - futur `start_godmode_m10.py`.

6. **Runtime memecoin LIVE_150 (PAPER_ONCHAIN)**
   - Script : `scripts/test_memecoin_runtime_live150.py`
   - Pipeline du script :
     1. charge `config.json`
     2. construit `RuntimeWalletManager.from_config(cfg)`
     3. construit `PaperTraderConfig.from_env()` + `PaperTrader`
     4. crée `ExecutionEngine(inner_engine=paper_trader, wallet_manager=runtime_wallet_manager)`
     5. crée `MemecoinStrategyEngine` via `build_memecoin_strategy_from_config(cfg)`
     6. boucle :
        - `runtime_wallet_manager.on_tick()`
        - `signals = memecoin_engine.next_signals()`
        - pour chaque signal → `exec_engine.execute_signal(signal)`
        - relit le snapshot `wallets_runtime.json` pour log rapide (equity_total, sniper_sol, copy_sol). 

   - Résultat actuel :
     - trades memecoin PAPER écrits dans `data/godmode/trades.jsonl`
     - equity_total = 150$, 10 wallets, sniper_sol/copy_sol = 30$ chacun
     - stack M10 “full tuyauterie LIVE” validée en PAPER.

7. **Dashboard GODMODE (runtime + stat jour)**
   - Backend : `godmode_dashboard.py` (FastAPI)
     - lit :
       - `config.json` → profil, finance, fees_policy, risk_wallets, safety_guards
       - `data/godmode/wallets_runtime.json` → equity, pnl jour, % par wallet
       - `data/godmode/trades.jsonl` → liste des trades + PnL
       - `data/godmode/execution_runtime.json` (si présent) → drawdown, kill switch, etc.
     - expose les endpoints utilisés par le frontend :
       - `/godmode/status`
       - `/godmode/wallets/runtime`
       - `/godmode/alerts/finance`
       - `/godmode/trades/runtime`. 

   - Frontend : `index.html` (dashboard runtime)
     - appelle les endpoints ci-dessus
     - affiche :
       - equity totale, PnL jour, mode LIVE/PAPER
       - tableau `Wallets runtime` (chain, rôle, balance, % equity, PnL jour)
       - tableau des trades (heure, pair, side, notional, raison)
       - alertes finance (buffers, caps fees, caps risk_wallets, safety_guards). 

---

## 11. Roadmap M10 → “100% LIVE sans TX”

1. **Stabiliser la stack PAPER actuelle**
   - Continuer à faire tourner `test_memecoin_runtime_live150.py` en parallèle du dashboard.
   - Vérifier régulièrement :
     - `tail -n 50 data/godmode/trades.jsonl`
     - `cat data/godmode/wallets_runtime.json | jq '.equity_total_usd, .wallets.sniper_sol, .wallets.copy_sol'`.
   - Corriger les petits manques dashboard :
     - chain/role affichés `UNKNOWN/generic`
     - footer “Wallet fees : aucune info (fees_wallet non trouvé)” → aligner avec `finance.fees_policy.fees_wallet_id`. 

2. **Figer les règles LIVE_150 dans le code**
   - `bot/finance/live_policies.py` :
     - buffer fees (min/target/max)
     - caps equity fees & risk_wallets
     - safety_guards (warning/critical drawdown, losing streak, min_operational_capital).
   - `bot/wallets/flows.py` :
     - appliquer clairement :
       - `fees_sweeps`
       - `profits_sweep`
       - `compounding`.
   - Ajouter quelques tests ciblés (scripts de type `test_wallet_flows_live150.py`). 

3. **Brancher les garde-fous risk/execution en PAPER**
   - Réviser :
     - `tx_guard.py`
     - `bot/agent/risk_engine.py`
     - `bot/trading/execution_with_risk.py`
   - Scripts de test :
     - `scripts/test_risk_execution_live150.py`
     - `scripts/test_kill_switch_live150.py`.
   - Objectif :
     - kill switch fonctionnel (drawdown / losing streak) mais **sans casser** le mode PAPER. 

4. **Préparer l’indexer on-chain (QuickNode)**
   - Vérifier les variables d’environnement RPC déjà posées (`RPC_SOLANA_HTTP`, etc.).
   - Dossiers :
     - `bot/bot_core/indexer/*`
     - `bot/bot_core/normalizer/*`
   - Étapes prévues :
     - valider l’indexer en mode “lecture seule” (scan de quelques slots / blocks, stockage dans `data/indexer/`),
     - définir un `MemecoinDataProvider` réel qui lit les tokens/index depuis ces données,
     - basculer `strategies.memecoin_farming.provider.kind` de `"onchain_dry_run"` vers `"onchain"` **une fois que l’indexer est stable**. 

5. **Pack M10 “tout-en-un”**
   - Finaliser `scripts/start_godmode_m10.py` pour :
     - charger `config.json`
     - démarrer runtime (agent + stratégies + execution PAPER + wallets)
     - démarrer API/dashboard (ou lancer `godmode_dashboard.py` en sous-processus).
   - Objectif : à terme, une séquence simple du genre :
     ```bash
     cd ~/BOT_GODMODE/BOT_GODMODE
     source venv/bin/activate
     python scripts/start_godmode_m10.py  # mode PAPER_ONCHAIN, profil LIVE_150
     ```
   - Le passage en LIVE réel consistera ensuite à :
     - remplacer `PaperTrader` par l’exécuteur on-chain,
     - mettre `tx_guard.hard_disable_send_tx = false`,
     - renseigner des wallets non fictifs.

