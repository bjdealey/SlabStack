# Engine parity

The GitHub Pages demo has no Python process behind it, so
`frontend/src/lib/demo/` is a hand-maintained TypeScript port of the backend
engines. That port is the demo's whole reason for existing — and the obvious
way for it to fail is *quietly*: a threshold changed on one side, a rounding
rule that drifts, a percentile that reaches the other way. The demo would keep
rendering confidently, and it would be showing numbers the real app does not
produce.

So the two implementations are run over the same inputs and compared field by
field:

```
make parity
```

`cases.json` holds five sets of hand-built scenarios.

**`cases`** exercise the decision engine's branches — a well-evidenced grade, a
card where only the top grade has ever sold, both risk tolerances, and a route
that loses money. Each is fed to `decision.evaluate_route` + `decision.decide`
on both sides, and every number, string, and per-grade row is compared.

**`listings`** exercise the suggested asking price behind the selling queue —
the one piece of arithmetic Phase 7 added that both sides implement alone. They
cover each liquidity band, both boundaries between them, the upper-quartile cap
firing, and the two cases where it must *not* fire: a quartile sitting below the
realistic price, and one exactly equal to it. Capping there would suggest asking
*less* than the card fetches. Two cases exist purely to catch a floor/round swap,
following the lesson below.

**`scoring`** exercise the Brier score behind the learning system: a perfect confident call, a
confident wrong one, a hedged wrong one, a full ladder, half grades, nothing to mark, and — the one
easiest to get quietly wrong — a grade the model ruled out entirely, which must score as a maximal
miss rather than being skipped.

**`corrections`** exercise the learned correction as pure arithmetic over the signed errors: under
and over the minimum sample, the offset clamp, calibration switched off, nothing to correct, a wide
scatter that widens the range, and a single result that has no spread to measure. Both the numbers
and the sentences are compared, because the sentence is what the user actually reads.

**`allocations`** exercise the penny-exact split behind submission costing,
which is the other place two hand-written implementations drift. Several cases
exist only to catch a floor/round swap: they use weights whose shares all sit
above a half-penny, so rounding overshoots the total and the remainder loop
cannot claw it back. Without them that swap passes silently — which is exactly
what happened when this section was first written, and why those cases are
there.

**Any difference is a bug in one of the two.** The comparison prints both
values and which side produced them; decide which is right before making them
agree. Floating-point noise is tolerated to 1e-6 — nothing else is.

Add a case whenever you touch the engine in a way the existing cases would not
have caught.
