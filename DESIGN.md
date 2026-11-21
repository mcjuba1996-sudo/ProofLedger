# ProofLedger design note (Live 0.6.2 / generator 0.4.1)

## Live dashboard and official entry points

The local HTTP service drives the bundled Reconciler in a worker process. Uploaded Python policies
run in a separate CPython WASI process with a read-only standard library. The agent receives
public reset and tool observations and returns JSON actions. The runner owns the seed, limits,
episode files and terminal report.

The browser is the operator's control panel. It polls actual ordered events and can replay them
more slowly than the calculation. Playback speed and pause do not affect the environment clock,
inspection step cost, reward or measured runtime. Reports are released after the run completes.
The closed mode hides reward/terminal diagnostics from the agent's interaction stream.
Live events do not use trace.build(), which includes evaluator-only fields.

The UI uses local assets and a loopback listener. For the official run the judge imports
`judge_adapter.py` in its own process, reads or generates the episode, and transports the
public exchanges to its restricted policy worker. Both entry points call the same scorer.

## What it is
A reproducible simulator of one fictional company and one reporting period. An agent reads records
through five narrow tools and submits findings with evidence. The environment scores the answer
with the shared matcher and reward. It does not train a model.

## How an episode is built (`generator.py`)
1. **Hidden consistent history.** Entities, invoices, payments and correct double-entry postings
   in KZT at the policy FX rate (rate date is the invoice's issue date in company time).
2. **Error injection with a recorded cause** (written to `provenance.json`):

| type | what changes | amount in truth | evidence set |
|---|---|---|---|
| duplicate_invoice | two bookings of the same issuer's source invoice, with identical terms, items, currency and amount | invoice total | both invoice docs, linked by issuer and `source_invoice_number` |
| payment_allocation | part or all of a receipt goes to 2900 instead of AR; "missing" also sets `invoice_refs: null` | KZT on 2900 | payment doc + 2900 row |
| fx_error | AR is booked at another day's rate or a mistyped rate | \|booked − correct\| KZT | invoice doc + AR/revenue row + correct fx key |
| period_error | invoice issued after period end (company time) but booked inside it | KZT booked | invoice doc + revenue/AR row |

   Mixed scenarios: `mix_fx_allocation` (a foreign invoice at a wrong rate whose payment is partly
   unallocated) and `mix_fx_period` (a next-period foreign invoice booked early at the booking-date rate).
3. **Legitimate distractors:** recurring identical amounts, split and partial payments,
   overpayment + refund pairs, invoices near the period boundary shown in a timezone that shifts the
   naive date, and mixed display timezones on every record.
4. **Solvability check.** `analysis.detect_all` rebuilds every finding from open records only; the
   episode is rejected unless it matches the truth exactly (no FP, no FN) and the ledger balances.
   An issue with no available evidence cannot be produced.

Two random streams: content (`seed`) and identifiers/order (`seed` + `relabel`). `relabel` is the
harmless transform for paired checks: new ids and storage order, identical content and rewards.

## Curriculum, attacks and injections (v0.3)
- **Levels L1–L5** (`levels.py`) are preset config families: one error and one timezone (L1), all types (L2),
  traps (L3), interacting errors (L4), large year-end books with new ids (L5). Measured: the reconciler passes
  all five; a timezone-naive reconciler passes L1–L2 and fails from L3. See `reports/failure_analysis.md`.
- **Reward-hacking zoo** (`attacks.py`): 11 attacks, from flag-everything and guessed ids to type spraying,
  evidence stuffing, persuasive explanations and obeying planted instructions. All score below the honest
  answer; replacing explanations with filler leaves the reward unchanged.
- **Injected text**: some invoice line items contain instructions aimed at an AI auditor. Truth never depends on
  text; an agent that obeys them loses reward.
- **Comparison protocol** (`compare.py`): paired returns on held-out seeds with a bootstrap 95% CI; claim an
  improvement only when the interval excludes zero.

## Data integrity (v0.4)
Every injected error is recorded in `provenance.json` as concrete changes (inserted invoice, updated
amount before/after, early posting, misallocated receipt). `integrity.restore_clean` undoes them and
`integrity.check_books` verifies the clean books: debits = credits, every posted invoice at the policy
rate and inside the period, every receipt fully allocated to a referenced invoice in its currency, no
duplicates. Then each issue must be traced to a change and the injected books must violate a rule.
The published episodes stay within the suggested sizes. Some larger L5 configurations can
slightly exceed the suggested 200-document size; this is not a hard protocol limit.

## Contract and isolation (`env.py`, `serve.py`)
The evaluator can run in a separate process (`serve.py`, JSON lines); `RemoteEnvironment` is the agent
side and holds only a pipe. Episodes can be loaded from files (`episode_path`), so external harmless
transforms (`transform.py`: shuffled rows, renamed ids) are checked on the same code path.
`reset(seed, config) -> (obs, info)`, `step(action) -> (obs, reward, terminated, truncated, info)`,
JSON-only. Pages ≤ 50 rows with stable offset cursors. Unknown id/action/table → bounded error code,
step consumed. 100 steps including submit; 60 s wall clock. Truncation scores an empty answer. A submit received after the deadline consumes one action slot and adds zero inspections to the cost.
Closed mode returns reward 0 and no diagnostics until the run ends (`final_report()` is evaluator side).
Truth never enters observations; a canary string in truth/provenance is tested to be absent from
all trajectories.

## Matcher and reward (`matcher.py`)
TP requires matching type, canonical affected IDs (stripped, unique, sorted), exact currency,
an amount within the episode's published tolerance, and at least one complete evidence set.
All cited evidence must exist in the open tables. Among maximum-cardinality matchings the
scorer selects the lexicographically smallest sorted list of (issue_id, original finding index).
The default tolerance for a fixture with no declared rule is zero. The published episodes
declare their tolerance explicitly in minor units. Reward is
clip(2·F1 − 1 − 0.002·inspection_steps, −1, 1). A malformed submission returns −1 and null metrics.

## Measured results (`python -m proofledger.verify`, 20 seeds × 8 configs)
oracle 1.000 everywhere; reconciler F1 = 1.00, return 0.976 (~13 steps); flag_all −1.000;
silence +1 on clean, −1 on dirty; random explorer ≈ silence. A timezone-naive variant of the reconciler
misreads the FX date and the reporting period near timezone boundaries. The per-level results and
the counts of incorrect and missed findings are in `reports/failure_analysis.md`.
`python -m proofledger` runs the test suite (including Live regressions) and 52 behavioural checks.

## Honest limitations
- The reconciler and the solvability check share `analysis.py`; tests in `tests/test_v02.py` recompute
  amounts and dates independently.
- Rewards say nothing about fitness for real audit work. All data is synthetic.
- `episode_id` is derived from a hash of the seed and config: closed seeds must be large random values.
- Public contract and scoring fixtures exercise the published checks. Organizers control their
  hidden seeds, transformed files, policy processes and final grading.
