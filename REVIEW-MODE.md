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

- A Grid / Focus segmented control is available only on the Review view.
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

Feedback can also change eligibility, interest bands and ranking. The next
append after a local removal or undo therefore scans from offset zero without
replacing or reordering existing queued cards. Duplicate-only pages are skipped
until an unseen URL or actual exhaustion is found. Non-advancing/out-of-range
cursors fail with a retryable error. Existing delivery contexts are preserved;
new deliveries still require independent viewport visibility confirmation.

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

No reader, high-resolution fetcher, new sampling policy, download basket,
automatic download, resolution memory, batch labeling or ranker activation.
Cross-page session restoration beyond the freshly loaded page is not promised.

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
