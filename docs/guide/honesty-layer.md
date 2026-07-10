# The honesty layer — structural, not prose

Every LHM metric is a **personal correlation/trend in one person's own
records** — never a causal, treatment, or clinical claim. Most software
expresses that kind of limit as a disclaimer paragraph. LHM's position is
that prose honesty doesn't survive contact with a codebase: disclaimers get
edited, templated away, or separated from the numbers they qualify, and
nothing breaks when they do. So the honesty layer is **structural**: the
limits live in types, gates, and refusals, and violating them fails loudly
at persist, render, *and* publish time. A dishonest metric isn't discouraged
— it is unrepresentable on every surface the SDK controls.

Concretely, that means:

- a metric **cannot exist without caveats** (`MetricSpec.caveats` requires
  at least one; the guard requires the triad below);
- claim-violating text **cannot persist, render, or cross a wire** (the
  guard runs at all three boundaries independently);
- weak evidence **cannot be pinned** (low-tier candidates are ephemeral by
  contract);
- a confidence tier **cannot be forged** (`manage` re-derives the tier from
  the fit itself and refuses a mismatch — candidates persist only on their
  own evidence);
- an Rx-derived metric **cannot reach a public surface**, and cannot claim
  the top tier at all in v0.1.

## The claim-scope guard

`lhm.metrics.claims.assert_claim_safe(label, caveats, confidence, rx_input,
public_surface=…)` gates every metric's user-facing text. Three checks, all
structural:

**1. The banned lexicon.** Clinical-efficacy vocabulary never appears in a
metric's text — personal n-of-1 trends use structure-function association
language only. The seven banned stems ship as `lhm.BANNED_LEXICON` (print it
to see them); matching is stem-based (`\b(word)\w*`) and runs after NFKC
normalization + casefolding + zero-width-character stripping, so
homoglyph-adjacent forms and zero-width insertions fold back to matchable
text before the scan. One deliberate exemption: the standard negated
FDA structure-function disclaimer ("… not intended to diagnose, …") is the
canonical *compliant* caveat, so recognized negated-disclaimer sentences are
excised before the scan. And one accepted edge: the stems over-match a few
benign words — for a claims guard, a rare false rejection beats a missed
efficacy claim.

**2. The correlation/uncertainty triad.** A metric must carry at least one
caveat, and the caveats must *explicitly* contain (a) an
association/correlation framing marker ("association", "observational",
"n-of-1", …) and (b) an uncertainty marker ("interval", "not causal", "may
not", …). Honesty a reader can see, checked by machine.

**3. The Rx tier.** An `rx_input` metric is stricter: never the `strong`
tier in v0.1, and never a `public_surface=True` target — user-private
surfaces only.

All three **fail closed**: violations raise `ClaimScopeError`, and callers
must not persist/render on failure — and must not "fix" the text
automatically (auto-rewriting a claim into compliance is exactly the kind of
silent honesty-erosion the gate exists to stop).

```python
from lhm import BANNED_LEXICON, ClaimScopeError, assert_claim_safe

# compliant text passes silently
assert_claim_safe(
    label="Magnesium glycinate and sleep quality — personal trend",
    caveats=(
        "Personal n-of-1 association from your own records — observational, not causal.",
        "Estimates carry an uncertainty interval and may not generalize.",
    ),
    confidence="mid",
    rx_input=False,
)
print("compliant text: passed")

# a banned-stem label refuses (built from the SDK's own constant, so this
# example never carries an efficacy claim of its own)
bad_label = f"Magnesium {BANNED_LEXICON[0]}s sleep quality"
try:
    assert_claim_safe(
        bad_label,
        ("Personal n-of-1 association; uncertainty interval applies.",),
        "mid",
        False,
    )
except ClaimScopeError as err:
    print(f"refused: {type(err).__name__}")

# association marker present, uncertainty marker missing → the triad refuses
try:
    assert_claim_safe(
        "Magnesium glycinate and sleep quality — personal trend",
        ("A personal n-of-1 association in your own records.",),
        "mid",
        False,
    )
except ClaimScopeError as err:
    print(f"refused: {err}")

# the Rx rules
rx_caveats = ("Personal n-of-1 association — observational; uncertainty interval applies.",)
for kwargs in ({"confidence": "strong"}, {"confidence": "mid", "public_surface": True}):
    try:
        assert_claim_safe(
            "Synthetic Peptide D and sleep — personal trend",
            rx_caveats,
            kwargs.get("confidence"),
            True,
            public_surface=kwargs.get("public_surface", False),
        )
    except ClaimScopeError as err:
        print(f"refused: {err}")
```

```text
compliant text: passed
refused: ClaimScopeError
refused: caveats must carry an uncertainty marker (e.g. an interval note, 'not causal') — R10 triad
refused: an Rx-input metric never surfaces the 'strong' tier in v0.1 (C1b)
refused: an Rx-input metric is user-private — never a public/marketed surface (C1b)
```

## Where the gate runs — every boundary, independently

| Boundary | Call site | What it protects |
|---|---|---|
| **Persist** | `manage("add", candidate)` | claim-violating text never becomes a stored `MetricSpec` |
| **Render** | `to_viz_payload(spec, …)` | a `VizPayload` can never be constructed from violating text, regardless of what upstream did |
| **Publish** | `HttpPublisher.publish` / `InProcessPublisher.publish` | violating text never reaches a wire or a callback — the gate raises before any transport |

The redundancy is the point: each boundary re-checks rather than trusting
the previous one, so the guarantee holds even for hand-built specs and
payloads that never went through the pipeline. The `VizPayload` carries
`caveats` and `rx_input` **with** the data, so a render surface can (and the
reference dashboard does) re-check the Rx rules independently of upstream.

## Tiers and the caveats-verbatim rule

`confidence ∈ {low, mid, strong}` is the honesty surface for evidence
strength. Two structural rules ride it: **low never persists** (candidates
stay ephemeral — see the [quickstart's refusal](quickstart.md#when-the-evidence-is-weak-the-sdk-says-no)),
and **`manage` re-derives the tier from the fit** so a hand-built candidate
can't forge a persistable tier onto a sub-threshold fit score.

Caveats **render verbatim**: they travel untransformed from `MetricSpec`
through `VizPayload` to the rendered card. A consuming app may add context
around them but must not paraphrase, truncate, or summarize them — the
triad's markers are load-bearing for the reader, not boilerplate.

v0.1 is honest about itself the same way: the estimators are deterministic
stubs, so every `FitBlock` says `method="stub"`, each of the six correction
hooks records `applied=False` with a note about what the v2 estimator will
do there, and the default caveats say the interval is provisional. The
rigor *surface* — tiers, intervals, named corrections — is contract-tested
now, so the v2 numerics slot in without any shape change.
