# BOT_GODMODE – Vision & Architecture v1

> Spécification fonctionnelle & technique de haut niveau  
> Statut : brouillon validé pour démarrer la refacto

---

## 1. Objectif du bot

BOT_GODMODE est un bot de trading **100% autonome 24/24** orienté :

- **Farming de memecoins** sur plusieurs chaînes.
- **Copy trading type Cupsey** via un wallet dédié qui suit des wallets on-chain "élite".

Le bot fonctionne avec :

- **10 wallets logiques** aux rôles bien définis.
- Un **RiskEngine strict** (global + par wallet + par stratégie).
- Un **Finance Engine** (autofees, compounding, sécurisation, cash-out).

Objectif métier :

- Maximiser la croissance du capital sur le long terme.
- Garder un comportement "**quasi pas de pertes**" grâce à des garde-fous forts :
  - limites de risque,
  - checks de sécurité avant LIVE,
  - circuit breakers.

Le niveau visé est **pro / semi-institutionnel** sur :

- l’architecture,
- la robustesse,
- la sécurité,

tout en restant exploitable sur une infra perso (PC/VPS + RPC premium).

---

## 2. Modes de fonctionnement

### 2.1 Modes d’exécution

- `PAPER_ONCHAIN` (mode de travail actuel cible)
  - Lecture des **vraies données on-chain**.
  - Exécutions simulées (journalisées) : aucun swap réel, aucune TX signée.
  - Utilisé pour tester la logique complète 24/24 avant tout passage en LIVE.

- `LIVE`
  - Lecture des **vraies données on-chain**.
  - Exécution réelle : envoi de transactions, swaps, gestion réelle des fonds.
  - N’est activé qu’après :
    - plusieurs jours de PAPER_ONCHAIN stables,
    - validation du comportement via dashboard + logs,
    - activation des checks de sécurité (honeypot, owner, liquidité, etc.).

### 2.2 Modes de sécurité

- `SAFE`  
- `NORMAL`  
- `DEGEN`  

Le `safety_mode` influence :

- la taille max des positions,
- les stratégies autorisées,
- les seuils de risk,
- les tokens autorisés (via scoring / checks).

---

## 3. Architecture – Modules principaux

Organisation logique du code (cible) :

- `core/`
  - `runtime` : start_bot, gestion des modes, boucle principale, orchestrateur.
  - `risk_engine` : règles de risque globales + par wallet + par stratégie, circuit breakers.
  - `state` : reconstruction de l’état (positions, PnL, limites) à partir des logs.

- `wallets/`
  - `manager` : gestion des 10 wallets logiques (rôles, soldes, exposition).
  - `flows` : règles de flux autorisés entre wallets (graph de flux).

- `strategies/`
  - `memecoin_farming/agent` : scan, scoring, signaux.
  - `copy_trading/agent`    : suivi de wallets "élite" avec nos garde-fous.

- `execution/`
  - clients par chaîne (ETH d’abord, BSC/BASE/SOL ensuite).
  - `router` : choix de la route d’exécution (wallet + RPC + DEX).
  - `positions` : gestion TP/SL/trailing, statut des positions.
  - `trade_store` : journal complet des signaux / ordres / résultats.

- `finance/`
  - `fees` : gestion automatique des frais / gas (AutoFees).
  - `sweep` : transferts des profits vers les bons wallets.
  - `compounding` : politique de réinvestissement / sécurisation.

- `monitoring/`
  - `api` : endpoints HTTP internes (status, PnL, wallets, trades...).
  - `ui`  : mini dashboard web.
  - `alerts` : notifications (ex. Telegram).

- `recovery/` (ou dans `core/state`)
  - règles de redémarrage safe,
  - reconstruction du state après crash / reboot à partir des logs (TradeStore, FinanceStore).

---

## 4. Modèle de wallets (W0–W9)

Le modèle utilise **10 wallets logiques** avec des rôles métier clairs :

- **W0 – VAULT / TREASURY**  
  Coffre-fort principal (capital sécurisé).
  - Reçoit une partie des profits.
  - Ne renfloue les wallets de trade que dans des limites strictes (montant max/jour, etc.).

- **W1 – TRADE_MEME_MAIN**  
  Wallet principal de **farming memecoins** (strat standard).
  - Sert de base pour les strats memecoins "safe/normal".
  - Profits routés vers W5, puis redistribués (vault, compounding, fees, payout).

- **W2 – TRADE_MEME_DEGEN**  
  Wallet pour strats memecoins plus agressives.
  - Capital limité (% max du capital total).
  - Ne reçoit jamais de fonds directement de W0 (passe par W5).

- **W3 – COPY_TRADE_ELITE**  
  Wallet de **copy trading** (type Cupsey / top wallets).
  - Reçoit une allocation dédiée.
  - Les tailles sont recalibrées selon notre RiskEngine, pas de copy 1:1 naïve.

- **W4 – FEES / GAS_MAIN**  
  Réservoir de gas/frais.
  - Reçoit automatiquement un petit pourcentage des profits.
  - Sert à renflouer les wallets de trade qui manquent de gas.

- **W5 – PROFIT_BOX / SAVINGS_BUFFER**  
  Zone tampon où arrivent les profits des wallets de trade (W1/W2/W3/W8).
  - Point central du **Finance Engine** pour redistribuer vers :
    - W0 (vault),
    - W1/W3 (compounding),
    - W4 (fees),
    - W6 (stables),
    - W9 (payout).

- **W6 – STABLES / HEDGE**  
  Réserve en stable / hedge.
  - Permet de sécuriser une partie des gains.
  - Peut éventuellement renflouer certains wallets dans des conditions précises.

- **W7 – EMERGENCY / PANIC**  
  WALLET "panic mode".
  - Utilisé en cas de circuit breaker global.
  - Sweep d’urgence depuis les wallets de trade pour préserver le capital.

- **W8 – TEST / SANDBOX**  
  Wallet expérimental.
  - Pour tester nouvelles strats / nouvelles chaînes avec capital limité.
  - Les profits remontent vers W5 ; pertes acceptées dans une limite encadrée.

- **W9 – PAYOUT / OWNER_WALLET**  
  Wallet dédié aux **retraits / cash-out persos**.
  - Reçoit automatiquement un % des profits via W5.
  - Utilisé comme source unique de retraits vers tes wallets persos externes.

Les **flux autorisés** (graph de flux) seront définis dans un module dédié (WalletFlows + Finance Engine)
et dans une config déclarative (par exemple YAML).

---

## 5. RiskEngine

Le RiskEngine fournit une **double couche** de contrôle :

1. **Risque global**
   - Max drawdown global (jour / semaine / total).
   - Max exposition globale par chaîne / par type d’actif.
   - Conditions de déclenchement des circuit breakers :
     - trop de trades perdants d’affilée,
     - erreurs répétées d’exécution,
     - comportement anormal.

2. **Risque par wallet & par stratégie**
   - Pour chaque wallet (W0–W9) :
     - `max_notional_per_trade`,
     - `max_daily_loss`,
     - `max_open_positions`, etc.
   - Pour chaque stratégie (`memecoin_farming.safe`, `memecoin_farming.degen`, `copy_trading.default`, etc.) :
     - budget de risque dédié,
     - max trades par heure / jour,
     - chaînes autorisées.

Le RiskEngine expose une API du type :

```text
risk_check(
  wallet_id,
  strategy_id,
  chain_id,
  notional,
  stop_loss,
  take_profit,
  leverage=None
) -> (ACCEPT / ADJUST / REJECT)
