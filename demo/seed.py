"""Reset Guardian to a clean demo state.

Usage:
    .venv/bin/python -m demo.seed
    GUARDIAN_DATA_DIR=/tmp/x .venv/bin/python -m demo.seed

Wipes the event audit trail and the wallet, then re-initialises both so the
demo starts from a tidy, curated state.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import yaml  # noqa: E402

from guardian.core import Guardian  # noqa: E402
from guardian.events import EventLog  # noqa: E402
from wallet.wallet import Wallet  # noqa: E402


def main() -> None:
    cfg = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
    data_dir = Path(__import__("os").environ.get("GUARDIAN_DATA_DIR", str(BASE_DIR / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)

    log_path = data_dir / "events.jsonl"
    wallet_path = data_dir / "wallet.json"
    if log_path.exists():
        log_path.unlink()
    if wallet_path.exists():
        wallet_path.unlink()

    log = EventLog(log_path)
    wallet = Wallet(cfg["policy"], wallet_path, starting_balance=cfg["wallet"]["starting_balance_usd"])
    guardian = Guardian(cfg, log, wallet)
    guardian.reset()

    print(f"Guardian demo reset. Data dir: {data_dir}")
    print(f"  workflow : {guardian.status()['status']}")
    print(f"  balance  : ${guardian.status()['wallet']['balance']:.2f}")
    print(f"  judge    : {guardian.llm.describe()}")


if __name__ == "__main__":
    main()
