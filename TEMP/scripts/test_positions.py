from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys

# Ajout du repo au PYTHONPATH (comme start_bot)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.trading.positions import (  # type: ignore
    PositionConfig,
    PositionManager,
    PositionManagerConfig,
)


class DummyTrade:
    """Petit trade factice pour tester le PositionManager."""
    def __init__(self) -> None:
        self.id = "T_TEST_1"
        self.chain = "ethereum"
        self.symbol = "PEPE/ETH"
        self.side = "BUY"  # peut être "BUY"/"SELL" ou un enum avec .value
        self.price = Decimal("0.000001")   # prix d'entrée
        self.qty = Decimal("1000000000")   # quantité
        self.opened_at = datetime.utcnow()
        self.reason = "unit_test"
        self.meta = {}


def main() -> None:
    # 1) Config de position : TP/SL/trailing
    pos_cfg = PositionConfig()
    pos_cfg.take_profit.tp1_pct = Decimal("0.2")   # TP1 à +20%
    pos_cfg.take_profit.tp1_size_pct = Decimal("0.5")  # fermer 50% à TP1
    pos_cfg.take_profit.tp2_pct = Decimal("0.5")   # TP2 à +50%
    pos_cfg.take_profit.tp2_size_pct = Decimal("0.3")  # fermer 30% à TP2

    pos_cfg.stop.sl_pct = Decimal("0.1")  # SL à -10%
    pos_cfg.stop.trailing_activation_pct = Decimal("0.3")  # activer trailing à +30%
    pos_cfg.stop.trailing_pct = Decimal("0.15")  # trailing à 15% sous le plus haut

    mgr = PositionManager(PositionManagerConfig(default_position_config=pos_cfg))

    # 2) On ouvre une position à partir d'un DummyTrade
    trade = DummyTrade()
    pos = mgr.open_from_trade(trade)

    entry = pos.entry_price
    print("Entry price:", entry)
    print("TP1:", pos.tp1_price)
    print("TP2:", pos.tp2_price)
    print("SL:", pos.sl_price)
    print("---\n")

    # 3) On simule un chemin de prix :
    #    - TP1
    #    - activation du trailing
    #    - TP2
    #    - trailing stop
    prices = [
        entry,                         # 0) juste après entrée
        entry * Decimal("1.10"),       # 1) +10%
        entry * Decimal("1.22"),       # 2) > +20% => TP1
        entry * Decimal("1.35"),       # 3) > +30% => trailing activé
        entry * Decimal("1.55"),       # 4) > +50% => TP2
        entry * Decimal("1.80"),       # 5) nouveau plus haut => trailing monte
        entry * Decimal("1.40"),       # 6) retrace (mais peut rester au-dessus du trailing)
    ]

    last_price_idx = len(prices)

    print("Simulation des ticks de prix:\n")

    for i, p in enumerate(prices):
        events = mgr.on_price_tick(chain="ethereum", symbol="PEPE/ETH", price=p)
        if events:
            print(f"Tick {i} — price={p}: {len(events)} event(s)")
            for ev in events:
                print("   ", ev.event_type.value, "close_qty=", ev.close_qty, "price=", ev.price)
            mgr.apply_events(events)
            print("   Position remaining_qty=", pos.remaining_qty, "status=", pos.status.value)
        else:
            print(f"Tick {i} — price={p}: aucun event")

    # Dernier tick pour déclencher le trailing stop
    if pos.trailing_stop_price is not None and pos.is_long:
        trigger_price = pos.trailing_stop_price * Decimal("0.999")
    elif pos.trailing_stop_price is not None and pos.is_short:
        trigger_price = pos.trailing_stop_price * Decimal("1.001")
    else:
        trigger_price = entry * Decimal("0.5")  # gros dump fallback

    print("\nDernier tick pour tenter de déclencher le trailing stop:")
    events = mgr.on_price_tick(chain="ethereum", symbol="PEPE/ETH", price=trigger_price)
    if events:
        print(f"Tick {last_price_idx} — price={trigger_price}: {len(events)} event(s)")
        for ev in events:
            print("   ", ev.event_type.value, "close_qty=", ev.close_qty, "price=", ev.price)
        mgr.apply_events(events)
        print("   Position remaining_qty=", pos.remaining_qty, "status=", pos.status.value)
    else:
        print(f"Tick {last_price_idx} — price={trigger_price}: aucun event")

    print("\n=== Résumé final de la position ===")
    print("Position id:", pos.id)
    print("Trade id:", pos.trade_id)
    print("Status:", pos.status.value)
    print("Remaining qty:", pos.remaining_qty)
    print("TP1 filled:", pos.tp1_filled)
    print("TP2 filled:", pos.tp2_filled)
    print("Trailing active:", pos.trailing_active)
    print("Trailing stop price:", pos.trailing_stop_price)


if __name__ == "__main__":
    main()
