"""Authenticated owner dashboard, pause/budgets and encrypted ledger backups.

Run with the existing Modal owner profile; never expose this as an HTTP route.
Reports and backups stay under ignored .local/operations with private permissions.
"""

import argparse
from contextlib import closing
from decimal import Decimal, InvalidOperation
import html
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from cryptography.fernet import Fernet
import modal

ROOT = Path(".local/operations")


def money(value):
    try:
        amount = Decimal(value) * 10000
        if (
            not amount.is_finite()
            or amount != amount.to_integral_value()
            or not 0 <= amount <= 10000000000
        ):
            raise ValueError()
        return int(amount)
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError(
            "Use a non-negative USD amount with at most four decimal places"
        ) from None


def dollars(units):
    return f"${units / 10000:,.4f}"


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_bytes(value)


def validate_snapshot(blob):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "restore.sqlite"
        path.write_bytes(blob)
        with closing(sqlite3.connect(path)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
            if db.execute(
                "SELECT COUNT(*) FROM accounts a WHERE reserved<0 OR spent<0 OR available+reserved+spent!=COALESCE((SELECT SUM(granted-revoked) FROM purchases WHERE owner=a.owner),0)"
            ).fetchone()[0]:
                raise RuntimeError("Backup credit balance check failed")


def encrypted_backup(blob, target):
    key_path = ROOT / "backup-key.fernet"
    if not key_path.exists():
        private_write(key_path, Fernet.generate_key())
    cipher = Fernet(key_path.read_bytes())
    encrypted = cipher.encrypt(blob)
    validate_snapshot(cipher.decrypt(encrypted))
    private_write(target, encrypted)


def dashboard(report):
    def esc(value):
        return html.escape(str(value))

    def table(headers, rows):
        return (
            "<div class='scroll'><table><thead><tr>"
            + "".join("<th>" + esc(h) + "</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join("<td>" + esc(v) + "</td>" for v in row) + "</tr>"
                for row in rows
            )
            + "</tbody></table></div>"
        )

    c = report["control"]
    wallets = table(
        [
            "Funding class",
            "Accounts",
            "Funded face",
            "Revoked",
            "Consumed service",
            "Available",
            "Reserved",
            "Outstanding face",
            "Invariant",
        ],
        [
            [
                w["kind"],
                w["accounts"],
                dollars(w["funded_face_units"]),
                dollars(w["revoked_units"]),
                dollars(w["consumed_units"]),
                dollars(w["available_units"]),
                dollars(w["reserved_units"]),
                dollars(w["outstanding_face_units"]),
                "OK" if w["balance_invariant_ok"] else "FAILED",
            ]
            for w in report["wallets"]
        ],
    )
    attempts = table(
        ["Mode", "Outcome", "Attempts", "Observed seconds", "Admission envelopes"],
        [
            [
                a["mode"],
                a["outcome"],
                a["attempts"],
                round(a["observed_seconds"], 2),
                dollars(a["envelope_units"]),
            ]
            for a in report["attempts"]
        ],
    )
    events = table(
        ["UTC day", "Action", "Source", "Count"],
        [
            [
                e["day"],
                "credit-backed run (includes fixtures)"
                if e["kind"] == "paid_run_started"
                else e["kind"],
                e["source"],
                e["count"],
            ]
            for e in report["events_30d"]
        ],
    )
    actuals = table(
        [
            "Month",
            "Net receipts (after refunds/fees)",
            "Modal actual cost",
            "Other actual cost",
            "Cash difference before tax/liabilities",
        ],
        [
            [
                a["month"],
                dollars(a["net_receipts"]),
                dollars(a["modal_cost"]),
                dollars(a["other_cost"]),
                dollars(a["net_receipts"] - a["modal_cost"] - a["other_cost"]),
            ]
            for a in report["actuals"]
        ],
    )
    alerts = (
        "".join("<li>" + esc(a) + "</li>" for a in report["alerts"])
        or "<li>No current rule-based alerts.</li>"
    )
    billing = report.get("provider_billing")
    billing_html = "<p>Provider billing could not be retrieved; no zero-cost assumption was made.</p>"
    if billing:
        billing_html = f"<p>Actual current-cycle Modal summary: <strong>${Decimal(billing['metered_usd']):,.4f} metered</strong>; <strong>${Decimal(billing['billed_usd']):,.4f} billed after adjustments</strong>.</p><p class='note'>Workspace-wide snapshot; includes owner tests and other workloads. Current-cycle totals can update later. Not a per-customer margin measurement.</p>"
    return f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Pageweft — owner operations</title><style>body{{margin:0;background:#111714;color:#ecf1e9;font:16px/1.6 system-ui;padding:clamp(16px,4vw,52px)}}main{{max-width:1300px;margin:auto}}h1,h2{{line-height:1.2}}.card{{border:1px solid #445047;border-radius:12px;padding:20px;margin:20px 0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #445047;white-space:nowrap}}.note{{color:#bfcabd}}a{{color:#b5d3aa}}li{{margin:8px 0}}</style><main><p class='note'>Private, generated snapshot · {esc(report["day_utc"])} UTC · no public admin endpoint</p><h1>Pageweft operations</h1><div class='card'><strong>Processing: {"PAUSED" if c["paused"] else "accepting within controls"}</strong><p>Daily admissions {dollars(report["admitted_daily_units"])} / {dollars(c["daily_units"])}; monthly {dollars(report["admitted_monthly_units"])} / {dollars(c["monthly_units"])}.</p><p>{report["pending_sections"]} pending sections · {report["stale_sections"]} stale · {report["negative_accounts"]} negative-credit accounts.</p><p class='note'>These limits cover admission estimates, not the entire provider bill. Already dispatched work can finish. Check actual Modal usage in <a href='https://modal.com/apps/m-amir-gomaa/main/deployed/mineru-pdf-extractor'>the platform dashboard</a>.</p></div><h2>Action required</h2><ul>{alerts}</ul><h2>Credits and service consumption</h2><p>{report["commercial_purchases"]} verified live purchases · {report["repeat_buyers"]} repeat pack buyers. Trial jobs admitted: {report["trial_usage"]["day"]} today, {report["trial_usage"]["month"]} this UTC month; default global allowances are 3/day and 20/month.</p>{wallets}<p class='note'>Funding is not profit. Outstanding credits are undelivered obligations; face values, consumed service and fixture credit are separate. Refund debt can reduce realizable service revenue. Existing legacy funding is unverified until authoritative reconciliation.</p><h2>Compute evidence this UTC month</h2>{attempts}<p class='note'>Observed worker estimate: {dollars(report["observed_worker_estimate_units"])}; excludes startup, idle, HTTP, ledger, orchestration, storage and egress. Missing timings are not zero actual cost.</p><h2>Provider billing</h2>{billing_html}<h2>Recorded actual cash and costs</h2>{actuals}<p class='note'>Enter invoice/settlement totals, not advertised rates. Missing rows mean unreconciled. Cash difference excludes outstanding credit liability, owner pay and income tax.</p><h2>Activity in the last 30 days</h2>{events}<p class='note'>{esc(report["measurement_note"])} No document text, filename, raw IP, email or key is included in this report.</p></main></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("report")
    sub.add_parser("pause")
    sub.add_parser("resume")
    b = sub.add_parser("budgets")
    b.add_argument("--daily", type=money, required=True)
    b.add_argument("--monthly", type=money, required=True)
    sub.add_parser("backup")
    v = sub.add_parser("verify-backup")
    v.add_argument("file", type=Path)
    a = sub.add_parser("actuals")
    a.add_argument("--month", required=True)
    a.add_argument("--net-receipts", type=money, required=True)
    a.add_argument("--modal-cost", type=money, required=True)
    a.add_argument("--other-cost", type=money, required=True)
    args = parser.parse_args()
    if args.action == "verify-backup":
        validate_snapshot(
            Fernet((ROOT / "backup-key.fernet").read_bytes()).decrypt(
                args.file.read_bytes()
            )
        )
        print(
            "Encrypted backup decrypted and passed SQLite integrity and credit-balance checks."
        )
        return
    ledger = modal.Function.from_name("mineru-pdf-extractor", "book_store")
    if args.action in ("pause", "resume"):
        ledger.remote("control", {"paused": args.action == "pause"})
    elif args.action == "budgets":
        ledger.remote(
            "control", {"daily_units": args.daily, "monthly_units": args.monthly}
        )
    elif args.action == "actuals":
        ledger.remote(
            "actuals",
            {
                "month": args.month,
                "net_receipts": args.net_receipts,
                "modal_cost": args.modal_cost,
                "other_cost": args.other_cost,
            },
        )
    elif args.action == "backup":
        target = (
            ROOT
            / "backups"
            / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ".fernet")
        )
        encrypted_backup(ledger.remote("snapshot", {}), target)
        print("Encrypted ledger backup verified:", target)
        print(
            "Keep this backup and .local/operations/backup-key.fernet separately and securely off this laptop."
        )
        return
    report = ledger.remote("operations", {})
    try:
        summary = modal.Workspace.from_context().billing.summary()
        report["provider_billing"] = {
            "metered_usd": str(summary.metered_cost),
            "billed_usd": str(summary.billed_cost),
            "at": time.time(),
            "scope": "whole Modal workspace, current billing cycle",
        }
    except Exception:
        report["provider_billing"] = None
    private_write(ROOT / "latest.json", json.dumps(report, indent=2).encode())
    private_write(ROOT / "dashboard.html", dashboard(report).encode())
    print("Private dashboard:", (ROOT / "dashboard.html").resolve())
    print(
        "Alerts:",
        len(report["alerts"]),
        "| commercial accounts:",
        report["wallets"][0]["accounts"],
        "| actual profit: requires settlement/invoice reconciliation",
    )


if __name__ == "__main__":
    main()
