# ProjectTown v1 benchmark (formal)

This is a deterministic Runtime simulation, not an LLM or token benchmark.

Raw rows: 4320

| Configuration | Runs | Success | Progress | Recovery | Duplicates |
|---|---:|---:|---:|---:|---:|
| B0:free_chat | 108 | 0.000 | 0.876 | 0.000 | 11 |
| B0:full_replan | 108 | 0.000 | 0.876 | 0.000 | 11 |
| B0:no_checkpoint | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B0:no_idempotency | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B0:no_verifier | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B0:no_watchdog | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B0:none | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B0:single_vs_three_role | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B1:free_chat | 108 | 0.000 | 0.876 | 0.000 | 11 |
| B1:full_replan | 108 | 0.000 | 0.876 | 0.000 | 11 |
| B1:no_checkpoint | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B1:no_idempotency | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B1:no_verifier | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B1:no_watchdog | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B1:none | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B1:single_vs_three_role | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B2:free_chat | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B2:full_replan | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B2:no_checkpoint | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B2:no_idempotency | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B2:no_verifier | 108 | 0.528 | 0.960 | 0.000 | 11 |
| B2:no_watchdog | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B2:none | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B2:single_vs_three_role | 108 | 0.556 | 0.963 | 0.059 | 11 |
| B3:free_chat | 108 | 1.000 | 1.000 | 1.000 | 0 |
| B3:full_replan | 108 | 0.685 | 0.970 | 0.333 | 0 |
| B3:no_checkpoint | 108 | 0.639 | 0.967 | 0.235 | 0 |
| B3:no_idempotency | 108 | 0.898 | 0.995 | 0.784 | 11 |
| B3:no_verifier | 108 | 0.972 | 0.997 | 0.941 | 0 |
| B3:no_watchdog | 108 | 0.917 | 0.996 | 0.824 | 0 |
| B3:none | 108 | 1.000 | 1.000 | 1.000 | 0 |
| B3:single_vs_three_role | 108 | 1.000 | 1.000 | 1.000 | 0 |
| B4:free_chat | 108 | 1.000 | 1.000 | 1.000 | 0 |
| B4:full_replan | 108 | 0.685 | 0.970 | 0.333 | 0 |
| B4:no_checkpoint | 108 | 0.639 | 0.967 | 0.235 | 0 |
| B4:no_idempotency | 108 | 0.898 | 0.995 | 0.784 | 11 |
| B4:no_verifier | 108 | 0.972 | 0.997 | 0.941 | 0 |
| B4:no_watchdog | 108 | 0.917 | 0.996 | 0.824 | 0 |
| B4:none | 108 | 1.000 | 1.000 | 1.000 | 0 |
| B4:single_vs_three_role | 108 | 1.000 | 1.000 | 1.000 | 0 |

## Artifacts

- [Raw results (CSV)](results.csv)
- [Raw results (JSON)](results.json)
- [Success-rate chart](success.svg)
- [Reproducibility manifest](manifest.json)

## Failures and limitations

- No external model, model latency, or model token use was measured.
- The committed results exercise a deterministic feature/fault matrix.
- Use real model adapters only in a separately labelled evaluation.
