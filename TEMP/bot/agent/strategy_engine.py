from typing import List

from bot.normalizer.patterns import NormalizedEvent


class StrategyEngine:
    def evaluate(self, events: List[NormalizedEvent]) -> List[str]:
        alerts: List[str] = []

        # On se concentre sur les derniers events
        for e in events[-50:]:
            kind = e.kind

            # Ancien comportement: fake_swap (mode mock / dev)
            if kind == "fake_swap":
                alerts.append(f"[{e.chain}] Interesting swap at block {e.block}")
                continue

            raw = e.raw or {}

            # ERC20 Transfer
            if kind == "erc20_transfer":
                evt = (raw.get("event") or {})
                frm = evt.get("from")
                to = evt.get("to")
                value = evt.get("value_raw")
                contract = raw.get("contract")
                alerts.append(
                    f"[{e.chain}] ERC20 Transfer on {contract} "
                    f"from {frm} to {to} value_raw={value} at block {e.block}"
                )

            # ERC20 Approval
            elif kind == "erc20_approval":
                evt = (raw.get("event") or {})
                owner = evt.get("owner")
                spender = evt.get("spender")
                value = evt.get("value_raw")
                contract = raw.get("contract")
                alerts.append(
                    f"[{e.chain}] ERC20 Approval on {contract} "
                    f"owner={owner} spender={spender} value_raw={value} block={e.block}"
                )

        return alerts
