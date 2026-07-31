"""Domain models: things tied to an identity or to a domain outcome.

The line between this package and `value_objects/`:

    models/          carries an identity, or is the outcome of an operation on one —
                     Balance (an account's), FinancialQuery (a plan for an account),
                     FinancialQueryResult (that account's figures).
    value_objects/   defined purely by its attributes, with invariants and no identity —
                     Money, DateRange, Category, RelativePeriod. Two of them with equal
                     fields are the same thing.
"""
