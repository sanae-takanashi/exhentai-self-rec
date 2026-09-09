# Audit Instrumentation Safety Patch

Date: 2026-09-09

## Discussion And Decisions

The implementation plan was discussed with the Fable advisor through
authenticated Tailscale HTTP MCP. Private prompts, replies and corpus metrics
remain under ignored `data/`; no credentials or personal gallery records belong
in this document.

Adopted: exclude missing decisions from diagnostic denominators; reject missing
acceptance preconditions; separate delivered and visible events; retain unknown
legacy visibility; make visibility idempotent and require an existing delivery;
carry assignments within the daily budget, preserving original provenance.

Not adopted: relaxing sample/fold bounds, arbitrary short expiry, or claiming a
synthetic numerical pass validates an as-yet-unimplemented candidate protocol.
The current fallback union is not an independent random audit of a learned
threshold's incremental region. Activation is explicitly disabled until that
protocol is implemented and tested. Neutral feedback remains neither positive
nor negative for these diagnostic outcome counts.

## Implemented

- Strict boolean decision validation and explicit missing-context counts.
- Version/threshold cohort validation; no pooled loss metric across cohorts.
- Outcome cutoff respected when replaying a historical diagnostic window.
- All failed preconditions reported, plus a hard candidate-protocol guard.
- Separate nullable visibility protocol and first-visible timestamps, additive
  migrations only; old delivery flags are never reinterpreted as visible.
- Browser half-card, continuous one-second foreground dwell, with interruption
  reset, detached-card cleanup and bounded retry.
- Visibility API validates existing instrumented delivery identity; arbitrary
  audit IDs cannot be supplied to the visibility endpoint.
- Frozen daily slot records; pending items resume within the 35-day horizon,
  consume slots before new draws, and retain original probabilities.
- New sampling-frame names describe the current-plus-retained fallback union.

## Verification

```powershell
.\.venv-rocm\Scripts\python.exe -m unittest tests.test_audit tests.test_recommender tests.test_app tests.test_personalized tests.test_visual
node --test tests/test_visibility.cjs
node --check static/app.js
node --check static/visibility.js
git diff --check
```

Optional real-browser integration:

```powershell
npm install --prefix data/browser-validation playwright --no-audit --no-fund
node tests/browser_audit.cjs .venv-rocm/Scripts/python.exe
```

The browser check runs headless Edge on desktop and mobile viewports, creates a
synthetic database under ignored `data/browser-validation`, starts a loopback
fixture server without production workers, verifies offscreen deliveries are
not counted as visible and scrolling adds visible cards, saves screenshots,
then stops its own browser and server. No production data or remote gallery
fetch is used. Unit tests separately simulate hidden tabs and interrupted dwell.

The full pre-change native Windows baseline had three pre-existing H@H archive
assertion failures caused by `/` versus `\\` path expectations. They are outside
this patch; do not claim the full suite passes while they remain.

## Deployment And Limits

Back up the production SQLite database using its backup API before a supervised
restart. This change does not restart production, fetch galleries, change the
production visual encoder, or trigger downloads. Schema additions are nullable
columns and separate tables. Rollback must use a code version retaining the
fail-closed activation guard; do not roll back to the known-invalid acceptance
logic or erase the new visibility evidence.

The 35-day report is still diagnostic and not suitable as a reachable acceptance
schedule for infrequent use. Adaptive horizons, full frozen cohort identities,
stratified marginal-region audits, design-based uncertainty/nonresponse bounds,
retained-membership expiry policy and a new SigLIP2 GPU run are deferred.
The existing SigLIP2 offline scripts stay opt-in; neither they nor these
instrumentation changes establish model-release acceptance.
