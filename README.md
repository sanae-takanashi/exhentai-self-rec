# exhentai-self-recommend

A small local recommendation engine for ExHentai galleries.

It stores your login cookies locally, fetches recent/search result pages, ranks galleries from your bootstrap tags and feedback, and lets you vote with thumbs up/down so the model can adapt over time.

## Features

- Local web UI for recommendations and voting.
- Cookie-based ExHentai access.
- Cookie input accepts a normal `Cookie:` header, multiline `name=value` cookie fragments, copied browser cookie-table rows with or without headers, or Netscape/curl cookie file rows.
- Stored cookies can be cleared from the settings panel without deleting preferences.
- Bootstrap preferences with positive/negative weights across tags, title text, category, and uploader metadata.
- SQLite storage for galleries, settings, votes, and learned feature weights.
- Online learning from thumbs up/down or 1-5 scores using title tokens, categories, uploaders, and parsed tags, with namespace-aware weighting for stronger identity tags.
- Visual learning from cover thumbnails and stored random page samples. DINOv2 is the default visual encoder when optional `torch`, `transformers`, and `Pillow` dependencies are installed; otherwise the browser falls back to compact `8x8` RGB canvas embeddings. The ranker scores candidates by similarity to your liked-minus-disliked visual preference direction.
- Repeated same-direction feedback on the same gallery adds a small capped confidence boost, while a later opposite vote resets that direction.
- Conservative gallery-detail enrichment so recommendations learn from full gallery tags, not only titles. Refreshes prefer promising galleries that have not already been detail-enriched.
- Random page-sample previews per gallery so you can judge content beyond the cover. Each enriched gallery stores `5 + (pages // 100)` random page thumbnails (proxied via `/thumb`) and shows them directly on the recommendation card with the cover thumbnail. The first list page comes free from the detail fetch; the `Sample pages` setting (`sample_extra_pages`, default 2) caps how many extra list pages are fetched to widen the sample spread for large galleries. Accounts using ExHentai's large-thumbnail preference get clean per-page samples; the compact sprite layout yields combined sprite strips.
- Fetch and enrichment runs retrain the model whenever they add detail metadata, so feedback on an already-rated gallery can immediately learn from the fuller tag set.
- Detail parsing reads normal tag links and ExHentai taglist attributes, including `artist:`, `female:`, `parody:`, and related namespaces.
- List/detail parsing reads thumbnails from normal image tags and inline CSS background URLs.
- Gallery and tag links from either `exhentai.org` or `e-hentai.org` are accepted, including relative gallery paths; stored gallery URLs are canonicalized to `exhentai.org`.
- On-demand enrichment for the current top recommendation queue without fetching new result pages.
- Learned query expansion: positive feedback teaches the fetcher which tags to search next.
- Deterministic retraining from feedback history using the latest vote/score per gallery.
- Preference export/import for bootstrap tags and feedback. Cookies are not exported.
- A settings "Danger zone" `Reset data` button (with a confirmation prompt) wipes fetched galleries, votes, the learned model, and fetch history while keeping your cookie and bootstrap tags. The same reset is available at `POST /api/reset` and is refused while a fetch or enrichment is running.
- Paginated recommendation browsing with `Load More` once the local gallery pool grows.
- Local filtering by stored gallery title, tag, category, or uploader.
- Configurable recommendation candidate pool so older local galleries can still be considered by the learned ranker.
- Fetch-plan preview showing recent, bootstrap, learned, or manual queries before a refresh.
- Fetch status history so you can see recent refreshes, queries, counts, and errors.
- Background refresh while the server is running, with the browser queue reloading after completed refreshes.
- Refresh status shows the background worker's last check, next scheduled check, and latest loop error when available.
- Auto-refresh wakes promptly when you save cookies, bootstrap tags, or refresh settings instead of waiting for the previous sleep window.

## Quick start

```bash
python3 -m exh_rec.app
```

Open <http://127.0.0.1:8787>.

The server binds to `0.0.0.0` by default, so another device on the same network can open `http://<server-ip>:8787` if your firewall allows the port. To force local-only access, run:

```bash
EXH_REC_HOST=127.0.0.1 python3 -m exh_rec.app
```

## Windows virtualenv

From PowerShell in the repo root:

```powershell
.\scripts\setup-venv.ps1
.\scripts\run.ps1
```

Open <http://127.0.0.1:8787>. The helper creates `.venv`, installs runtime dependencies from `requirements.txt`, and runs `python -m exh_rec.app` from that virtualenv.

For DINOv2 on CPU:

```powershell
.\scripts\setup-venv.ps1 -Visual cpu
.\scripts\run.ps1 -VisualEncoder dinov2 -DinoDevice cpu
```

For DINOv2 with CUDA, install a CUDA-enabled PyTorch build. If your PyTorch/CUDA wheel index is known, pass it explicitly:

```powershell
.\scripts\setup-venv.ps1 -Visual cuda -TorchIndexUrl "https://download.pytorch.org/whl/cu121"
.\scripts\run.ps1 -VisualEncoder dinov2 -DinoDevice cuda
```

On a slow VPS, keep DINOv2 disabled and use browser/simple embeddings:

```powershell
.\scripts\run.ps1 -VisualEncoder simple
```

If your PowerShell policy blocks local scripts, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

In the settings panel:

- Paste your ExHentai cookie header, usually including `ipb_member_id`, `ipb_pass_hash`, and `igneous`. You can also paste multiline `name=value` cookie fragments, copied browser cookie-table rows with or without the header row, or Netscape/curl cookie file rows; the app stores only the cookie name/value pairs.
- Non-empty cookie input must parse into `name=value` cookie pairs. Malformed cookie text is rejected without replacing a previously saved cookie.
- The settings panel previews stored cookie key names and warns if common ExHentai login keys such as `ipb_member_id`, `ipb_pass_hash`, or `igneous` are missing. Cookie values are never shown in the preview.
- Click `Check Login` to verify the stored cookie can see gallery listings before running a full fetch.
- Failed login checks show the specific access-check message instead of a generic HTTP error, so expired or incomplete cookies are easier to diagnose.
- Saving a new non-empty cookie clears the previous login-check result so stale verification is not shown for a replaced cookie.
- Use `Clear Cookie` if you want to remove the stored login cookie while keeping bootstrap tags, feedback, and fetched galleries.
- Add bootstrap preferences, one per line or comma-separated. Tags like `artist:name`, metadata like `category:manga` or `uploader:name`, and plain title terms are supported. Underscore tag input such as `artist:some_name` is normalized to match parsed ExHentai tags. Use `-tag` or `tag:-2` for negative preferences. Numeric namespaced values like `parody:1984` are preserved; add an extra suffix such as `parody:1984:2` to weight them.
- Namespaced bootstrap preferences such as `artist:name` and `female:tag` match exact parsed tags or metadata. Plain preferences without a namespace match title/tag text on term boundaries.
- Set `Details` to the maximum number of fetched galleries that should be opened for full tag metadata per refresh. The app spends this budget on newly fetched galleries that already look promising under your bootstrap and learned model. `0` disables detail-page enrichment.
- Set `Learned` to the maximum number of positive learned tags that should be added to each refresh query plan. `0` disables learned query expansion.
- Set `Pool` to the number of recent local galleries that should be scored before the recommendation page is sliced. Higher values let older fetched galleries compete with newer ones.
- Blank or invalid numeric settings fall back to safe defaults and are clamped to the supported ranges.
- Click `Save`, then `Fetch`.

The refresh panel shows the current fetch plan. Typing an optional one-off search query and leaving the field updates the plan preview to that manual query.

Blank or whitespace-only one-off search input uses the normal recent/bootstrap/learned fetch plan.

Generated bootstrap and learned tag queries quote multi-word tag values for ExHentai search reliability, while keeping the plain tag label visible in the plan.

Positive bootstrap preferences are added to the fetch plan by descending weight, with at most six bootstrap-driven searches per refresh.

Learned query expansion skips tags that exactly match a negative remote-search bootstrap preference, so a disliked tag is not reintroduced as an automatic learned search. Manual one-off searches still run exactly what you type.

The refresh panel also shows whether auto refresh is disabled, waiting for a saved cookie, or ready to run at the configured interval.

When auto refresh is running, the refresh panel shows the worker's latest check time and next scheduled check time. If the background loop hits an unexpected error before a normal fetch run is recorded, the latest loop error is shown there too.

Local metadata preferences such as `category:manga` and `uploader:name` affect ranking and detail selection, but are not used as generated remote search queries.

Saving cookies, bootstrap tags, or refresh settings wakes the background refresh worker so the new plan can take effect promptly while the server is running.

The refresh panel also shows recent fetch history in `status:fetched/enriched` form so background refresh behavior is visible at a glance.

Fetch history counts `fetched` as galleries seen on fetched pages and `stored` as newly discovered gallery URLs, so repeated refreshes make duplicate-heavy runs obvious.

Refreshes that return zero galleries are marked failed with a message to check the cookie, access, or search terms instead of being shown as successful empty runs.

When a background refresh finishes, the browser reloads the first recommendation page automatically so newly fetched galleries enter the queue without a manual page refresh.

Recommendation page responses include the latest fetch or enrichment summary, so the browser can keep the queue and refresh panel aligned.

Recommendation cards show the cover thumbnail, visible page-sample previews when available, model score, uploader metadata, and the current feedback signal you have given that gallery. Thumbs are strong positive/negative signals. A 1-5 score maps to a softer signal: 1 is negative, 3 is neutral, and 5 is positive. `Skip` records a neutral score of `3`.

Invalid API query numbers fall back to safe defaults, while invalid feedback vote/score values return clear bad-request errors.

Recommendation reasons include bootstrap matches, learned feature hits, rating adjustments, and freshness boosts when those factors affect the score.

The ranked queue applies a small diversity penalty to repeated artists, groups, parodies, characters, and uploaders so one learned preference does not completely crowd out nearby alternatives.

When you vote or score a gallery that still has only list metadata, the app uses your saved cookie to fetch that gallery's detail page in the background of the same action and retrains from the fuller tag set. If no cookie is saved or the detail request fails, the feedback still records normally.

Rated galleries are hidden from the main queue by default after you vote, score, or skip them. A neutral score of `3` also hides the gallery but does not add positive or negative learned weight. Enable `Rated` in the toolbar to review already-rated galleries.

While a vote, score, skip, or clear action is being saved, the affected gallery's feedback buttons are temporarily disabled so accidental double-clicks do not submit duplicate in-flight feedback.

Rated cards include `History` so you can inspect the exact vote/score events currently feeding the learned model.

Use `Load More` below the recommendation grid to page through additional scored candidates from the local gallery pool.

Use the local filter field to narrow recommendations already stored in SQLite by title, tag, category, or uploader. Filtered views search the full bounded local pool, even when the normal recommendation pool is smaller. This does not fetch a new ExHentai search; use the one-off search field and `Fetch Query` for that.

Use `Enrich` to open detail pages for the best currently recommended galleries that still have only list metadata. This uses the same `Details` limit and saved cookie, but does not fetch new result pages.

Fetch and Enrich retrain the learned model after successfully saving new detail pages, so any existing feedback on those galleries starts using the fuller metadata right away.

Detail enrichment preserves the gallery's original listing freshness, so opening metadata for an older gallery does not make it look newly fetched in the recent queue.

Use `Clear` on a rated card to remove that gallery's feedback history, retrain the model, and put it back into the unrated queue.

Use `Retrain` to rebuild learned weights from stored feedback. This is also done automatically at server startup and after every new vote/score.

Learned feature weights are intentionally simple and inspectable. The model view separates positive and negative learned weights so you can see what the recommender is favoring or avoiding. Artist/group/parody/character tags and uploaders get more learning signal than broad categories or noisy title words; language tags are learned more gently.

Visual embeddings can use either browser-side simple vectors or server-side DINOv2. Use `Visual encoder` in the settings panel, or `EXH_REC_VISUAL_ENCODER=simple`, on slow CPU-only machines. Use `dinov2` on a laptop/workstation with enough CPU/GPU. DINOv2 uses `facebook/dinov2-small` by default, configurable with `EXH_REC_DINOV2_MODEL`. Install the optional stack with your preferred PyTorch build plus `transformers`, `torchvision`, and `Pillow`; if DINOv2 is disabled or cannot load, the browser decodes same-origin `/thumb` images into the existing `8x8` RGB fallback vector. A gallery's vector uses its cover thumbnail plus up to ten stored random sample thumbnails. Once you rate galleries that have vectors, recommendations can include `visual +...` or `visual -...` reasons.

Set `DINOv2 device` in the settings panel, or use `EXH_REC_DINOV2_DEVICE`, to control where PyTorch runs:

```bash
EXH_REC_DINOV2_DEVICE=cpu python3 -m exh_rec.app
EXH_REC_DINOV2_DEVICE=cuda python3 -m exh_rec.app
EXH_REC_DINOV2_DEVICE=cuda:0 python3 -m exh_rec.app
```

`auto` is the default and uses CUDA when `torch.cuda.is_available()` is true, then falls back to CPU. Use a CUDA-enabled PyTorch install on your laptop if you want GPU acceleration; CPU-only PyTorch is enough when you want to avoid GPU packages.

To explicitly avoid DINOv2 on a VPS:

```bash
EXH_REC_VISUAL_ENCODER=simple python3 -m exh_rec.app
```

If you rate the same gallery more than once in the same direction, the latest signal gets a small capped confidence boost during retraining. A later opposite vote changes the learned direction instead of preserving the older streak.

Use `Export` and `Import` in the backup panel to move bootstrap tags and feedback history between local installs. `Replace data` clears existing local bootstrap tags and feedback before import. Exports intentionally do not include your ExHentai cookie.

Imports require the `exh-rec-preferences-v1` export schema; unsupported files are rejected before changing local preferences.

Import skips malformed rows, including non-object entries, gallery rows without URLs, invalid tag JSON, invalid gallery ratings, invalid bootstrap weights, and feedback votes outside `-1` to `1`. Imported scores outside `1` to `5` are ignored; valid score-only feedback derives its learning signal from the score. Import only reports galleries and feedback that could be applied to the local database.

Data is stored in `data/recommender.sqlite3` by default. Override with:

```bash
EXH_REC_DATA_DIR=/path/to/private/data python3 -m exh_rec.app
```

Thumbnails are served through the local app instead of hotlinked from ExHentai, then cached under `data/thumbs`. This lets the server use the saved cookie and gallery referer when ExHentai's thumbnail host rejects direct browser requests.

Cached thumbnail image bytes live on disk as files, not in SQLite. SQLite holds only metadata (gallery info, settings, votes, feature weights, and sample thumbnail URLs). Each cached thumbnail is stored as two files named by the `sha256` of its source URL under `data/thumbs/`: a `.bin` with the raw image bytes and a `.json` with the source URL, content type, and fetch time. Cover thumbnails and the per-gallery sample previews share this cache. It is permanent (no automatic eviction or TTL) and capped at 5 MB per image; to reclaim space you can safely delete `data/thumbs/` and images will be re-fetched on demand.

Set `Proxy` in the settings panel, or use `EXH_REC_PROXY`, to route ExHentai page fetches, detail enrichment, sample-page fetches, and proxied thumbnails through a local proxy:

```bash
EXH_REC_PROXY=http://127.0.0.1:7890 python3 -m exh_rec.app
EXH_REC_PROXY=https://127.0.0.1:7890 python3 -m exh_rec.app
EXH_REC_PROXY=socks5://127.0.0.1:1080 python3 -m exh_rec.app
```

SOCKS5 support requires PySocks:

```bash
python3 -m pip install PySocks
```

The Windows venv helper installs PySocks through `requirements.txt`.

When a proxy is configured, the app also sets `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` for the server process before DINOv2 model loading, which helps Hugging Face downloads use the same route.

Override the bind address or port with:

```bash
EXH_REC_HOST=0.0.0.0 EXH_REC_PORT=8787 python3 -m exh_rec.app
```

## Safety notes

This is a personal tool. Cookies are stored in plaintext SQLite so the scraper can reuse them. Keep the data directory private and do not commit it.

By default the web server listens on all interfaces for remote access. Do not expose it to the public internet without firewall rules, a VPN, SSH tunnel, or an authenticating reverse proxy.

The scraper intentionally fetches normal result pages and does not attempt to bypass access controls. You are responsible for using the site within its terms and with your own account.

## Tests

```bash
python3 -m unittest discover -s tests
```
