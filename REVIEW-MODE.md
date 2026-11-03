# Focus Review

## Workflow And Boundary

The owner browses the Review list, opens a gallery in an ExHentai tab, inspects
pages there, optionally downloads through the site's own resolution dialog,
then returns to this application to rate it. Obvious negatives may be rated
from their cover without opening the site.

Focus Review itself changes only this application. It does not inject scripts into
ExHentai, inspect its tabs, trigger downloads, select resolutions, or equate a
click/download with an explicit preference.

An optional, separately installed read-only userscript adds keyboard reminders
on native ExHentai gallery/Archiver pages. See KEYBOARD-WORKFLOW.md. The app does
not inject or install it, and the script does not submit downloads or feedback.

## First Version

- A Grid / Focus segmented control is available on Review and Low Interest.
  Both views use the same rating, reason, bookmark, classification, metadata,
  navigation and single-level undo controls, samples, tags and detail sections.
  Low Interest retains its band badge; the ranking trial switch stays Review-only.
  Grid remains the initial default. The chosen layout and current URL are
  remembered in tab-local session storage when available; no labels, cookies,
  candidate snapshots or browsing history are stored there. A remembered URL
  is restored only if it is still in the freshly returned eligible page.
- Focus shows one queued gallery, an uncropped cover, the existing cached
  sample thumbnails, title, metadata and tags. The primary rating controls
  stay in a consistent footer, together with the optional reason selector,
  Favorite, Ban and Mark as Updates. More contains only metadata refresh,
  feedback history/clear and parent/related details. Existing score/vote/Skip meanings do
  not change; Skip still records a neutral score, not a temporary deferral.
- The cover and title remain ordinary noreferrer links opening a new tab.
  Only a click on one of those links sets an Opened indicator in this tab.
  Returning from the site does not advance, rerank or label anything.
- Previous / Next navigate the loaded queue without submitting feedback.
  Navigation never opens the external site. More candidates can be loaded at
  the queue boundary. Duplicate URLs are not appended.
- A successful score, thumb vote or Skip removes the current item and shows
  its successor; a failure leaves it in place with controls enabled for retry.
  Pending operations disable navigation and prevent duplicate submissions.
  The existing request-generation guards remain in force across mode/view
  switches, list requests, feedback and undo.
- Keys 1-5 rate, A/D thumb down/up, S skips, O activates the normal gallery link,
  J/K (or left/right) navigate next/previous, and Z undoes the last rating.
  Q/W cycle the previous/next reason, including the empty option, without moving
  focus or submitting feedback. There is no selection mode or Enter confirmation;
  J/K always navigate galleries, and 1-5 always rate. R is not an app shortcut.
  Shortcuts are active only in Focus
  Review, ignore key repeat/composition/modifiers, and do not intercept text
  inputs, selects, editable content or an open dialog. Tooltips name controls
  and their shortcuts. A compact footer reminder can be collapsed with its
  keyboard icon; that state survives card changes in the current page.
- Undo is single-level and applies only to the last successful score/vote/Skip
  submitted from Focus. It is not Clear Rating: the server checks the gallery
  and exact latest feedback ID under a write transaction, removes only that
  event, and preserves earlier feedback, marks and downloads. A newer rating
  causes HTTP 409 with no mutation. Only audit outcomes referencing the removed
  feedback ID are reopened as incomplete, with their sampling/exposure facts
  retained; no substitute outcome is invented. Model retraining remains queued.
  A new non-rating mutation or explicit queue restart invalidates the local
  undo slot. Older backends do not advertise undo support, so it stays disabled
  until the updated backend is running.
- Background fetch/visual completion does not replace a Focus queue. It enables
  an explicit refresh control. Refresh and changed filters begin a new queue;
  switching Grid / Focus does not reorder the current loaded candidates.
- The batch received from the API is still a delivered page. Only the rendered
  current card can satisfy viewport-v1 visibility (50% continuous foreground
  intersection for one second). Each candidate retains its original request,
  surface, rank and audit context when reached later. Unshown queued candidates
  never become visible merely because they were fetched or prefetched.
- Exhaustion offers the next local page or a queue refresh, never automatic
  site navigation. Missing covers, long titles, empty results, API failures,
  unavailable browser storage and repeated mode switches remain usable.

## Queue Continuation

Successful feedback or classification at the Focus queue tail automatically
checks for a successor. Grid also refills when the loaded batch becomes empty.
This check runs even if the previous response said No More: feedback can admit
new candidates into the bounded recommendation pool. A failed continuation
does not undo or resubmit the successful action; Next, Load More and Refresh
remain available to retry, and Focus rating undo remains valid.

Review and Low Interest list reads bypass the browser cache and have a 30-second deadline for
the entire continuation scan, including response bodies and duplicate pages.
Loading is shown separately from an exhausted queue. A stalled read releases
the controls at the deadline, preserves committed feedback and the undo slot,
and never automatically retries a feedback POST. Late results cannot append
after the deadline; the owner can retry the list read with Next or Load More.

Feedback can also change eligibility, interest bands and ranking. The next
append after a local removal or undo therefore scans from offset zero without
replacing or reordering existing queued cards. Duplicate-only pages are skipped
until an unseen URL or actual exhaustion is found. Non-advancing/out-of-range
cursors fail with a retryable error. Existing delivery contexts are preserved;
new deliveries still require independent viewport visibility confirmation.

Review may finish while Low Interest still contains galleries. Retained tail
memberships never promote a gallery just to keep one ordinary Review item.
The bounded daily audit protocol remains unchanged; it is not an exhaustion refill.

## Implementation

Keep the existing API page and card data as the queue. Add a presentation state
and focused rendering path in static/app.js rather than a second recommender.
Use the same delegated feedback actions in both layouts. Keep a delivery-context
map separate from DOM visibility subscriptions so navigation can observe the
new current element without fabricating another delivery. Dispose detached
observers and preserve the current item during background activity.

The only new server operation is POST /api/feedback/undo, taking gallery_url
and a positive integer feedback_id. It returns no reranked page and performs
no remote requests. The normal feedback response advertises its availability.
No schema migration, model gate, download integration, network exposure or
production launcher changes are required.

## Focus Controls And Classification Follow-Up

Essential controls must be directly visible without opening More or scrolling
the metadata pane, including on narrow mobile screens. Use a compact fixed
footer with reasons/actions above the rating row; its measured height reserves
the gallery work area. Keep covers uncropped and all controls within the viewport.

Classification submission confirms only the committed override. It must not
train the continuing classifier or rebuild either queue in the POST request.
The classifier's existing signature check retrains on its next read; explicit
overrides take precedence immediately. This changes no release/evaluation gates.
After an explicit move succeeds, remove that gallery locally and advance once,
preserving every other loaded item and its delivery context. Do not wait for
queue counts, replace the Focus queue, or apply full pages from older servers.
Failures retain the item and allow retry; duplicate actions remain guarded.
Use Auto clears the displayed override and signals that a queue refresh is
available in Focus; Grid reloads eligibility. Results from a departed view must
never overwrite its replacement. Classification is not a rating or download.

## Acceptance

1. Desktop and mobile both display a single usable gallery and fixed-position
   primary actions, without cropped covers or horizontal page overflow.
2. Native external links retain target=_blank and rel=noreferrer. The app adds
   no browser extension, injected remote script or automatic popup. The optional
   user-installed hint script remains isolated from Review and native actions.
3. Scoring from the cover and returning to score after site browsing both
   advance exactly once, only after a successful response.
4. Navigation/mode changes do not generate preference labels or reorder items.
5. Failed and duplicate submissions, delayed list responses, view switches,
   queue exhaustion and unavailable session storage are covered by tests.
6. Undo preserves older feedback; stale IDs and concurrent newer feedback
   fail closed. Audit selection/visibility and unrelated state remain intact.
7. Only actually displayed cards acquire confirmed-visible events, including
   after navigation, feedback, delayed delivery acknowledgment and mode changes.
8. Existing frontend feedback/visibility regressions and focused backend tests
   pass. Browser tests use synthetic galleries and blocked external networking,
   never production votes, credentials or downloads.

## Deferred

No remote reader, high-resolution fetcher, new sampling policy, download basket,
automatic download, resolution memory, batch labeling or ranker activation.
Cross-page session restoration beyond the freshly loaded page is not promised.

## Local Page Previews And Comparison

An optional, separately built local library now supplies preview fallbacks in
Review. Site covers and stored samples remain preferred. Missing covers use
the first sampled local file; failed site images try one local fallback and
then show an unavailable state. Missing site samples use the existing
first/middle/last file samples. Local files are labeled, not presented as
verified site page numbers or original-resolution images. Cover/title links
keep the normal native-site destination; local sample buttons inspect an
already-downloaded file in a modal and never open the remote site.

The Local Pages toolbar icon opens a separate read-only library dialog.
Compare samples on a matched Review card opens that gallery directly. The
dialog offers every indexed gallery, including metadata gaps, sample inspection,
and two Top-3 columns. Cover similarity uses only the cached cover vector;
inner-sample similarity averages downloaded sample vectors excluding file
position 1. Both columns search the identical paired candidate set, deduplicated
by gid with the widest sampled version preferred; all versions of the query
gid are excluded. Missing/ambiguous metadata does not invent a site link.
Without a cover or body vector, local samples remain available but the
comparison is explicitly unavailable. Cosine is similarity, not a preference
probability, and filename order is not a verified site-page identity.

The dialog has independent catalog/comparison request-generation guards.
Changing the selection or closing it invalidates old responses. Retry, empty
libraries, invalid libraries, failed comparisons and unavailable images remain
usable. Native modal focus blocks Focus Review rating shortcuts. Inspection
does not submit labels or reorder the current queue; the local vectors are
never passed to the production visual-embedding endpoints or ranker.

Build and preview instructions are in README.md. Serving a new backend requires
the owner's normal restart. The separate loopback-only preview server uses a
read-only database connection, refuses POST, and starts no production workers.

## Verification And Rollout

Run the focused regressions from the repository root:

```text
python tests/run_review_checks.py
node --test tests/test_feedback.cjs tests/test_visibility.cjs
node tests/browser_feedback.cjs
node tests/browser_review.cjs
node tests/browser_keyboard_hints.cjs
```

The browser scripts use the existing optional Playwright installation under
data/browser-validation and headless Edge. All routes are synthetic. Screenshots
and reports remain under the ignored data/browser-validation directory. The
Python runner retains temporary fixtures and selects feedback, undo and audit
regressions; it is not a claim that the full unrelated test suite was run.

The existing server serves static assets directly, so refreshing the Review page
loads Grid / Focus. The server process must be restarted by its owner with its
established launcher/environment before exact-event undo is available. The
frontend enables undo only when a new feedback response advertises support.
No production restart or production feedback/download mutation is part of this
rollout verification.
