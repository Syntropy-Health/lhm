# LHM Design 0002 — Packaging Strategy: in-repo now, PyPI split at L4

**Status:** DECIDED (principal-directed research, 2026-07-10).
**Decision (D13):** keep LHM **in-repo (path-source)** through L3, then
**extract to its own repo + publish to PyPI at L4**, at which point
syntropy-journal consumes `lhm==x.y` from the index like any dependency and
the entire in-repo packaging-hazard class is deleted. Do **not** submodule it.

---

## 1. The question

Should the LHM SDK be a separately installable package module — its own repo
and release cadence — so SDK changes stop riding syntropy-journal's
land/deploy trains, despite the tight coupling (the journal app is the
reference consumer and L3 builds its adapters)?

## 2. Research corpus: three patterns already live in this repo, each with a failure record

| Pattern | Living precedent | Observed failure record (this week) |
|---|---|---|
| **A. In-repo dir + path-source** (LHM today; also syntropy-diet, syntropy-sdk) | `lhm = { path = "LHM", editable = true }` | (1) PR #1446's conflict resolution **silently dropped the dep + source entry** → prod Unit gate red (`import lhm`), latent for a full land cycle. (2) The Dockerfile never `COPY`'d `LHM/` → **both prod+test image builds red** (`Distribution not found at file:///app/LHM`). (3) `uv.lock` conflicts on **every** cross-branch merge (the lock carries the path package). All three are now test-pinned — but each was a whole incident. |
| **B. Git submodule + path-source** (reflex-clerk-api, reflex-audio-capture, reflex-stripe) | `.gitmodules` → `libs/...` | The **worst** record: an unpopulated `libs/reflex-clerk-api` submodule **broke `uv lock` in bump-on-pr CI for every feature PR** (the #2454 regression — the lock step can't build a submodule that isn't checked out). Submodule-pointer bumps are a standing coordination tax (`apps/gtm/presentation` carries a "don't bump without coordinating" warning). CI/Docker contexts must remember `submodules: recursive` forever. |
| **C. PyPI-published** (reflex-stripe, half-way) | `reflex-stripe>=0.1.1` on PyPI **but pinned back to a local path** because the 0.1.2 fix isn't published yet | Publish-lag friction: a local fix needed NOW forces a temporary path-source override until the release ships. Manageable (it's the documented exit path in pyproject), but real. |

Plus the cadence evidence the principal is reacting to: **L2 sat in the land
queue behind a multi-stage prod promote** — SDK iteration is currently
chained to app release trains even when the change is inert to the app.

## 3. Options weighed

- **A. Status quo forever** — fastest co-development; but the three A-class
  hazards recur structurally (every merge is a chance to re-drop the dep;
  every Docker change is a chance to lose the COPY), the lock stays a
  conflict magnet, and `pip install lhm` (U1/U3, the OSS + commercial
  promise) is impossible without a public artifact anyway.
- **B. Own repo as a submodule** — decouples pushes, keeps one checkout;
  **rejected on direct evidence**: it inherits every A-class Docker/lock
  hazard AND adds the B-class ones (the reflex-clerk-api lock-break is the
  exact failure mode LHM would acquire, on the package the app can least
  afford to lose).
- **C. Own repo + PyPI now (mid-L3)** — maximum decoupling immediately; but
  L3 is the point of **maximum coupling** (adapters, store, dashboard
  binding, the publish route — app-side code written against SDK contracts
  that will still wiggle). Every contract adjustment becomes a
  paired-PR + release + version-bump dance across two repos, which is
  slower than the land-train tax it removes.
- **D. In-repo through L3 → repo split + PyPI at L4** ✅ — couples when
  coupling is highest, decouples exactly when the OSS pillar (L4:
  docs site, quickstart, GitHub story) **requires** a public repo anyway,
  and the commercial API-key path (U3) requires an installable artifact.

## 4. Decision — D13

1. **Through L3:** LHM stays at `LHM/` with the path-source dep. The
   incident pins earned this week are the guardrails: dep/source/lock/import
   integrity (`test_lhm_dep_integrity.py`), Dockerfile COPY + layer order
   (`test_dockerfile_lhm_copy.py`), and the R7 packaging gates.
2. **Cadence relief now, without splitting:** SDK-only changes are inert to
   the running app until app code imports new surface — they may land to
   test on the normal queue **without joining deploy/promote trains**, and
   the CTO may batch them. (The L2 hold was promote-freeze sequencing, not
   architecture.)
3. **At L4 (the OSS pillar):** extract `LHM/` to `Syntropy-Health/lhm`
   (history preserved via `git subtree split`), publish `lhm` 0.1.x to PyPI
   (the name is already reserved in our pyproject; R7's clean-env
   wheel-install gate has been rehearsing this since L1). syntropy-journal
   then depends on **`lhm==x.y` from the index**: the path-source line, the
   Dockerfile `COPY LHM/`, and the lock's path entry are all **deleted** —
   the whole A-class hazard family goes away rather than being pinned.
4. **Co-development after the split** uses the documented reflex-stripe
   pattern in reverse: a temporary `[tool.uv.sources]` local override while
   hacking both sides, removed at publish (that pyproject comment is the
   playbook).
5. **Version discipline after the split:** the app pins exact (`lhm==x.y`);
   the SDK releases semver with the contracts' additive-only rule
   (`lhm-viz/v1`, `lhm-publish/v1`) carrying compatibility; a
   contract-schema drift test app-side replaces in-repo lockstep.

## 5. What would change the call

- If L3 turns out to need < ~2 weeks of contract wiggle, split earlier —
  the gate is "contracts stable enough that paired PRs are rare," not a
  date.
- If another A-class incident lands **despite** the pins, that's evidence
  the pins are insufficient and the split should be pulled forward.
- The submodule option stays rejected regardless — no new evidence can make
  the bump-on-pr lock-break class attractive.
