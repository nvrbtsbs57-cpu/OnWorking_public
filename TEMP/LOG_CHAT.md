JOURNAL – Session du 30/11/2025
1. Objectif de la session

Réparer et vérifier tout le pipeline runtime → fichiers locaux → API /godmode → dashboard, en particulier le tuyau wallets_runtime.json qui était introuvable et empêchait le dashboard runtime d’être fiable.

2. État final obtenu (photo de fin)

Fichiers locaux (soufflette)

wallets_runtime.json  -> wallets=10, equity=None
execution_runtime.json -> keys=['daily_drawdown_pct', 'hard_stop_active', 'kill_switch', 'risk_enabled', 'soft_stop_active']
trades.jsonl           -> 1 derniers trades: SOL/USDC | 4.16 | executed


API /godmode (port 8001)

GET /status          -> 200 OK
  mode=GODMODE, run_mode=paper, exec_mode=PAPER_ONCHAIN,
  equity_total_usd=150.0, wallets_count=10

GET /wallets/runtime -> 200 OK
  source=runtime_wallets_json, wallets=10, equity_total_usd=150.0

GET /trades/runtime  -> 200 OK
  trade_count=1, volume_usd=4.16


Dashboard UI

“TRADES RÉCENTS : 1 – Top coin : SOL (1 trades)” → raccord avec trades.jsonl.

Statut global cohérent avec /godmode/status.

Tableau Wallets encore vide côté front, alors que l’API renvoie bien 10 wallets → problème désormais côté UI seulement.

Conclusion :
✅ pipeline runtime/files/API OK
✅ dashboard lit bien runtime_wallets_json et trades_jsonl
❌ UI “Wallets” pas encore branchée correctement sur la réponse API.

3. Scripts / fichiers créés ou modifiés

scripts/build_wallets_runtime_from_config.py (NOUVEAU)

Rôle : helper simple pour générer data/godmode/wallets_runtime.json à partir de config.json.

Fonctionnement :

lit finance.wallets.initial_balances_usd et la liste wallets de la config;

crée un objet :

{
  "updated_at": "...",
  "wallets": {
    "wallet_id": {
      "balance_usd": ...,
      "pnl_today_usd": 0.0,
      "realized_pnl_today_usd": 0.0,
      "open_positions": 0
    }, ...
  }
}


écrit le tout dans data/godmode/wallets_runtime.json.

Commande utilisée (Python global, pas le venv) :

"C:\Users\ME\AppData\Local\Programs\Python\Python311\python.exe" scripts\build_wallets_runtime_from_config.py


scripts/debug_runtime_pipes.py (MIS À JOUR)

Réécrit pour :

ne plus crasher sur la structure JSON de /godmode/status (AttributeError: 'str' object has no attribute 'get' corrigé).

être défensif : un échec HTTP affiche un message propre au lieu de lever une exception.

afficher un résumé plus clair :

local files (wallets, execution, trades)

résumés /status, /wallets/runtime, /trades/runtime.

Supporte :

--once

--interval N (par défaut 10s)

--limit N (nb de trades)

Utilisation :

"C:\Users\ME\AppData\Local\Programs\Python\Python311\python.exe" scripts\debug_runtime_pipes.py --once


Dashboard standalone (scripts/start_bot.py / start_dashboard)

Confirme qu’il tourne bien sur 127.0.0.1:8001.

Quand le port est pris :

on identifie le PID avec netstat -ano | find "8001"

on tue le process avec taskkill /PID <PID> /F

Ensuite :

"C:\Users\ME\AppData\Local\Programs\Python\Python311\python.exe" scripts\start_bot.py


Environnement Python

Le venv\Scripts\python.exe est bloqué par Windows (popup violette + “Accès refusé”).

Décision : pour tout ce qui est dashboard + outils de debug, on utilise dorénavant le Python global 3.11 :

"C:\Users\ME\AppData\Local\Programs\Python\Python311\python.exe" ...

4. Décisions importantes

Pour M3 runtime / monitoring, on accepte pour l’instant la solution simple :

wallets_runtime.json est généré par un script helper à partir de config.json.

Cela permet de tester l’UI et de vérifier le pipeline end-to-end.

Le vrai RuntimeWalletManager (basé sur WalletFlowsEngine) sera branché plus tard ; le helper build_wallets_runtime_from_config.py servira de plan B / outil de reset rapide.

La soufflette debug_runtime_pipes.py est maintenant l’outil standard pour vérifier les trois couches :

fichiers locaux,

API /godmode,

cohérence dashboard.

BLOC DE CONTINUITÉ (ce qu’on fera au prochain round)

RuntimeWalletManager “vrai”

Rebrancher le vrai RuntimeWalletManager sur le runtime M1/M3 (au lieu du simple script de build) pour que :

les balances évoluent avec les PnL,

equity_total_usd soit calculée dynamiquement,

wallets_runtime.json soit mis à jour en continu sans script manuel.

UI Wallets

Corriger le front React pour que le tableau “WALLETS” consomme vraiment /godmode/wallets/runtime :

aujourd’hui : API renvoie 10 wallets, UI affiche “Aucun wallet dans le runtime”.

vérifier les clés utilisées (wallet_id, equity_usd, etc.) et le mapping.

Génération de nouveaux trades

Relancer un runtime memecoin minimal pour générer de nouveaux trades dans data/godmode/trades.jsonl.

Vérifier dans la soufflette que trade_count > 1, puis voir le dashboard se mettre à jour.

Nettoyage / doc rapide

Ajouter dans le README interne :

les commandes “officielles” pour :

lancer le dashboard,

tester les pipes,

regénérer wallets_runtime.json.

mention du problème venv/Windows et du choix Python global.

Quand tu reviens de ta pause, on prend ce bloc de continuité comme point de départ et on attaque la partie runtime réel (trades + PnL) + UI wallets. 🧠💤