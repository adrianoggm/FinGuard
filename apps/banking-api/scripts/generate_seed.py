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
class SideAccount:
    """A second or third account of the same holder.

    `monthly_minor` is what moves into it from the current account each month — a standing order to
    the piggy bank, a monthly purchase of a fund. It is an *internal* movement in both directions
    and nothing here is spending: that distinction is the whole reason this dataset exists before
    the multi-account work rather than after it.

    `interest_rate` is the yearly rate a savings account pays, credited monthly. Zero for anything
    that does not pay one, which is every kind but SAVINGS.
    """

    suffix: str
    kind: str
    display_name: str
    monthly_minor: int
    interest_rate: float = 0.0


@dataclass(frozen=True)
class Holder:
    name: str
    reference: str
    salary_minor: int
    rent_minor: int
    opening_balance_minor: int
    # Everything beyond the current account. Empty for two of the three holders on purpose: one
    # account is still the ordinary case, and a dataset where everybody has three would let the
    # single-account path rot without anybody noticing.
    side_accounts: tuple[SideAccount, ...] = ()


HOLDERS = (
    Holder(
        "Laura Martin Ruiz",
        "ES-0001",
        245_000,
        92_000,
        180_000,
        side_accounts=(
            # 2 % yearly, credited monthly. A rate rather than a fixed amount so the figures grow
            # with the balance, which is what makes a chart of a piggy bank worth looking at.
            SideAccount("A", "SAVINGS", "Hucha", 30_000, interest_rate=0.02),
            # Cost basis, not value. The ledger knows what was paid and nothing else, which is why
            # ADR-009 refuses to call this a valuation until there is a price source with a date.
            SideAccount("F", "ASSET", "Fondo indexado", 15_000),
        ),
    ),
    Holder("Diego Ferrer Gomez", "ES-0002", 189_000, 78_000, 95_000),
    Holder("Nuria Salas Ortega", "ES-0003", 312_000, 115_000, 260_000),
)


@dataclass(frozen=True)
class Card:
    """A card as its holder sees it.

    Four digits and never a number: a PAN belongs in a vault, and nothing in this product needs
    one — a screen shows the last four so somebody recognises which card, and the payment goes
    through the account.
    """

    display_name: str
    brand: str
    kind: str
    last_four: str
    expires_month: int
    expires_year: int
    status: str
    spending_limit_minor: int | None


# Two for Laura, because a card screen with one card cannot show the charges split between them,
# and that split is most of what the screen is for. Nuria carries a blocked one on purpose: a status
# that is only ever ACTIVE is a status nobody renders and nobody tests.
CARDS = {
    "ES-0001": (
        Card("LAURA MARTIN RUIZ", "VISA", "DEBIT", "4417", 9, 2028, "ACTIVE", None),
        Card("LAURA MARTIN RUIZ", "MASTERCARD", "CREDIT", "8302", 3, 2029, "ACTIVE", 150_000),
    ),
    "ES-0002": (Card("DIEGO FERRER GOMEZ", "VISA", "DEBIT", "5521", 11, 2027, "ACTIVE", None),),
    "ES-0003": (
        Card("NURIA SALAS ORTEGA", "VISA", "DEBIT", "1096", 6, 2030, "ACTIVE", None),
        Card("NURIA SALAS ORTEGA", "MASTERCARD", "CREDIT", "7734", 1, 2027, "BLOCKED", 90_000),
    ),
}

# What the classifier disagrees with the bank about, and what a holder disagreed with both about.
# Keyed on the merchant, because that is how a real classifier works and because it makes the
# dataset explicable: every Steam purchase moves, and nothing else does.
#
# Both are deliberately small. The point is to make the layers *visible* — a demo where every
# movement has three opinions on it is a demo of a bug — and to leave plenty of movements whose
# category still comes straight from the bank.
RECLASSIFIED = {
    # The bank files a games shop under leisure. It is a subscription for most people who spend
    # there monthly, which is exactly the kind of correction a classifier earns its keep on.
    "Steam": "SUBSCRIPTIONS",
}
CLASSIFIER_VERSION = "rules-v1"

# The holder's own corrections, which beat the classifier. Sports gear bought for physiotherapy is
# the archetype: nothing about the merchant says so and only the person knows.
REFILED = {
    "Decathlon": "HEALTH",
}

# Free labels, on whichever movements happen to carry these merchants. Zero or many per movement,
# which is the whole reason they are not categories: two of these on one purchase must not make it
# count twice in any breakdown.
TAGGED = {
    "Fnac": ("regalo",),
    "La Tagliatella": ("trabajo", "invitado"),
    "Renfe": ("viaje",),
}


# Counterparty for every operation: the bank's clearing account. Without it there
# would be no double entry and the balance trigger would reject each transaction.
CLEARING = "ES-COMPENSACION"

# Who each holder can pay. Two Martas on purpose, in Laura's list: resolving "Marta" has to be
# a question rather than a choice, and a dataset where every name is unique would let that go
# untested until somebody's money went to the wrong person.
# Name, phone, and the account a transfer would go to. The account is optional and half of them
# do not have one, deliberately: a contact list is built for Bizum, where the phone is enough, so
# "I can Bizum this person and not transfer to them" is the ordinary case rather than an edge. It
# is also the only way to exercise `ContactHasNoAccountError` against real data.
#
# Every IBAN here passes its own check digits — they were generated by solving mod-97 rather than
# typed — so the seed cannot be the reason a transfer is refused.
CONTACTS = {
    "ES-0001": (
        ("Marta Ruiz Gomez", "+34600111222", "ES9121000418450200051332"),
        ("Marta Solis Vega", "+34600333444", None),
        ("Aitor Beltran Diaz", "+34611222333", "ES6000491500051234567892"),
        ("Carmen Nieto Lara", "+34622333444", None),
    ),
    "ES-0002": (
        ("Laura Martin Ruiz", "+34633444555", "ES9620853500093000000000"),
        ("Sergio Vidal Pons", "+34644555666", None),
    ),
    "ES-0003": (
        ("Diego Ferrer Gomez", "+34655666777", "ES3701822200150201504058"),
        ("Elena Prat Mas", "+34666777888", None),
    ),
}


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
    # The other side of the double entry. The clearing account when absent, which is every movement
    # that crosses the bank's boundary. Set for a transfer between two accounts of the same holder:
    # one transaction, two entries, both theirs — which is what makes it internal rather than two
    # unrelated movements that happen to cancel out.
    counterparty: uuid.UUID | None = None


def _side_account_month(
    rng: random.Random,
    holder: Holder,
    side: SideAccount,
    month: date,
    end: date,
    accounts: dict[str, uuid.UUID],
) -> list[Movement]:
    """One month of a second account: the standing order into it, and any interest it pays.

    The standing order is two entries of one transaction and the type says INTERNAL_TRANSFER, so
    neither leg counts as spending or income. Interest is a real credit from the bank and does
    count — the money came from outside.
    """
    current = accounts[holder.reference]
    other = accounts[f"{holder.reference}-{side.suffix}"]
    day = day_in_month(rng, month, 1, 4, end)
    moved: list[Movement] = [
        Movement(
            occurred_at=moment(rng, day),
            kind="INTERNAL_TRANSFER",
            description=f"Traspaso a {side.display_name}",
            merchant=side.display_name,
            category="INTERNAL",
            amount_minor=-side.monthly_minor,
            account=current,
            counterparty=other,
        )
    ]
    if side.interest_rate:
        # A twelfth of the yearly rate on what has gone in so far. Approximate on purpose: this is
        # a dataset for reading questions off, not an amortisation table, and rounding it to the
        # cent is what keeps the ledger balanced.
        elapsed = month_starts(end, MONTHS).index(month) + 1
        paid = round(side.monthly_minor * elapsed * side.interest_rate / 12)
        if paid:
            moved.append(
                Movement(
                    occurred_at=moment(rng, day_in_month(rng, month, 27, 28, end)),
                    kind="SALARY",
                    description="Intereses",
                    merchant="FinGuard",
                    category="INTEREST",
                    amount_minor=paid,
                    account=other,
                )
            )
    return moved


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
    # A stream of its own for everything about a second account, and the reason is the one written
    # above `card_rng`: every draw from `rng` shifts every draw after it, so allocating a piggy
    # bank from it changes every amount and date in the dataset — which is exactly what happened
    # the first time this was written, and the phase 2 expectations moved with it.
    side_rng = random.Random(seed ^ 0x51DE5)
    accounts: dict[str, uuid.UUID] = {CLEARING: identifier(rng)}
    movements: list[Movement] = []

    for holder in HOLDERS:
        account = identifier(rng)
        accounts[holder.reference] = account
        # Allocated here, before any movement, so the identifiers stay stable whatever is added to
        # the loop below: this generator is only useful because it is byte-for-byte reproducible.
        for side in holder.side_accounts:
            accounts[f"{holder.reference}-{side.suffix}"] = identifier(side_rng)
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
            for side in holder.side_accounts:
                movements.extend(_side_account_month(side_rng, holder, side, month, end, accounts))
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
    # A generator of its own for the cards, and this is the reason: every draw from `rng` shifts
    # every draw after it, so assigning cards from it would have changed every amount and date in
    # the dataset — and the 15 expected figures in evaluation/queries are computed against those. A
    # separate stream keeps this addition additive: the same movements, now with a card named.
    card_rng = random.Random(seed ^ 0xCA2D5)
    cards: dict[uuid.UUID, tuple[tuple[uuid.UUID, Card], ...]] = {
        accounts[reference]: tuple((identifier(card_rng), card) for card in held)
        for reference, held in CARDS.items()
    }
    lines: list[str] = []
    # Which transaction each movement became, so the classification and tag rows below can
    # point at them without a second pass over the same list.
    transaction_ids: dict[int, uuid.UUID] = {}

    lines.append("BEGIN;")
    lines.append("")
    lines.append("-- The ledger is immutable: it cannot be cleared with DELETE.")
    lines.append("TRUNCATE ledger_entries, ledger_transactions, balance_projections,")
    # payment_intents references contacts and accounts, and TRUNCATE needs every referencing
    # table named with them. A dataset that cannot be reloaded is a dataset nobody reloads.
    # `cards` is referenced by ledger_transactions, so TRUNCATE needs the two named together.
    # `movement_categories` references both accounts and ledger_transactions, and every table
    # added after this one has to be named here too — postgres refuses the whole statement
    # otherwise, so a migration that forgets it breaks reloading the dataset and nothing else.
    # Never CASCADE: it would silently empty whatever a future migration happens to hang off
    # the ledger, which is a list nobody enumerated.
    lines.append("         payment_intents, movement_categories, movement_classifications,")
    lines.append("         movement_tags, contacts, cards,")
    lines.append("         accounts, customers;")
    lines.append("")

    bank_customer = identifier(rng)
    customer_rows = [f"({quote(str(bank_customer))}, {quote('FinGuard (compensacion)')})"]
    account_rows = [
        f"({quote(str(accounts[CLEARING]))}, {quote(str(bank_customer))}, "
        f"{quote(CLEARING)}, {quote(CURRENCY)}, {quote('ACTIVE')}, {quote('CURRENT')}, NULL)"
    ]
    for holder in HOLDERS:
        customer = identifier(rng)
        customer_rows.append(f"({quote(str(customer))}, {quote(holder.name)})")
        account_rows.append(
            f"({quote(str(accounts[holder.reference]))}, {quote(str(customer))}, "
            f"{quote(holder.reference)}, {quote(CURRENCY)}, {quote('ACTIVE')}, "
            f"{quote('CURRENT')}, {quote('Cuenta corriente')})"
        )
        # Same customer, so this is a holder with several accounts rather than several holders —
        # which is the whole point of the dataset and the thing the RLS work will have to honour.
        for side in holder.side_accounts:
            reference = f"{holder.reference}-{side.suffix}"
            account_rows.append(
                f"({quote(str(accounts[reference]))}, {quote(str(customer))}, "
                f"{quote(reference)}, {quote(CURRENCY)}, {quote('ACTIVE')}, "
                f"{quote(side.kind)}, {quote(side.display_name)})"
            )

    lines.append("INSERT INTO customers (id, full_name) VALUES")
    lines.append(",\n".join(f"    {row}" for row in customer_rows) + ";")
    lines.append("")
    lines.append(
        "INSERT INTO accounts (id, customer_id, account_reference, currency, status, "
        "kind, display_name) VALUES"
    )
    lines.append(",\n".join(f"    {row}" for row in account_rows) + ";")
    lines.append("")

    contact_rows = [
        f"({quote(str(identifier(rng)))}, {quote(str(accounts[reference]))}, "
        f"{quote(name)}, {quote(phone)}, {quote(iban) if iban else 'NULL'})"
        for reference, people in CONTACTS.items()
        for name, phone, iban in people
    ]
    lines.append("INSERT INTO contacts (id, account_id, display_name, phone, iban) VALUES")
    lines.append(",\n".join(f"    {row}" for row in contact_rows) + ";")
    lines.append("")

    card_rows = [
        f"({quote(str(card_id))}, {quote(str(account))}, {quote(card.display_name)}, "
        f"{quote(card.brand)}, {quote(card.kind)}, {quote(card.last_four)}, "
        f"{card.expires_month}, {card.expires_year}, {quote(card.status)}, "
        f"{card.spending_limit_minor if card.spending_limit_minor is not None else 'NULL'})"
        for account, held in cards.items()
        for card_id, card in held
    ]
    lines.append(
        "INSERT INTO cards (id, account_id, display_name, brand, kind, last_four, "
        "expires_month, expires_year, status, spending_limit_minor) VALUES"
    )
    lines.append(",\n".join(f"    {row}" for row in card_rows) + ";")
    lines.append("")

    clearing = accounts[CLEARING]
    transaction_rows: list[str] = []
    to_book: list[str] = []
    entry_rows: list[str] = []

    for number, movement in enumerate(movements, start=1):
        transaction = identifier(rng)
        # Which card was charged. Only for a CARD_PAYMENT: the ledger has a CHECK saying a movement
        # that names a card is a card charge, and a salary is not one. A blocked card still gets
        # charges, because it was not blocked when they happened.
        held = cards.get(movement.account, ())
        card = (
            card_rng.choice([card_id for card_id, _ in held])
            if held and movement.kind == "CARD_PAYMENT"
            else None
        )
        metadata = json.dumps(
            {"merchant_name": movement.merchant, "category": movement.category},
            ensure_ascii=True,
            sort_keys=True,
        )
        # Created PENDING regardless of where it ends up: migration 0004 only accepts entries
        # while a transaction is pending, and the ones that should be booked are booked at the
        # end. The dataset used to insert them already BOOKED and add entries afterwards, which
        # the ledger now forbids — and which is why nothing noticed it allowed it.
        transaction_ids[number] = transaction
        if movement.status == "BOOKED":
            to_book.append(str(transaction))
        transaction_rows.append(
            f"({quote(str(transaction))}, {quote(movement.kind)}, {quote('PENDING')}, "
            f"{quote(f'seed-{number:06d}')}, {quote(movement.description)}, "
            f"{quote(metadata)}::jsonb, {quote(movement.occurred_at.isoformat())}::timestamptz, "
            f"{quote(str(card)) if card else 'NULL'})"
        )

        amount = abs(movement.amount_minor)
        incoming = movement.amount_minor > 0
        # Double entry: what the holder receives comes out of the clearing account,
        # and the other way round.
        other_side = movement.counterparty or clearing
        for account, entry_type in (
            (movement.account, "CREDIT" if incoming else "DEBIT"),
            (other_side, "DEBIT" if incoming else "CREDIT"),
        ):
            entry_rows.append(
                f"({quote(str(identifier(rng)))}, {quote(str(transaction))}, "
                f"{quote(str(account))}, {quote(entry_type)}, {amount}, {quote(CURRENCY)})"
            )

    lines.extend(
        _batches(
            "INSERT INTO ledger_transactions "
            "(id, transaction_type, status, reference, description, metadata, occurred_at, "
            "card_id) VALUES",
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

    classification_rows: list[str] = []
    override_rows: list[str] = []
    tag_rows: list[str] = []
    for number, movement in enumerate(movements, start=1):
        if movement.status != "BOOKED":
            continue
        transaction = transaction_ids[number]
        if movement.merchant in RECLASSIFIED:
            classification_rows.append(
                f"({quote(str(movement.account))}, {quote(str(transaction))}, "
                f"{quote(RECLASSIFIED[movement.merchant])}, {quote(CLASSIFIER_VERSION)})"
            )
        if movement.merchant in REFILED:
            override_rows.append(
                f"({quote(str(movement.account))}, {quote(str(transaction))}, "
                f"{quote(REFILED[movement.merchant])})"
            )
        for label in TAGGED.get(movement.merchant, ()):
            tag_rows.append(
                f"({quote(str(movement.account))}, {quote(str(transaction))}, {quote(label)})"
            )

    if classification_rows:
        lines.extend(
            _batches(
                "INSERT INTO movement_classifications "
                "(account_id, transaction_id, category, classifier_version) VALUES",
                classification_rows,
            )
        )
    if override_rows:
        lines.extend(
            _batches(
                "INSERT INTO movement_categories (account_id, transaction_id, category) VALUES",
                override_rows,
            )
        )
    if tag_rows:
        lines.extend(
            _batches(
                "INSERT INTO movement_tags (account_id, transaction_id, tag) VALUES",
                tag_rows,
            )
        )

    # Booked last, once every entry exists. This is the lifecycle migration 0004 enforces, so
    # building the dataset now exercises it 1,621 times rather than routing around it.
    lines.append("")
    lines.append("-- PENDING -> BOOKED, now that the entries are in place. The lifecycle trigger")
    lines.append("-- re-checks the balance per transaction, so a dataset that does not add up")
    lines.append("-- fails here rather than being loaded and believed.")
    for start in range(0, len(to_book), BATCH):
        chunk = ", ".join(quote(identifier) for identifier in to_book[start : start + BATCH])
        lines.append(f"UPDATE ledger_transactions SET status = 'BOOKED' WHERE id IN ({chunk});")

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
