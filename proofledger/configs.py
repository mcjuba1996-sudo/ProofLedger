"""Versioned open configurations. Closed evaluation uses other seeds and parameter combinations."""
CONFIG_VERSION = "2026-11-20.2"

_ALL = {"duplicate_invoice": 1, "payment_allocation": 1, "fx_error": 1, "period_error": 1,
        "mix_fx_allocation": 1, "mix_fx_period": 1}

TRAIN = [
    (1001, {}),
    (1002, {"duplicate_invoice": 2}),
    (1003, {"payment_allocation": 2}),
    (1004, {"fx_error": 2}),
    (1005, {"period_error": 2}),
    (1006, {"mix_fx_allocation": 1}),
    (1007, {"mix_fx_period": 1}),
    (1008, _ALL),
    (1009, {"duplicate_invoice": 1, "fx_error": 1, "refund_rate": 0.2}),
    (1010, {}),
]

DEV = [
    (2001, {"n_invoices": 75, "duplicate_invoice": 3, "fx_error": 3}),
    (2002, {}),
    (2003, {"payment_allocation": 3, "split_payment_rate": 0.4}),
    (2004, {"period_error": 3, "boundary_distractors": 6}),
    (2005, {"mix_fx_allocation": 2, "mix_fx_period": 2}),
    (2006, {"n_entities": 4, "recurring_entities": 4, "duplicate_invoice": 2}),
    (2007, {"n_invoices": 70, "n_entities": 16, **_ALL}),
    (2008, {}),
    (2009, {"fx_error": 4, "payment_allocation": 2, "period_error": 2}),
    (2010, {"duplicate_invoice": 2, "mix_fx_period": 1, "unpaid_rate": 0.3}),
]
