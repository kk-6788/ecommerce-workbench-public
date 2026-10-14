# E-commerce Product Selection

## Purpose

Use this module when evaluating products for:

- Marketplace stores
- General-merchandise / listing-heavy stores
- Low-budget e-commerce operations
- Product pools
- SKU screening
- Early-stage category exploration

The objective is not to predict a perfect winner.

The objective is to create a repeatable system where:

- weak products are killed early,
- uncertain products receive small tests,
- strong products earn more capital.

---

# 1. Selection Pipeline

Use this sequence:

Candidate Pool  
↓  
Data Quality Check  
↓  
Hard Kill Filters  
↓  
Demand Evidence  
↓  
Unit Economics  
↓  
Competition Structure  
↓  
Supply / Logistics Risk  
↓  
Return / Complaint Risk  
↓  
Validation Cost  
↓  
Priority Score  
↓  
Small Test  
↓  
Real Outcome Data  
↓  
Scale / Improve / Kill

Do not score before hard filters.

---

# 2. Candidate Unit

Define the object being scored.

Avoid mixing:

- broad category
- keyword
- product concept
- SKU
- listing
- supplier offer

A valid candidate should have a sufficiently stable definition:

- product type
- target customer
- target price range
- sourcing assumption
- market / platform
- primary demand intent

If two candidates represent materially different price points, functions, audiences, or supply structures, score them separately.

---

# 3. Hard Kill Filters

Typical kill filters:

## Economics

- Contribution margin below minimum acceptable threshold
- Shipping destroys margin
- Return cost creates unacceptable downside
- Promotion dependency makes economics unstable

## Capital

- MOQ exceeds available test budget
- Inventory locks excessive cash
- Long replenishment cycle
- High dead-stock risk

## Compliance

- Regulatory uncertainty
- Restricted category
- Certification requirements beyond capability
- IP / trademark / patent exposure

## Supply

- Single fragile supplier
- unstable lead time
- high defect risk
- inconsistent product specification

## Operations

- packaging complexity
- fragile / oversized logistics
- high support burden
- complex variant management
- difficult after-sales service

## Market

- no meaningful demand evidence
- severe price war
- dominant incumbents with no viable entry angle

A failed critical filter overrides the composite score.

---

# 4. Core Selection Dimensions

For surviving candidates assess:

## Demand

Questions:

- Is there search, browsing, transaction, or marketplace demand?
- Is demand stable, growing, seasonal, or temporary?
- Are there multiple demand signals?
- Does the demand correspond to the exact product intent?

## Margin

Estimate complete contribution margin.

Do not use only:

Selling Price - Purchase Cost.

## Competition

Analyze:

- incumbent strength
- listing concentration
- review barriers
- price compression
- differentiation opportunity
- ad competition
- copyability

## Supply

Analyze:

- supplier depth
- MOQ
- lead time
- consistency
- quality risk
- backup options

## Logistics

Analyze:

- size
- weight
- fragility
- dimensional shipping cost
- storage burden

## Return Risk

Analyze:

- sizing
- compatibility
- subjective preference
- quality sensitivity
- expectation mismatch
- damage probability

## Capital Efficiency

Analyze:

- cash required for test
- inventory turn
- cash recovery speed
- replenishment flexibility

## Validation Cost

Ask:

> How cheaply can we learn whether this product deserves more capital?

## Failure Cost

Ask:

> If wrong, how much cash, time, inventory, and operational attention are lost?

---

# 5. Evidence Strength

For each major dimension label inputs as:

`OBSERVED`
Real direct data.

`PROXY`
A reasonable indirect signal.

`ESTIMATED`
Derived from assumptions.

`UNKNOWN`
No reliable input.

Scores based mostly on estimated or unknown data must receive a confidence penalty.

---

# 6. Selection Decision

Use:

## KILL
Critical constraint fails.

## HOLD
Potential exists but key data is missing or risk is too high.

## TEST
Cheap validation justified.

## SMALL BET
Small inventory or limited listing test justified.

## SCALE
Real outcome data shows healthy economics and repeatability.

Never use `SCALE` based only on pre-launch scores.

---

# 7. Portfolio Logic

Do not treat all tests equally.

Separate candidates into:

### Core Tests
Highest evidence and best survival economics.

### Exploration Tests
Lower confidence but potentially useful learning.

### Opportunistic Tests
Cheap, reversible, temporary opportunities.

Recommended principle:

> Capital allocation should reflect confidence, not enthusiasm.

---

# 8. Real Outcome Feedback

After launch, feed real data back into the model:

- impressions
- CTR
- product-detail-view rate
- add-to-cart
- conversion
- revenue
- contribution margin
- CAC / ROAS
- refund rate
- return rate
- complaint rate
- defect rate
- stock turn
- reorder signal

Compare:

Predicted Rank  
vs  
Actual Outcome

If prediction and outcome repeatedly diverge:

Recalibrate variables, weights, thresholds, or the entire model.

A scoring system is only valuable if it improves future decisions.
