#!/usr/bin/env python3
"""Portfolio management CLI — create portfolios, add transactions, view P&L."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from analysis.skill_loader import load_lib_for_script
from portfolio.db import (
    VALID_SIDES,
    add_portfolio,
    add_transaction,
    compute_allocation,
    compute_lots,
    compute_performance,
    compute_pnl,
    compute_positions,
    delete_portfolio,
    edit_transaction,
    export_transactions,
    get_cached_prices,
    get_portfolio,
    get_portfolio_summary,
    init_db,
    list_portfolios,
    list_transactions,
    reconcile,
    refresh_prices,
    remove_transaction,
    rename_portfolio,
    replay_fifo,
)

_lib = load_lib_for_script(__file__)
default_db_path = _lib.default_db_path


def _parse_price_overrides(raw: list[str] | None) -> dict[str, float]:
    if not raw:
        return {}
    out = {}
    for item in raw:
        if "=" not in item:
            continue
        asset, price_str = item.split("=", 1)
        out[asset] = float(price_str)
    return out


# ───────────────────────────────────────────────────────────────── subcommands


def cmd_init(args):
    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    init_db(args.db)
    if not args.json:
        print(f"Database initialized at {args.db}")


def cmd_portfolio_create(args):
    pid = add_portfolio(args.db, args.name, args.base_ccy, args.notes)
    if not args.json:
        print(f"Created portfolio '{args.name}' (id={pid})")
    else:
        print(json.dumps({"id": pid, "name": args.name}))


def cmd_portfolio_list(args):
    pfs = list_portfolios(args.db)
    if args.json:
        print(json.dumps(pfs, indent=2))
        return
    if not pfs:
        print("No portfolios. Create one with 'portfolio create --name ...'")
        return
    print(f"{'ID':<4} {'Name':<24} {'Base':<6} {'Created'}")
    print("-" * 58)
    for p in pfs:
        print(f"{p['id']:<4} {p['name']:<24} {p['base_ccy']:<6} {p['created_at'][:19]}")


def cmd_portfolio_show(args):
    pf = get_portfolio(args.db, args.id_or_name)
    if not pf:
        print(f"No portfolio matching '{args.id_or_name}'", file=sys.stderr)
        return
    if args.json:
        print(json.dumps(pf, indent=2))
        return
    for k, v in pf.items():
        print(f"  {k}: {v}")


def cmd_portfolio_rename(args):
    if rename_portfolio(args.db, args.id, args.name):
        if not args.json:
            print(f"Renamed portfolio {args.id} to '{args.name}'")


def cmd_portfolio_delete(args):
    if not args.yes:
        pf = get_portfolio(args.db, args.id)
        if not pf:
            print(f"Portfolio {args.id} not found", file=sys.stderr)
            sys.exit(1)
        tx_count = len(list_transactions(args.db, portfolio_id=args.id))
        resp = input(f"Permanently delete portfolio '{pf['name']}' and its {tx_count} transactions? [y/N] ")
        if resp.lower() != "y":
            print("Cancelled.")
            return
    if delete_portfolio(args.db, args.id):
        if not args.json:
            print(f"Deleted portfolio {args.id} (all transactions removed)")


def cmd_add(args):
    pid = args.portfolio
    pf = get_portfolio(args.db, pid)
    if not pf:
        print(f"Portfolio '{pid}' not found", file=sys.stderr)
        sys.exit(1)
    if isinstance(pid, str):
        pid = pf["id"]

    asset = args.asset
    side = args.side.upper()
    qty = args.qty
    price = args.price
    fee = args.fee or 0
    tx_hash = args.tx
    ref_val = args.ref
    notes = args.notes
    if notes and notes.startswith("@"):
        with open(notes[1:]) as f:
            notes = f.read().strip()
    ts = args.date or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    txid = add_transaction(
        args.db,
        pid,
        ts,
        side,
        asset,
        qty=qty,
        price=price,
        fee=fee,
        tx_hash=tx_hash,
        ref=ref_val,
        notes=notes,
    )
    if args.json:
        print(json.dumps({"id": txid}))
    else:
        label = {"BUY": "Bought", "SELL": "Sold"}
        verb = label.get(side, side)
        price_str = f" @ {price}" if price else ""
        fee_str = f" | fee {fee}" if fee else ""
        print(f"[{txid}] {verb} {qty} {asset}{price_str}{fee_str}")


def cmd_list(args):
    rows = list_transactions(
        args.db,
        portfolio_id=args.portfolio,
        asset=args.asset,
        side=args.side,
        since=args.since,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No transactions found.")
        return
    for r in rows:
        ts = r["ts"][:19] if r["ts"] else "-"
        side = r["side"]
        asset = r["asset"]
        qty = r["qty"]
        price = f"@{r['price']}" if r["price"] else ""
        tx_str = f"tx:{r['tx_hash'][:12]}" if r["tx_hash"] else ""
        notes_str = f" | {r['notes']}" if r.get("notes") else ""
        print(f"  {ts}  {side:<12} {qty:>12} {asset:<24} {price:<12} {tx_str}{notes_str}")


def _get_prices(args):
    prices = _parse_price_overrides(args.price_override) if hasattr(args, "price_override") else {}
    if hasattr(args, "no_refresh") and args.no_refresh:
        cached = get_cached_prices(args.db)
        cached.update(prices)
        return cached
    auto = refresh_prices(args.db)
    auto.update(prices)
    return auto


def cmd_view(args):
    portfolio_id = args.portfolio
    prices = _get_prices(args)
    summary = get_portfolio_summary(args.db, portfolio_id, prices)

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print("=" * 72)
    for bp in summary["by_portfolio"]:
        pnl_sign = "+" if bp["total_pnl"] >= 0 else ""
        print(f"  {bp['name']} ({bp['base_ccy']})")
        print(f"    Invested:       {bp['invested']:,.2f} {bp['base_ccy']}")
        print(f"    Current value:  {bp['current_value']:,.2f} {bp['base_ccy']}")
        print(f"    Realized P&L:   {bp['realized_pnl']:,.2f} {bp['base_ccy']}")
        print(f"    Unrealized P&L: {bp['unrealized_pnl']:,.2f} {bp['base_ccy']}")
        print(f"    Total P&L:      {pnl_sign}{bp['total_pnl']:,.2f} {bp['base_ccy']}")
        print(f"    Fees:           {bp['fees']:,.2f} {bp['base_ccy']}")
        print(f"    Positions:      {bp['positions']}")
        print()


def cmd_positions(args):
    portfolio_id = args.portfolio
    prices = _get_prices(args)
    positions = compute_positions(args.db, portfolio_id, prices)

    if args.json:
        print(json.dumps(positions, indent=2))
        return
    if not positions:
        print("No open positions.")
        return

    pf_names = {p["id"]: p["name"] for p in list_portfolios(args.db)}
    print(
        f"{'Portfolio':<14} {'Asset':<24} {'Qty':>12} {'Avg Cost':>12} {'Price':>12} {'Value':>14} {'Unreal P&L':>12}"
    )
    print("-" * 104)
    for pos in positions:
        pf = pf_names.get(pos["portfolio_id"], "?")
        cp = f"{pos['current_price']:,.2f}" if pos["current_price"] else "-"
        cv = f"{pos['current_value']:,.2f}" if pos["current_value"] else "-"
        up = f"{pos['unrealized_pnl']:+,.2f}" if pos["unrealized_pnl"] is not None else "-"
        print(f"{pf:<14} {pos['asset']:<24} {pos['qty']:>12.6f} {pos['avg_cost']:>12.2f} {cp:>12} {cv:>14} {up:>12}")


def cmd_pnl(args):
    portfolio_id = args.portfolio
    prices = _get_prices(args)
    rows = compute_pnl(args.db, portfolio_id, prices)

    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No transactions found.")
        return

    print(
        f"{'Portfolio':<14} {'Asset':<24} {'Buys':>5} {'Sells':>5} {'Invested':>12} {'Proceeds':>12} {'Realized':>12} {'Unreal':>10} {'Total':>10} {'Fees':>8}"
    )
    print("-" * 130)
    for r in rows:
        realized = f"{r['realized_pnl']:+,.2f}" if r["realized_pnl"] else "0"
        unrealized = f"{r['unrealized_pnl']:+,.2f}" if r["unrealized_pnl"] is not None else "-"
        total = f"{r['total_pnl']:+,.2f}" if r["total_pnl"] is not None else "-"
        print(
            f"{r['portfolio_name']:<14} {r['asset']:<24} {r['buys']:>5} {r['sells']:>5} {r['total_invested']:>12,.2f} {r['total_proceeds']:>12,.2f} {realized:>12} {unrealized:>10} {total:>10} {r['total_fees']:>8,.2f}"
        )


def cmd_lots(args):
    portfolio_id = args.portfolio
    lots = compute_lots(args.db, portfolio_id, args.asset)
    if args.json:
        print(json.dumps(lots, indent=2))
        return
    if not lots:
        print("No open lots.")
        return
    print(f"{'Portfolio':>5} {'Asset':<24} {'Qty':>12} {'Entry Price':>12} {'Entry Date'}")
    print("-" * 72)
    for lot in lots:
        print(
            f"{lot['portfolio_id']:>5} {lot['asset']:<24} {lot['qty']:>12.8f} {lot['entry_price']:>12.2f} {lot['entry_ts'][:19]}"
        )


def cmd_edit(args):
    if not edit_transaction(args.db, args.id, args.field, args.value):
        print(f"Transaction {args.id} not found", file=sys.stderr)
        sys.exit(1)
    if not args.json:
        print(f"Transaction {args.id} updated: {args.field} = {args.value}")


def cmd_remove(args):
    tx = remove_transaction(args.db, args.id)
    if not tx:
        print(f"Transaction {args.id} not found", file=sys.stderr)
        sys.exit(1)
    if not args.json:
        print(f"Removed transaction {args.id}")


def cmd_prices_refresh(args):
    prices = refresh_prices(args.db)
    if args.json:
        print(json.dumps(prices, indent=2))
    else:
        if not prices:
            print("No provider-ticker assets found. Add trades with --asset=kraken:SYM first.")
            return
        for asset, price in sorted(prices.items()):
            print(f"  {asset:<30} {price:,.4f}")


def cmd_replay(args):
    portfolio_id = args.portfolio
    events = replay_fifo(args.db, portfolio_id)
    if args.json:
        print(json.dumps(events, indent=2))
        return
    if not events:
        print("No transactions to replay.")
        return
    for ev in events:
        ts = ev["ts"][:19]
        price_str = f"{ev['price']:,.2f}"
        if ev["side"] == "BUY":
            print(
                f"  #{ev['tx_id']:<4} {ts}  BUY   {ev['qty']:>10.8f}  {ev['asset']:<24}  @ {price_str:>12}   -> {ev['remain_qty']:.8f} remaining"
            )
        else:
            print(f"  #{ev['tx_id']:<4} {ts}  SELL  {ev['qty']:>10.8f}  {ev['asset']:<24}  @ {price_str:>12}")
            for lot in ev["consumed_lots"]:
                print(
                    f"         <- lot #{lot['tx_id']}: consumed {lot['qty_consumed']:.8f}  cost_basis {lot['cost_basis']:,.2f}  P&L {lot['pnl']:+,.2f}"
                )
            total_str = f"{ev['total_realized_pnl']:+,.2f}" if ev["total_realized_pnl"] else "0"
            print(f"         total realized: {total_str}")


def cmd_reconcile(args):
    portfolio_id = args.portfolio
    if not args.balance_file:
        print("--balance-file is required", file=sys.stderr)
        sys.exit(1)
    with open(args.balance_file) as f:
        balance = json.load(f)
    diffs = reconcile(args.db, portfolio_id, balance)
    if args.json:
        print(json.dumps(diffs, indent=2))
        return
    if not diffs:
        print("No positions to reconcile.")
        return
    icon = {"match": "=", "diff": "!=", "missing_computed": "+", "missing_external": "-"}
    print(f"  {'ST':<3} {'ASSET':<24} {'COMPUTED':>14} {'EXTERNAL':>14} {'DELTA':>14}  STATUS")
    print(f"  {'-' * 2}  {'-' * 24}  {'-' * 14}  {'-' * 14}  {'-' * 14}  {'-' * 15}")
    for d in diffs:
        delta_str = f"{d['delta']:+,.8f}" if d["delta"] else "0"
        print(
            f"  {icon.get(d['status'], '?'):<3} {d['asset']:<24} {d['computed_qty']:>14.8f} {d['external_qty']:>14.8f} {delta_str:>14}  {d['status']}"
        )


def cmd_allocation(args):
    prices = _get_prices(args)
    rows = compute_allocation(args.db, args.portfolio, prices)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No positions with current prices. Run 'prices refresh' or use --price-override.")
        return
    print(f"{'Asset':<30} {'Value':>12} {'Weight':>8} {'Qty':>12}")
    print("-" * 66)
    for r in rows:
        print(f"{r['asset']:<30} {r['value']:>12,.2f} {r['weight_pct']:>7.1f}% {r['qty']:>12.6f}")


def cmd_performance(args):
    prices = _get_prices(args)
    rows = compute_performance(args.db, args.portfolio, prices)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No closed trades yet.")
        return
    print(
        f"{'Asset':<30} {'Buys':>5} {'Sells':>5} {'Invested':>12} {'Proceeds':>12} {'Cost Sold':>12} {'Realized':>10} {'Fees':>8} {'PF':>6}"
    )
    print("-" * 108)
    for r in rows:
        pf_str = f"{r['profit_factor']:.2f}" if r["profit_factor"] else "-"
        print(
            f"{r['asset']:<30} {r['buys']:>5} {r['sells']:>5} {r['total_invested']:>12,.2f} {r['total_proceeds']:>12,.2f} {r['cost_of_sold']:>12,.2f} {r['realized_pnl']:>10,.2f} {r['total_fees']:>8,.2f} {pf_str:>6}"
        )


def cmd_export(args):
    rows = export_transactions(args.db, args.portfolio)
    if args.format == "csv":
        import csv
        import io

        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            [
                "id",
                "portfolio_id",
                "ts",
                "side",
                "asset",
                "qty",
                "price",
                "cost_quote",
                "fee",
                "tx_hash",
                "ref",
                "notes",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.get(k)
                    for k in [
                        "id",
                        "portfolio_id",
                        "ts",
                        "side",
                        "asset",
                        "qty",
                        "price",
                        "cost_quote",
                        "fee",
                        "tx_hash",
                        "ref",
                        "notes",
                    ]
                ]
            )
        content = out.getvalue()
    else:
        content = json.dumps(rows, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(content)
        if not args.json:
            print(f"Exported {len(rows)} transactions to {args.output}")
    else:
        print(content)


# ──────────────────────────────────────────────────────────────────── argument parser


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="Machine-readable JSON output")

    parser = argparse.ArgumentParser(description="Portfolio management — SQLite-backed, FIFO cost basis.")
    parser.add_argument("--db", default=None, help="Database path (default: $MARKET_SKILLS_PORTFOLIO_DB)")

    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="Initialize the database", parents=[shared])
    p.set_defaults(func=cmd_init)

    # portfolio
    pf = sub.add_parser("portfolio", help="Portfolio CRUD", parents=[shared])
    pf_sub = pf.add_subparsers(dest="sub")
    p = pf_sub.add_parser("create", help="Create a portfolio", parents=[shared])
    p.add_argument("--name", required=True)
    p.add_argument("--base-ccy", default="EUR")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_portfolio_create)

    p = pf_sub.add_parser("list", help="List portfolios", parents=[shared])
    p.set_defaults(func=cmd_portfolio_list)

    p = pf_sub.add_parser("show", help="Show portfolio details", parents=[shared])
    p.add_argument("id_or_name")
    p.set_defaults(func=cmd_portfolio_show)

    p = pf_sub.add_parser("rename", help="Rename a portfolio", parents=[shared])
    p.add_argument("id", type=int)
    p.add_argument("name")
    p.set_defaults(func=cmd_portfolio_rename)

    p = pf_sub.add_parser("delete", help="Permanently delete a portfolio and its transactions", parents=[shared])
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p.set_defaults(func=cmd_portfolio_delete)

    # add
    p = sub.add_parser("add", help="Add a transaction", parents=[shared])
    p.add_argument("--portfolio", required=True, help="Portfolio id or name")
    p.add_argument("--side", required=True, choices=[s.lower() for s in VALID_SIDES])
    p.add_argument("--asset", required=True, help="Asset as provider:ticker (e.g. kraken:BTCUSD) or bare symbol")
    p.add_argument("--qty", type=float, required=True)
    p.add_argument("--price", type=float)
    p.add_argument("--fee", type=float, default=0)
    p.add_argument("--tx", help="Transaction hash or exchange order ID")
    p.add_argument("--ref", help="Reference / order number")
    p.add_argument("--notes", help="Free text. Use @path to read from file.")
    p.add_argument("--date", help="ISO 8601 timestamp (default: now)")
    p.set_defaults(func=cmd_add)

    # list
    p = sub.add_parser("list", help="List transactions", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--asset")
    p.add_argument("--side", choices=[s.lower() for s in VALID_SIDES])
    p.add_argument("--since")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_list)

    # view
    p = sub.add_parser("view", help="Portfolio summary", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--price-override", action="append", help="e.g. kraken:BTCUSD=45000")
    p.add_argument("--no-refresh", action="store_true", help="Use cached prices only, skip auto-fetch")
    p.set_defaults(func=cmd_view)

    # positions
    p = sub.add_parser("positions", help="Current holdings", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--asset")
    p.add_argument("--price-override", action="append", help="e.g. kraken:BTCUSD=45000")
    p.add_argument("--no-refresh", action="store_true", help="Use cached prices only, skip auto-fetch")
    p.set_defaults(func=cmd_positions)

    # pnl
    p = sub.add_parser("pnl", help="P&L per asset", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--asset")
    p.add_argument("--price-override", action="append", help="e.g. kraken:BTCUSD=45000")
    p.add_argument("--no-refresh", action="store_true", help="Use cached prices only, skip auto-fetch")
    p.set_defaults(func=cmd_pnl)

    # lots
    p = sub.add_parser("lots", help="Open FIFO lots", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--asset")
    p.set_defaults(func=cmd_lots)

    # edit
    p = sub.add_parser("edit", help="Edit notes or ref on a transaction", parents=[shared])
    p.add_argument("id", type=int)
    p.add_argument("--field", required=True, choices=["notes", "ref"])
    p.add_argument("--value", required=True)
    p.set_defaults(func=cmd_edit)

    # remove
    p = sub.add_parser("remove", help="Remove a transaction", parents=[shared])
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_remove)

    # prices
    p = sub.add_parser("prices", help="Price cache management", parents=[shared])
    p_sub = p.add_subparsers(dest="sub")
    p = p_sub.add_parser("refresh", help="Fetch current prices for all held assets", parents=[shared])
    p.set_defaults(func=cmd_prices_refresh)

    # allocation
    p = sub.add_parser("allocation", help="Portfolio allocation by asset", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--price-override", action="append", help="e.g. kraken:BTCUSD=45000")
    p.add_argument("--no-refresh", action="store_true", help="Use cached prices only, skip auto-fetch")
    p.set_defaults(func=cmd_allocation)

    # performance
    p = sub.add_parser("performance", help="Trading performance stats", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--no-refresh", action="store_true", help="Use cached prices only, skip auto-fetch")
    p.set_defaults(func=cmd_performance)

    p = sub.add_parser("replay", help="FIFO audit trail — shows every lot open and close", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("reconcile", help="Compare computed positions against external balance file", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--balance-file", required=True, help='JSON file: {"asset": qty, ...}')
    p.set_defaults(func=cmd_reconcile)

    # export
    p = sub.add_parser("export", help="Export transactions to CSV or JSON", parents=[shared])
    p.add_argument("--portfolio", type=int)
    p.add_argument("--format", choices=["csv", "json"], default="json")
    p.add_argument("--output", help="Output file path (default: stdout)")
    p.set_defaults(func=cmd_export)

    return parser


# ─────────────────────────────────────────────────────────────────────── main


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.db is None:
        args.db = default_db_path()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    func = getattr(args, "func", None)
    if not func:
        # sub-command without a sub-sub parser
        parser.print_help()
        sys.exit(1)

    func(args)


if __name__ == "__main__":
    main()
