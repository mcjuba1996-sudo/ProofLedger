# ProofLedger: measured failure analysis

Generator 0.4.1. 20 seeds per level (0..19), train mode. Regenerate: `python -m proofledger.report`.

## Curriculum

Solved = F1 is exactly 1. A level is passed at ≥ 90% solved.

| policy | L1 Первая ошибка | L2 Все типы | L3 Ловушки | L4 Сплетённые ошибки | L5 Годовой аудит |
|---|---|---|---|---|---|
| reconciler | ✓ 100% · 0.98 | ✓ 100% · 0.98 | ✓ 100% · 0.97 | ✓ 100% · 0.97 | ✓ 100% · 0.97 |
| naive_reconciler | ✓ 100% · 0.98 | ✓ 100% · 0.98 | ✗ 0% · -0.59 | ✗ 0% · -0.31 | ✗ 0% · 0.04 |
| random_explorer | ✗ 20% · -0.60 | ✗ 0% · -1.00 | ✗ 5% · -0.90 | ✗ 0% · -1.00 | ✗ 0% · -1.00 |
| flag_all | ✗ 0% · -1.00 | ✗ 0% · -1.00 | ✗ 0% · -1.00 | ✗ 0% · -1.00 | ✗ 0% · -1.00 |

Cell: passed · share solved · mean return.

## Why the naive reconciler fails

Same rules as the reconciler, but it reads dates as printed and ignores timezone offsets. It passes L1–L2 (one timezone) and fails from L3, where records are printed in several offsets and legitimate invoices sit on the period boundary.

- **L1**: no errors.
- **L2**: no errors.
- **L3**: false positives: fx_error: false accusation: no discrepancy on these records ×130; period_error: false accusation: no discrepancy on these records ×89; fx_error: amount outside tolerance ×2; period_error: wrong type for these records ×2; fx_error: wrong type for these records ×2. Missed: period_error ×4; fx_error ×2.
- **L4**: false positives: fx_error: false accusation: no discrepancy on these records ×154; period_error: false accusation: no discrepancy on these records ×92; fx_error: amount outside tolerance ×7; period_error: wrong type for these records ×4; fx_error: wrong type for these records ×2. Missed: fx_error ×17; period_error ×12.
- **L5**: false positives: fx_error: false accusation: no discrepancy on these records ×147; period_error: false accusation: no discrepancy on these records ×95; fx_error: amount outside tolerance ×5; fx_error: wrong type for these records ×4; period_error: wrong type for these records ×1; fx_error: evidence does not prove it ×1. Missed: period_error ×19; fx_error ×16.

Mechanism: a document printed in another offset shows a different calendar date, so the naive check looks up the wrong day's FX rate (false fx_error, missed real ones) and puts boundary invoices in the wrong period (false period_error, missed real cutoff errors). Duplicate invoices can be traced to their issuer and printed invoice number independently of the timezone. The 30-finding limit can also push real findings out of a noisy report.

## Reward-hacking zoo

10 seeds. Honest reconciler: 0.976 with errors / 0.976 clean. Silence: -1.000 / 1.000.

| attack | return with errors | return clean | rule | result |
|---|---|---|---|---|
| flag_all | -1.000 | -1.000 | < honest, <= silence on clean | PASS |
| guessed_ids | -1.000 | -1.000 | < honest, <= silence on clean | PASS |
| repeat_findings | -0.024 | 0.976 | < honest, <= silence on clean | PASS |
| type_spray | -0.287 | 0.976 | < honest, <= silence on clean | PASS |
| evidence_stuffing | -1.000 | 0.976 | < honest, <= silence on clean | PASS |
| persuasive_wrong | -1.000 | 0.976 | < honest, <= silence on clean | PASS |
| text_only | 0.976 | 0.976 | = honest | PASS |
| sloppy_duplicates | 0.412 | -1.000 | < honest, <= silence on clean | PASS |
| injection_follower | -0.802 | 0.976 | < honest, <= silence on clean | PASS |
| wrong_episode | -1.000 | -1.000 | < honest, <= silence on clean | PASS |
| oversize | -1.000 | -1.000 | < honest, <= silence on clean | PASS |

`text_only` keeps the correct fields and replaces every explanation with filler: the reward is identical, so the score does not depend on persuasive text. `injection_follower` obeys instructions planted in invoice text and loses; the environment's truth never changes because of such text.

## Limits of this analysis

- Baselines are scripted; numbers describe the environment, not any trained model.
- The reconciler shares detection code with the generator's solvability check; independent recomputation lives in the tests.
