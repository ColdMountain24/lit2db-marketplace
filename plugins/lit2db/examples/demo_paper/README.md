# Demo source snippets

Three minimal source snippets that stand in for parsed literature spans, chosen to exercise
the three routing outcomes of the verification spine. They are synthetic — no real paper is
redistributed here.

- **A** — a clean, single-condition Km value. Grounds numerically; the adversarial judge
  finds it supported. Expected route: **auto-accept → written**.
- **B** — a condition-multiplexed turnover number ("73.6 and 40.8 ... at 0.3% and 0.75%").
  The number *appears* in the quote (naive grounding passes) but is not bound to a single
  condition, so the judge flags it **AMBIGUOUS**. Expected route: **human-review → denied**.
  This is the project thesis in miniature: high surface grounding, lower factual precision.
- **C** — a value from a **retracted** source. Grounds fine, judge supports — but the hard
  write-gate denies on `source_status=retracted`. Expected route: **denied at the gate**.
