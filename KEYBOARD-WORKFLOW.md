# Keyboard Workflow

## Review

Use Focus mode. J/K navigate next/previous without feedback (arrows still work),
O opens the ordinary gallery link, 1-5 rate, A/D thumb down/up, S records neutral
Skip, and Z undoes the last supported rating. Thumbs retain their existing mild
vote semantics; they are not aliases for scores 1/5. Input fields, dialogs, pending actions,
composition and held keys retain their existing guards.

Q/W choose the previous/next reason directly, cycling through all options,
including the empty optional reason. Focus stays in Review; there is no selection
mode or Enter confirmation. J/K always navigate galleries, and 1-5 always rate.
Selecting a reason never submits feedback. Reasons accompany only negative
votes or scores 1/2. R is no longer an app shortcut.

The keyboard icon collapses the reminder. Tab to it and press Enter or Space
to toggle it without a mouse. Collapse state survives card changes, not reloads.

## Native Gallery And Archiver

Vimium C supplies link hints (F) and closes the current tab/window (X).
Ctrl+W remains the browser-native close shortcut. In the Archiver, the custom D
mapping focuses an available H@H resolution nearest 1280x, in this order:
1280, 800, 1920, 2560. It never falls back to Original. Enter is a separate,
explicit native download submission and may consume site credits.

The Archive focus mapping was exercised with Vimium C 2.12.3 on Zen. Add these
to existing custom mappings without replacing unrelated settings. The new J/K
configuration still needs to be saved and verified in the user's browser;
headless app tests do not load the installed extension or change its settings.

```text
# ExHentai Archiver: focus only; Enter confirms the native action.
env exArchive host=":https://exhentai.org/archiver.php?"
map d runKey expect={"exArchive":"<v-ex1280>"} keys="<v-defaultd>"
map <v-defaultd> scrollPageDown
# ExHentai only: a screen per J/K; retain default line scrolling elsewhere.
env exSite host=":https://exhentai.org/"
map j runKey expect={"exSite":"<v-exnext>"} keys="<v-defaultj>"
map k runKey expect={"exSite":"<v-exprev>"} keys="<v-defaultk>"
map <v-exnext> scrollFullPageDown
map <v-exprev> scrollFullPageUp
map <v-defaultj> scrollDown
map <v-defaultk> scrollUp
map <v-ex1280> LinkHints.activateFocus direct="em=a[onclick*=\"do_hathdl('1280')\"];em=a[onclick*=\"do_hathdl('800')\"];em=a[onclick*=\"do_hathdl('1920')\"];em=a[onclick*=\"do_hathdl('2560')\"]"
```

For Review, preserve the existing Vimium exclusion that passes through
`1 2 3 4 5 <left> <right> j k q w a d o s z` at `:http://127.0.0.1:18787/`.
Add Q/W to that rule's pass-through keys before trying them in Vimium. The app
cannot override keys intercepted by an extension; changing app code alone does
not update this rule. R is intentionally not a Review binding.
Other sites retain Vimium's D page-down behavior. Link-hint labels vary per page;
use the label currently shown for Archive Download, not a fixed label.

Applying the app code does not update Vimium settings or the installed copy of
the userscript. Update the Review exclusion and custom mappings above, then
update Review Keyboard Hints to 1.1.0 through the existing script manager and
reload the relevant tabs. Keep unrelated exclusions (such as Gmail) intact.

## Optional On-Screen Reminders

`static/review-keyboard-hints.user.js` is an opt-in script for an existing
userscript manager such as ScriptCat. With the local app running, its installer
URL is `http://127.0.0.1:18787/static/review-keyboard-hints.user.js`.
Review the script, then explicitly install it in the manager. Reload existing
gallery tabs or open new ones after installation. No server restart is needed.

The small bottom dock shows gallery/Archiver shortcuts, offers the same
keyboard-accessible collapse icon, and reports the actual focused resolution.
"Available" is not "focused". The script does not bind J, K, D, X or Enter and does
not verify that Vimium mappings exist; the above configuration is required.
If only Original is offered, use native link hints to choose manually.

Only HTTPS ExHentai gallery-detail pages and archiver.php are eligible. Runtime
checks reject other hosts, paths and child frames. The script uses isolated
Shadow DOM, reserves its own scroll space, and reads native focus/option markup.
It makes no requests, uses no browser storage, reads no authentication data,
does not change native links/forms/handlers, and never downloads or rates.
Disable it in the manager to remove external hints; native browsing and Vimium
continue to work. Each newly loaded page starts with reminders expanded.

## Verification

```text
node --test tests/test_feedback.cjs tests/test_visibility.cjs
node tests/browser_review.cjs
node tests/browser_keyboard_hints.cjs
```

Browser tests use the optional Playwright installation under
`data/browser-validation/node_modules` and headless Edge. All requests are
fulfilled with synthetic fixtures, never real galleries, feedback or downloads.
Screenshots cover desktop, narrow mobile and small popup sizes in light/dark
themes. The external-script tests emulate focus results, not a Vimium install;
live browser verification remains separate and must stop before download Enter.
