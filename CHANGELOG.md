# Changelog

All notable changes to `syntropy-lhm` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). While the major version is 0, a
minor bump may change the public API.

A version on PyPI can never be changed or re-uploaded, so every release gets a
new number and its own entry here.

## [Unreleased]

## [0.1.0] — first public release

The first release of the LHM (Longevity Health Metrics) SDK: n-of-1 personal
metrics with typed inputs. It is application-independent: it has no web
framework, database or auth of its own. It reports correlation with
confidence and never causation.

### Added

- **Tool surface.** `syntropy.lhm.tools` has three plain async functions,
  `fetch`, `analyze` and `manage`, with typed inputs and outputs
  (`AlignedData`, `AlignedPair`, `CandidateSignal`, `ManageOutcome`). A host
  application wraps them as tools and owns persistence and auth through an
  `LhmAdapter` it supplies.
- **Metric specs and claim scope.** `MetricSpec` and `MetricIntent` describe a
  metric. `ClaimScopeError` and `BANNED_LEXICON` refuse causal or outcome
  wording at the SDK boundary, so a result can only be phrased as an
  association.
- **FHIR mapping.** Observation-shaped records are mapped through
  `fhir.resources` 8.3.0 (exact pin). Codes are plain `Coding` fields from a
  curated table, with no terminology service.
- **Natural-language intents.** An `Interpreter` turns a short request into a
  `MetricIntent`. It raises `AmbiguousIntentError` instead of guessing.
- **Publishing envelope.** `PublishEnvelope`, `PublishReceipt`,
  `InProcessPublisher`, and `HttpPublisher` behind the `publish` extra
  (`httpx`).
- **Optional extras.**
  - `units`: UCUM units via `pint` and `ucumvert`.
  - `lhm-scoring`: interpretable scoring via `statsmodels` and
    `scikit-learn`. Without it, scoring reports `SCORING_AVAILABLE = False`
    and records the `stub` method, which never passes readiness.
  - `publish`: HTTP transport.

### Notes

- Requires Python 3.11 or newer.
- Licensed under Apache-2.0. See `LICENSE` and `NOTICE`.
- Status: alpha (`Development Status :: 3 - Alpha`).
