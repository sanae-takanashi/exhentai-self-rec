# Ranking observations

Open `/static/ranking-insights.html` for a read-only dashboard backed by
`GET /api/insights/ranking?days=30&surface=all`. Days accepts integers 1–365;
the default window is 30 days. All times are UTC and the end is exclusive.

The report groups actual recorded serving policy, model version, artifact ID,
training timestamp and surface. Missing identities remain `unknown`. A Legacy
row can contain the challenger artifact as contextual information: it does not
mean that Legacy used that artifact to rank. Retraining creates a separate
cohort when its recorded identity changes.

Delivery events count recorded impressions; gallery counts deduplicate URLs
within a cohort. Visibility requires `viewport-v1` with a valid confirmation
between delivery and report end. Older uninstrumented events are identified
separately; missing visibility is never treated as a negative outcome.

Only the latest feedback ID per gallery as of report end can contribute an
outcome. It must itself be inside the reporting window and have an exact
`feedback_impression_links` entry pointing to a delivered impression in this
window, on the same surface and gallery, created no later than feedback.
There is no attribution by proximity in time. A newer unlinked correction
removes older credited feedback; undo naturally restores the previous row.
Feedback on a different surface likewise supersedes the earlier outcome.

Positive, negative and neutral outcomes are separate. `duplicate_update`
is excluded from preference counts. Negative reasons come only from latest,
exactly linked negative feedback. Unknown preference counts include galleries
without a usable preference outcome, including duplicate exclusions and
invalid votes; neutral outcomes remain a separate known category.

Positive fraction uses positive / (positive + negative), never delivered
galleries. Observed positives per 100 visible uses positives whose exact linked
impression was already confirmed visible by feedback time divided by distinct
visible galleries in that cohort. It is an observed yield, not an estimate of
the preferences of unrated galleries. Gallery counts overlap across cohorts
and must not be summed; one latest outcome can credit at most one cohort.

These are observational, self-selected groups with potentially different
queues and time periods, not a randomized A/B experiment or evidence of causal
improvement. They are not calibrated probabilities or formal model acceptance.
The 100-label reminder counts exactly attributed explicit Review galleries
inside the selected window, across known serving policies; it is not a count
since the last assessment. It cannot activate a model. Formal acceptance gates
and Review-only trial scope are unchanged.

The report issues SELECT statements only. It performs no training, artifact
writes, acceptance changes or upstream network requests. Tests use synthetic
in-memory SQLite fixtures:

```powershell
& .\.venv-rocm\Scripts\python.exe -B -m unittest discover -s tests -p test_ranking_insights.py
```
