"""Generates the banking core's synthetic dataset as a versionable SQL file.

It is deterministic: with the same seed and the same end date it produces the very
same file, byte for byte. That is why the dataset lives in git instead of being
generated on each machine: the tests, the phase 2 benchmark and the demo all work on
exactly the same movements.

    python scripts/generate_seed.py                 # regenerates seeds/0001_demo.sql
    python scripts/generate_seed.py --months 6      # a shorter dataset

Regenerating without changing arguments must leave `git diff` empty. If it does not,
the generator has stopped being deterministic.

The data itself stays in Spanish: it represents a Spanish bank's movements, and the
assistant will read it out to a Spanish-speaking user.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

SEED = 20260730
END_DATE = date(2026, 7, 31)
MONTHS = 18
BATCH = 500

CURRENCY = "EUR"


@dataclass(frozen=True)
class Spending:
    category: str
    merchants: tuple[str, ...]
    minimum: int
    maximum: int
    times_per_month: tuple[int, int]


# Amounts are in minor units, like the ledger.
SPENDING = (
    Spending(
        "GROCERIES", ("Mercadona", "Carrefour", "Lidl", "Dia", "Alcampo"), 1_200, 9_500, (6, 13)
    ),
    Spending("TRANSPORT_FUEL", ("Repsol", "Cepsa", "BP", "Shell"), 3_500, 7_800, (2, 6)),
    Spending(
        "RESTAURANTS", ("Bar Manolo", "La Tagliatella", "Goiko", "Cafe Central"), 900, 6_200, (3, 9)
    ),
    Spending("TRANSPORT_PUBLIC", ("Renfe", "EMT Madrid", "Cabify", "Uber"), 150, 4_500, (0, 7)),
    Spending("HEALTH", ("Farmacia Gran Via", "Clinica Dental Sur"), 800, 5_500, (0, 3)),
    Spending("LEISURE", ("Cines Yelmo", "Fnac", "Decathlon", "Steam"), 700, 8_900, (0, 4)),
)

SUBSCRIPTIONS = (
    ("Netflix", "SUBSCRIPTIONS", 1_399),
    ("Spotify", "SUBSCRIPTIONS", 1_099),
    ("Gimnasio Altafit", "SUBSCRIPTIONS", 2_990),
)


@dataclass(frozen=True)
class Holder:
    name: str
    reference: str
    salary_minor: int
    rent_minor: int
    opening_balance_minor: int


HOLDERS = (
    Holder("Laura Martin Ruiz", "ES-0001", 245_000, 92_000, 180_000),
    Holder("Diego Ferrer Gomez", "ES-0002", 189_000, 78_000, 95_000),
    Holder("Nuria Salas Ortega", "ES-0003", 312_000, 115_000, 260_000),
)

# Counterparty for every operation: the bank's clearing account. Without it there
# would be no double entry and the balance trigger would reject each transaction.
CLEARING = "ES-COMPENSACION"


@dataclass(frozen=True)
class Movement:
    occurred_at: datetime
    kind: str
    description: str
    merchant: str
    category: str
    amount_minor: int  # positive = money into the holder's account
    account: uuid.UUID
    status: str = "BOOKED"


def identifier(rng: random.Random) -> uuid.UUID:
    """A reproducible UUID v4: it comes from the seeded generator, not os.urandom."""
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def moment(rng: random.Random, day: date) -> datetime:
    return datetime.combine(day, time(rng.randrange(7, 23), rng.randrange(60)), tzinfo=UTC)


def month_starts(end: date, months: int) -> list[date]:
    first = end.replace(day=1)
    result = []
    for _ in range(months):
        result.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    return sorted(result)


def day_in_month(rng: random.Random, month: date, low: int, high: int, end: date) -> date:
    following = (month + timedelta(days=31)).replace(day=1)
    last = (following - timedelta(days=1)).day
    day = month.replace(day=min(rng.randrange(low, high + 1), last))
    return min(day, end)


def generate(months: int, end: date, seed: int) -> tuple[list[Movement], dict[str, uuid.UUID]]:
    rng = random.Random(seed)
    accounts: dict[str, uuid.UUID] = {CLEARING: identifier(rng)}
    movements: list[Movement] = []

    for holder in HOLDERS:
        account = identifier(rng)
        accounts[holder.reference] = account
        opening = month_starts(end, months)[0]

        movements.append(
            Movement(
                occurred_at=moment(rng, opening),
                kind="ACCOUNT_OPENING",
                description="Saldo de apertura",
                merchant="FinGuard",
                category="OPENING",
                amount_minor=holder.opening_balance_minor,
                account=account,
            )
        )

        for month in month_starts(end, months):
            movements.append(
                Movement(
                    occurred_at=moment(rng, day_in_month(rng, month, 25, 28, end)),
                    kind="SALARY",
                    description="Nomina mensual",
                    merchant="Innovasur SL",
                    category="SALARY",
                    amount_minor=holder.salary_minor + rng.randrange(-2_000, 8_000),
                    account=account,
                )
            )
            movements.append(
                Movement(
                    occurred_at=moment(rng, day_in_month(rng, month, 1, 5, end)),
                    kind="DIRECT_DEBIT",
                    description="Alquiler",
                    merchant="Arrendamiento Centro",
                    category="HOUSING_RENT",
                    amount_minor=-holder.rent_minor,
                    account=account,
                )
            )
            for merchant, category, amount in SUBSCRIPTIONS:
                movements.append(
                    Movement(
                        occurred_at=moment(rng, day_in_month(rng, month, 2, 12, end)),
                        kind="CARD_PAYMENT",
                        description=f"Suscripcion {merchant}",
                        merchant=merchant,
                        category=category,
                        amount_minor=-amount,
                        account=account,
                    )
                )
            for spending in SPENDING:
                for _ in range(rng.randrange(*spending.times_per_month)):
                    # A single draw: the description and the merchant_name must name
                    # the same shop, or the movement contradicts itself.
                    merchant = rng.choice(spending.merchants)
                    movements.append(
                        Movement(
                            occurred_at=moment(rng, day_in_month(rng, month, 1, 28, end)),
                            kind="CARD_PAYMENT",
                            description=f"Compra en {merchant}",
                            merchant=merchant,
                            category=spending.category,
                            amount_minor=-rng.randrange(spending.minimum, spending.maximum),
                            account=account,
                        )
                    )
            for _ in range(rng.randrange(0, 5)):
                sent = rng.random() < 0.6
                amount = rng.randrange(500, 9_000)
                movements.append(
                    Movement(
                        occurred_at=moment(rng, day_in_month(rng, month, 1, 28, end)),
                        kind="BIZUM",
                        description="Bizum enviado" if sent else "Bizum recibido",
                        merchant=rng.choice(("Marta", "Sergio", "Elena", "Javi", "Rocio")),
                        category="TRANSFER_BIZUM",
                        amount_minor=-amount if sent else amount,
                        account=account,
                    )
                )

    # A handful of unbooked movements, so it can be checked that the balance ignores
    # what is not BOOKED yet.
    for holder in HOLDERS:
        movements.append(
            Movement(
                occurred_at=moment(rng, end),
                kind="CARD_PAYMENT",
                description="Compra pendiente de liquidar",
                merchant="Amazon",
                category="LEISURE",
                amount_minor=-rng.randrange(1_000, 12_000),
                account=accounts[holder.reference],
                status="PENDING",
            )
        )

    movements.sort(key=lambda m: (m.occurred_at, str(m.account)))
    return movements, accounts


def to_sql(movements: list[Movement], accounts: dict[str, uuid.UUID], seed: int) -> str:
    rng = random.Random(seed ^ 0xC0FFEE)
    lines: list[str] = []

    lines.append("BEGIN;")
    lines.append("")
    lines.append("-- The ledger is immutable: it cannot be cleared with DELETE.")
    lines.append("TRUNCATE ledger_entries, ledger_transactions, balance_projections,")
    lines.append("         accounts, customers;")
    lines.append("")

    bank_customer = identifier(rng)
    customer_rows = [f"({quote(str(bank_customer))}, {quote('FinGuard (compensacion)')})"]
    account_rows = [
        f"({quote(str(accounts[CLEARING]))}, {quote(str(bank_customer))}, "
        f"{quote(CLEARING)}, {quote(CURRENCY)}, {quote('ACTIVE')})"
    ]
    for holder in HOLDERS:
        customer = identifier(rng)
        customer_rows.append(f"({quote(str(customer))}, {quote(holder.name)})")
        account_rows.append(
            f"({quote(str(accounts[holder.reference]))}, {quote(str(customer))}, "
            f"{quote(holder.reference)}, {quote(CURRENCY)}, {quote('ACTIVE')})"
        )

    lines.append("INSERT INTO customers (id, full_name) VALUES")
    lines.append(",\n".join(f"    {row}" for row in customer_rows) + ";")
    lines.append("")
    lines.append(
        "INSERT INTO accounts (id, customer_id, account_reference, currency, status) VALUES"
    )
    lines.append(",\n".join(f"    {row}" for row in account_rows) + ";")
    lines.append("")

    clearing = accounts[CLEARING]
    transaction_rows: list[str] = []
    entry_rows: list[str] = []

    for number, movement in enumerate(movements, start=1):
        transaction = identifier(rng)
        metadata = json.dumps(
            {"merchant_name": movement.merchant, "category": movement.category},
            ensure_ascii=True,
            sort_keys=True,
        )
        transaction_rows.append(
            f"({quote(str(transaction))}, {quote(movement.kind)}, {quote(movement.status)}, "
            f"{quote(f'seed-{number:06d}')}, {quote(movement.description)}, "
            f"{quote(metadata)}::jsonb, {quote(movement.occurred_at.isoformat())}::timestamptz)"
        )

        amount = abs(movement.amount_minor)
        incoming = movement.amount_minor > 0
        # Double entry: what the holder receives comes out of the clearing account,
        # and the other way round.
        for account, entry_type in (
            (movement.account, "CREDIT" if incoming else "DEBIT"),
            (clearing, "DEBIT" if incoming else "CREDIT"),
        ):
            entry_rows.append(
                f"({quote(str(identifier(rng)))}, {quote(str(transaction))}, "
                f"{quote(str(account))}, {quote(entry_type)}, {amount}, {quote(CURRENCY)})"
            )

    lines.extend(
        _batches(
            "INSERT INTO ledger_transactions "
            "(id, transaction_type, status, reference, description, metadata, occurred_at) VALUES",
            transaction_rows,
        )
    )
    lines.extend(
        _batches(
            "INSERT INTO ledger_entries "
            "(id, transaction_id, account_id, entry_type, amount_minor, currency) VALUES",
            entry_rows,
        )
    )

    # The balance check runs here, when the transaction closes: the trigger is deferred.
    lines.append("COMMIT;")
    lines.append("")
    return "\n".join(lines)


def _batches(header: str, rows: list[str]) -> list[str]:
    lines: list[str] = []
    for start in range(0, len(rows), BATCH):
        lines.append(header)
        lines.append(",\n".join(f"    {row}" for row in rows[start : start + BATCH]) + ";")
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=MONTHS)
    parser.add_argument("--end-date", type=date.fromisoformat, default=END_DATE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=Path("seeds/0001_demo.sql"))
    arguments = parser.parse_args()

    movements, accounts = generate(arguments.months, arguments.end_date, arguments.seed)
    body = to_sql(movements, accounts, arguments.seed)
    digest = hashlib.sha256(body.encode()).hexdigest()

    booked = sum(1 for m in movements if m.status == "BOOKED")
    header = "\n".join(
        (
            "-- Synthetic dataset for the FinGuard banking core. GENERATED, do not edit by hand.",
            "--   scripts/generate_seed.py",
            "--",
            f"-- seed           {arguments.seed}",
            f"-- months         {arguments.months}, up to {arguments.end_date.isoformat()}",
            f"-- holders        {len(HOLDERS)} + clearing account",
            f"-- transactions   {len(movements)} ({booked} BOOKED,"
            f" {len(movements) - booked} PENDING)",
            f"-- entries        {len(movements) * 2}",
            f"-- sha256         {digest}",
            "--",
            "-- CAREFUL: it starts with a TRUNCATE. This is a development dataset.",
            "",
        )
    )

    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(header + body, encoding="utf-8", newline="\n")
    print(f"{arguments.out}: {len(movements)} transactions, {len(movements) * 2} entries")


if __name__ == "__main__":
    main()
