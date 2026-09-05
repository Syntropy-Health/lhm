# syntropy-lhm

Python SDK for the **Longevity Health Model (LHM)** — typed models, metric mapping,
natural-language intent binding, and a publish transport for health metrics.

```bash
pip install syntropy-lhm
```

- **Licence:** Apache-2.0
- **Status:** Alpha (`0.1.0`)
- **Requires:** Python 3.11+

## Where the source lives right now

**This repository is the package's published home and is currently a placeholder.**
The implementation lives in the SyntropyHealth applications monorepo and moves here as
part of a planned repository split.

That split has not happened yet. This is stated plainly rather than left for you to
discover: if you are looking for the source today, it is not in this repository.

## Package contents

| Module | Purpose |
|---|---|
| `syntropy.lhm.models` | Typed LHM models and metric mapping |
| `syntropy.lhm.metrics` | Metric definitions and claim-scope enforcement |
| `syntropy.lhm.nl` | Natural-language intent binding |
| `syntropy.lhm.publish` | Metric publish transport |

Error reference is shipped in the distribution under `docs/reference/errors.md`.

## Scope

This SDK is a data and modelling library. It performs no diagnosis, provides no medical
advice, and makes no health claims. Metric semantics are the caller's responsibility.

---

© Syntropy Health · Apache-2.0
