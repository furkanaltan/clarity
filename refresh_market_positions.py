"""Refresh all App-managed ETF and stock positions without the Telegram bot."""

from __future__ import annotations

import argparse
import json

from rove_market_data import refresh_all_market_positions


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Rov.E market positions")
    parser.add_argument("--db", required=True, help="Path to the productive SQLite database")
    args = parser.parse_args()

    result = refresh_all_market_positions(args.db)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))

    # One bad user ticker must not block valid portfolios. A complete provider
    # outage should still be visible as a failed systemd run.
    if result["total"] > 0 and result["failed"] == result["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
