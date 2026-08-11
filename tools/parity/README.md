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

`cases.json` holds hand-built scenarios chosen to exercise the branches that
actually differ if someone gets them wrong — a well-evidenced grade, a card
where only the top grade has ever sold, both risk tolerances, and a route that
loses money. Each case is fed to `decision.evaluate_route` + `decision.decide`
on both sides, and every number, string, and per-grade row is compared.

**Any difference is a bug in one of the two.** The comparison prints both
values and which side produced them; decide which is right before making them
agree. Floating-point noise is tolerated to 1e-6 — nothing else is.

Add a case whenever you touch the engine in a way the existing cases would not
have caught.
