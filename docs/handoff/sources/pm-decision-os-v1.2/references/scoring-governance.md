# Scoring Model Governance

## Purpose

Prevent scoring systems from becoming decorative mathematics.

A score is useful only if it improves real decisions.

---

# 1. Three-Layer Architecture

Use:

## Layer A — Hard Constraints
Pass / Fail.

## Layer B — Comparative Score
Rank surviving candidates.

## Layer C — Real Outcome Feedback
Calibrate the model after launch.

Do not merge all three into one weighted total.

---

# 2. Variable Rules

Every variable should have:

- definition
- data source
- direction
- scale
- normalization rule
- confidence
- update frequency
- rationale
- owner where relevant

If a variable cannot be defined consistently, do not include it.

---

# 3. Weight Rules

Weights should reflect:

- business importance
- empirical predictive value where available
- current operating stage

Do not justify a weight only by intuition.

If empirical validation is unavailable:

- keep weights simple
- use coarse bands
- perform sensitivity tests
- avoid decimals suggesting false certainty

---

# 4. Double Counting

Common duplication:

Demand:
- search volume
- keyword popularity
- trend score

Competition:
- review count
- seller count
- ranking difficulty

Profit:
- gross margin
- contribution margin
- ROAS

These may partially measure the same underlying factor.

Create a variable map and identify correlated or conceptually overlapping signals.

---

# 5. Confidence Penalty

Use confidence as a modifier or separate decision field.

Example conceptual rule:

High-confidence evidence:
No penalty.

Medium:
Moderate commitment.

Low:
Require test before commitment.

Avoid mathematically complex confidence formulas unless supported by real validation data.

---

# 6. Threshold Governance

Every threshold should have one of:

- business constraint
- historical evidence
- platform requirement
- test-derived calibration
- explicit provisional status

Mark unvalidated thresholds as:

`PROVISIONAL`

Do not present them as scientifically proven.

---

# 7. Backtesting

When outcome data exists:

Compare:

- model rank
- launch decision
- actual sales
- actual contribution margin
- CAC
- return rate
- inventory turn

Track:

- false positives
- false negatives
- ranking inversions

A useful model should improve over time.

---

# 8. Versioning

When changing variables, thresholds, or weights:

Record:

- version
- date
- change
- reason
- expected effect

Do not compare scores from materially different model versions without normalization.

---

# 9. Audit Output

Report:

- architecture quality
- variable validity
- data quality
- double counting
- weight logic
- threshold validity
- confidence handling
- backtest readiness
- governance maturity
- required fixes
