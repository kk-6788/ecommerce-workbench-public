# PM Decision OS

## Skill Name

`pm-decision-os`

## Version

`1.2`

## Purpose

PM Decision OS is a product-management decision skill for evaluating products, opportunities, features, business ideas, product requirements, MVPs, roadmaps, product-selection models, and early-stage projects.

It combines:

- Problem Discovery
- Evidence Assessment
- Jobs To Be Done
- Market Analysis
- Business Viability
- Assumption Mapping
- Product Strategy
- Prioritization
- Validation
- MVP Definition
- PRD Review
- Roadmapping
- Metrics
- Experimentation
- Post-launch Review
- Survival-oriented product management
- Audit-oriented review
- E-commerce product selection
- Keyword demand analysis
- E-commerce unit economics
- Competition structure analysis
- Supply and inventory risk
- Scoring-model governance

The purpose of this skill is not to maximize document completeness.

The purpose is to improve decision quality under uncertainty.

Core principle:

> Identify the most important uncertainty, validate it at the lowest reasonable cost, and increase investment only when evidence improves.

---

# 1. When To Use

Use this skill when the user asks to:

- Evaluate a product idea
- Analyze a new product opportunity
- Review a product plan
- Review a product-selection or scoring model
- Choose between multiple products
- Prioritize features
- Decide what to build first
- Define an MVP
- Write or review a PRD
- Build a product roadmap
- Analyze product-market fit
- Design a validation experiment
- Evaluate whether a project should continue
- Review an existing product
- Analyze a business/product opportunity
- Decide whether to Scale, Keep, Improve, or Kill
- Turn incomplete ideas into a structured product plan
- Evaluate an early-stage or low-budget business

This skill is especially useful when:

- Capital is limited
- Data is incomplete
- There are many candidate opportunities
- The cost of a wrong decision is significant
- The user needs to survive before optimizing
- The user needs evidence rather than opinions

---

# 2. Core Product Loop

Use the following default sequence:

Problem  
↓  
Evidence  
↓  
User / JTBD  
↓  
Market  
↓  
Business  
↓  
Assumptions  
↓  
Strategy  
↓  
Prioritization  
↓  
Validation  
↓  
MVP  
↓  
PRD  
↓  
Roadmap  
↓  
Execution  
↓  
Metrics  
↓  
Review  
↓  
Scale / Keep / Improve / Kill

This is a loop, not a one-time process.

After Review, return to Problem / Evidence with newly collected information.

---

# 3. Operating Modes

## DEFAULT_MODE

Use for normal product analysis.

Goal:

- Improve decision quality
- Identify evidence gaps
- Recommend next action
- Avoid premature commitment

## SURVIVAL_MODE

Activate when any of these are present:

- Limited capital
- Limited inventory budget
- Early-stage operations
- High uncertainty
- Need to survive before scaling
- Product-selection / merchandising / marketplace operation
- User explicitly says "先活下来", "铺货", "小资金", "低成本试错", or equivalent

In Survival Mode prioritize:

1. Capital preservation
2. Positive unit economics
3. Small reversible bets
4. Fast learning
5. Short feedback loops
6. Low inventory risk
7. Operational simplicity
8. Low failure cost
9. Fast cash recovery
10. Scalability only after evidence

Survival Mode sequence:

Opportunity  
↓  
Kill Filter  
↓  
Unit Economics  
↓  
Critical Risk  
↓  
Cheap Validation  
↓  
Small Launch  
↓  
Real Data  
↓  
Scale / Improve / Kill

Primary principle:

> Do not try to predict the perfect winner. Reduce the cost of being wrong.

Read: `references/survival-mode.md`


## ECOMMERCE_SELECTION_MODE

Activate when the task involves:

- 选品 / 铺货
- SKU 筛选
- 商品池
- 关键词池
- 电商平台候选产品
- 商品评分模型
- 商品上架测试
- 小批量备货
- 供应链与库存判断

Default flow:

Candidate Pool  
↓  
Data Quality Check  
↓  
Hard Kill Filters  
↓  
Keyword / Demand Analysis  
↓  
Unit Economics  
↓  
Competition Structure  
↓  
Supply / Inventory Risk  
↓  
Return Risk  
↓  
Priority Score  
↓  
Small-Bet Test  
↓  
Real Outcome Feedback  
↓  
Scale / Improve / Kill

Rules:

1. Hard constraints come before weighted scores.
2. Keyword volume must be deduplicated by intent.
3. Contribution margin matters more than gross sales.
4. Inventory downside must be explicit.
5. Low-confidence candidates earn tests, not large commitments.
6. `SCALE` requires real post-launch evidence.
7. Scoring models must be recalibrated against real outcomes.

Read:

- `references/ecommerce-selection.md`
- `references/keyword-demand.md`
- `references/unit-economics-ecommerce.md`
- `references/competition-risk.md`
- `references/supply-inventory-risk.md`
- `references/scoring-governance.md`

## AUDIT_MODE

Activate when the user provides an existing:

- PRD
- Product strategy
- Product-selection model
- Scoring model
- Product plan
- Roadmap
- Experiment design
- Decision memo
- Existing operating framework

In Audit Mode:

1. Do not rewrite first.
2. Diagnose decision logic first.
3. Separate facts, assumptions, unknowns, and risks.
4. Assign severity.
5. Recommend concrete fixes.

Severity levels:

- CRITICAL — likely to invalidate the decision or create major loss
- HIGH — material weakness requiring correction
- MEDIUM — important but not immediately fatal
- LOW — improvement opportunity

For each issue output:

- Problem
- Why it matters
- Evidence
- Recommended fix

Read: `templates/product-audit.md`

---

# 4. Routing Logic

Route the request to the smallest useful set of modules.

## Problem unclear
Read:
- `references/problem-discovery.md`
- `references/jtbd.md`

## User need / JTBD question
Read:
- `references/jtbd.md`

## Feature/product prioritization
Read:
- `references/prioritization.md`

## E-commerce product selection / listing / SKU pool
Activate:
- `ECOMMERCE_SELECTION_MODE`
If capital is constrained also activate:
- `SURVIVAL_MODE`
Read:
- `references/ecommerce-selection.md`
- `references/keyword-demand.md`
- `references/unit-economics-ecommerce.md`
- `references/competition-risk.md`
- `references/supply-inventory-risk.md`
Use:
- `templates/ecommerce-selection-report.md`
- `templates/small-bet-test.md`

## Product-selection or scoring-model review
Use:
- `AUDIT_MODE`
Read:
- `references/prioritization.md`
- `references/survival-mode.md`
- `references/scoring-governance.md`
- `templates/product-audit.md`
- `templates/scoring-model-audit.md`

## Validation / MVP / experiment design
Read:
- `references/validation.md`
Use:
- `templates/experiment-card.md`

## PRD writing or review
Read:
- `references/prd-review.md`
If reviewing existing PRD:
- activate `AUDIT_MODE`

## Metrics / success criteria
Read:
- `references/metrics.md`

## Low-budget / early-stage / survival
Activate:
- `SURVIVAL_MODE`
Read:
- `references/survival-mode.md`

## Final recommendation / decision memo
Use:
- `templates/decision-report.md`

Do not load every reference by default.

---

# 5. Decision Rule

Before giving a recommendation, separate information into:

## Facts

Information directly supported by reliable evidence.

## Strong Signals

Multiple consistent but not definitive indicators.

## Assumptions

Claims that may be plausible but remain unvalidated.

## Unknowns

Important information that is currently unavailable.

## Risks

Conditions that could materially damage the project.

## Tests

Uncertainties that can be resolved through validation.

Useful labels:

`[FACT]`  
`[STRONG SIGNAL]`  
`[ASSUMPTION]`  
`[UNKNOWN]`  
`[RISK]`  
`[TEST]`

Never silently convert assumptions into facts.

---

# 6. Evidence Ladder

Evaluate evidence strength.

## Level 0 — Opinion

Examples:

- I think
- We believe
- Competitors are doing it

Strength: Very weak.

## Level 1 — Market Signal

Examples:

- Search volume
- Reviews
- Forums
- Social discussion
- Competitor listings
- Keyword trends

Strength: Weak to moderate.

## Level 2 — Behavioral Evidence

Examples:

- Clicks
- Add-to-cart
- Searches
- Complaints
- Returns
- Usage patterns

Strength: Moderate.

## Level 3 — Transaction Evidence

Examples:

- Purchases
- Preorders
- Deposits
- Paid pilots

Strength: Strong.

## Level 4 — Repeated Value

Examples:

- Repeat purchase
- Retention
- Habitual usage
- Referral
- Organic growth

Strength: Very strong.

Preference order:

Behavior > stated preference  
Transaction > interest  
Repeated behavior > one-time transaction

---

# 7. Product Strategy Rule

A strategy must answer:

- Who are we serving?
- Which problem are we solving?
- Why is this problem worth solving?
- Why now?
- What is our advantage?
- Why would users choose us?
- What will we deliberately not do?

A strategy without trade-offs is not a strategy.

---

# 8. Business Viability Rule

Do not treat revenue as proof of quality.

At minimum consider:

Revenue

minus:

- Product cost
- Packaging
- Logistics
- Platform fees
- Payment fees
- Advertising
- Returns
- Refunds
- Support
- Storage
- Inventory loss
- Promotions
- Taxes where relevant

Prefer:

Contribution Margin  
over  
Revenue

When useful also examine:

- CAC
- LTV
- Payback period
- Inventory turnover
- Cash conversion cycle
- Break-even volume

Key question:

> If sales increase, does the business become healthier or lose money faster?

---

# 9. Assumption Mapping

At minimum consider:

- Problem Assumption
- Demand Assumption
- Product Assumption
- Willingness-To-Pay Assumption
- Price Assumption
- Channel Assumption
- Supply Assumption
- Margin Assumption
- Retention Assumption
- Scale Assumption

For each important assumption estimate:

- Importance
- Uncertainty
- Cost of being wrong

Prioritize assumptions with:

High importance  
+ High uncertainty  
+ High failure impact

Validate the assumption most likely to kill the project.

---

# 10. Prioritization Rule

Do not immediately score everything.

Use two stages.

## Stage A — Kill Filters

Potential Kill Criteria:

- Negative contribution margin
- Unacceptable legal or regulatory risk
- Supply instability
- Excessive return risk
- Capital requirement beyond budget
- No meaningful demand evidence
- Commodity competition with no advantage
- Very high acquisition cost
- Unacceptable payback period
- Operational complexity beyond current capability

Candidates that fail critical constraints should not receive high priority because of a composite score.

## Stage B — Ranking

Choose based on context:

Low data:
- ICE
- Value / Effort

Moderate data:
- RICE

Requirement classification:
- MoSCoW

Satisfaction:
- Kano

Cost of delay / resource allocation:
- WSJF

Never use precision beyond the quality of the inputs.

A score of 78 versus 76 is not automatically a meaningful difference.

---

# 11. Validation Rule

Before large investment, design the lowest-cost useful test.

Potential methods:

- Interviews
- Surveys
- Landing pages
- Ad tests
- Fake doors
- Preorders
- Deposits
- Samples
- Prototypes
- Concierge tests
- Wizard-of-Oz tests
- Manual service
- Marketplace listing
- Small-batch inventory
- A/B testing
- Price testing

Every experiment must define:

- Hypothesis
- Test
- Metric
- Success threshold
- Failure threshold
- Maximum cost
- Maximum duration
- Decision after test

Bad:
> Let's launch and see.

Better:
> Test whether at least 3% of qualified visitors click Buy at a target price before purchasing inventory.

---

# 12. MVP Rule

MVP means:

> Minimum product required to validate the core value hypothesis.

For every feature ask:

> If this feature is removed, can the core hypothesis still be tested?

If yes, consider removing it.

Separate:

- Must validate
- Useful later
- Nice to have

---

# 13. PRD Rule

PRD is not the starting point.

A PRD should summarize a sufficiently understood problem and proposed solution.

Recommended structure:

- Background
- Problem
- User
- Evidence
- Goals
- Non-Goals
- Solution
- User Stories
- Requirements
- Acceptance Criteria
- Metrics
- Risks
- Dependencies
- Open Questions

A polished PRD without evidence is still a well-formatted hypothesis.

Read: `references/prd-review.md`

---

# 14. Roadmap Rule

Prefer:

## Now
High confidence, actively executing.

## Next
Moderate confidence, likely but dependent on learning.

## Later
Lower confidence, directionally interesting but not committed.

Roadmaps should communicate uncertainty.

---

# 15. Metrics Rule

Define metrics before launch when possible.

Use multiple layers:

- North Star Metric
- Outcome Metrics
- Leading Indicators
- Quality Metrics
- Efficiency Metrics
- Guardrail Metrics

Never optimize one metric in isolation.

Read: `references/metrics.md`

---

# 16. Review Rule

At defined checkpoints classify the project into one of four states:

## SCALE
Evidence supports increased investment.

## KEEP
Performance is acceptable. Continue.

## IMPROVE
Core opportunity remains valid but execution needs adjustment.

## KILL
Critical assumptions failed. Stop further investment.

Killing a project is not automatically failure.

Continuing a disproven project because of sunk cost is a larger failure.

---

# 17. Product Scoring Model Audit

When reviewing a scoring model, audit five layers.

## Layer 1 — Variable Validity
Does each variable measure something meaningful?

## Layer 2 — Data Quality
Where does each number come from?

## Layer 3 — Weight Logic
Why does each variable receive this weight?

## Layer 4 — Interaction Effects
Are variables double-counting the same underlying factor?

## Layer 5 — Decision Validity
Does a higher score actually predict better outcomes?

Look for:

- False precision
- Arbitrary weights
- Duplicate signals
- Missing risk variables
- Weak proxies
- Non-comparable scales
- Unvalidated thresholds
- Survivorship bias
- Confirmation bias

When possible, compare predicted ranking with real outcomes.

---

# 18. E-commerce Selection Rules

When `ECOMMERCE_SELECTION_MODE` is active:

## Rule A — Separate Filters From Scores

Use:

Hard Kill Filter  
→ Comparative Ranking  
→ Real Outcome Feedback

Never collapse all three into one total score.

## Rule B — Demand Must Be De-duplicated

Keyword pools must be grouped by intent.

Do not sum overlapping synonyms as independent demand.

Read:
`references/keyword-demand.md`

## Rule C — Use Contribution Margin

Evaluate complete unit economics including:

- logistics
- platform fees
- advertising
- expected returns
- storage
- promotions

Read:
`references/unit-economics-ecommerce.md`

## Rule D — Inventory Risk Is a First-Class Variable

Always surface:

- MOQ
- test-order cash exposure
- lead time
- variant complexity
- replenishment risk
- dead-stock downside

Read:
`references/supply-inventory-risk.md`

## Rule E — Competition Is Structural

Do not reduce competition to seller count alone.

Check:

- concentration
- review moat
- price pressure
- ad pressure
- differentiation
- entry wedge

Read:
`references/competition-risk.md`

## Rule F — Scores Must Learn From Reality

After launch compare predicted rank with:

- sales
- contribution margin
- CAC / ROAS
- return rate
- complaint rate
- inventory turn

If the model repeatedly misranks products, revise:

- variables
- weights
- thresholds
- confidence handling

Read:
`references/scoring-governance.md`

---

# 19. Default Output

For substantial analysis, default to:

## Executive Conclusion

One of:

GO  
TEST  
HOLD  
REVISE  
KILL

Then explain why.

## Evidence

What do we actually know?

## Key Assumptions

What remains uncertain?

## Critical Risks

What could kill the project?

## Business Viability

Does the economics work?

## Priority

Should this be done now?

## Validation Plan

What is the cheapest useful next experiment?

## Success Criteria

What result justifies more investment?

## Kill Criteria

What result means stop?

## Next Action

Specify the next concrete action.

For formal output, use:
`templates/decision-report.md`

---

# 20. Recommendation Confidence

Every major recommendation may include:

- High confidence
- Medium confidence
- Low confidence

Confidence should depend on:

- Evidence strength
- Data quality
- Sample size
- Relevance
- Recency
- Number of unresolved assumptions

Do not express strong certainty from weak evidence.

---

# 21. Anti-Patterns

Avoid:

- Framework Theater
- Score Worship
- Feature-First Thinking
- Vanity Metrics
- Premature Scaling
- Analysis Paralysis
- Sunk Cost Bias
- HiPPO Bias
- Competitor Copying
- Survey Overconfidence
- False Precision

Frameworks support decisions.

Frameworks do not replace judgment.

---

# 22. Final Decision Check

Before finalizing a recommendation, ask:

1. What problem are we actually solving?
2. What evidence proves it exists?
3. Who experiences it?
4. What do they use today?
5. Why would they switch?
6. Is there a viable business?
7. What are we assuming?
8. Which assumption could kill the project?
9. What is the cheapest useful test?
10. What metric determines success?
11. What result means stop?
12. Is this the best use of limited resources right now?

If several cannot be answered:

Do not recommend large-scale commitment.

Recommend validation.

---

# 23. Core Philosophy

The product manager's job is not to always be correct.

The job is to create a system where:

Wrong ideas die cheaply.

Good ideas gain evidence.

Strong ideas gain capital.

Each experiment improves the next decision.

Operating loop:

**Discover → Judge → Validate → Execute → Measure → Learn → Decide Again**

Goal:

> Better decisions with every iteration.
