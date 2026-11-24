# PhantomLimb Premium Light Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the PhantomLimb PWA UI as a premium, light clinical instrument while preserving all Flask routes, data bindings, PWA behavior, and live monitoring interactions.

**Architecture:** Keep the existing Flask/Jinja structure and JavaScript contracts. Implement the redesign primarily as a focused CSS system pass with small template class refinements only where hierarchy or accessibility requires them, then align Chart.js palettes and verify every route at desktop and mobile widths.

**Tech Stack:** Flask/Jinja templates, CSS, vanilla JavaScript, Chart.js 4, PWA service worker/manifest.

## Global Constraints

- Default experience remains light; use `#F7F8F6` porcelain canvas, `#FFFFFF` surfaces, `#172A2D` ink, `#0F827C` connected teal, and `#E46F56` signal coral.
- Preserve Flask routes, authentication, PWA manifest/service worker, API contracts, polling cadence, Chart.js integration, and all element IDs consumed by scripts.
- Use `DM Sans` for display/body and `IBM Plex Mono` only for measurements and technical metadata.
- Use 8px card/control radii, hairline borders, restrained shadows, and a 4px spacing rhythm.
- Add accessible `:focus-visible` styles, contrast-safe status text, safe-area behavior, and reduced-motion handling.
- Do not introduce new dependencies or external image assets.
- This directory is not a Git repository; do not add commit steps or claim a commit was created.

---

### Task 1: Establish the premium light design system and app shell

**Files:**
- Modify: `static/css/style.css` — replace existing visual tokens and shared shell/component rules with the Quiet Instrument system.
- Modify: `templates/base.html` — update theme metadata/version cache keys and add small semantic hooks only where needed.
- Test: manual browser smoke test for authenticated shell and all shared controls.

**Interfaces:**
- Consumes: existing Jinja classes in `base.html`, all page templates, sidebar toggle script, PWA install/update markup.
- Produces: stable light tokens and shared styles for `.sidebar`, `.main-content`, `.nav-item`, `.page-header`, `.btn`, form controls, flashes, modals, and responsive safe-area layouts.

- [x] **Step 1: Capture the current shared class surface**

  Run:

  ```bash
  grep -oE 'class="[^"]+"' templates/base.html | sort -u
  ```

  Expected: the existing class list is available for matching without removing any IDs used by scripts.

- [x] **Step 2: Replace the token and shared shell layer**

  Update the `:root`, reset, app chrome, shared typography, button, form, focus, modal, flash, PWA, and responsive rules in `static/css/style.css` so they use:

  ```css
  :root {
    --paper: #f7f8f6;
    --surface: #ffffff;
    --surface-soft: #f1f5f3;
    --ink: #172a2d;
    --ink-2: #385256;
    --muted: #6f8585;
    --faint: #9aacaa;
    --line: #dde8e5;
    --line-dark: #c8d8d4;
    --nav: #ffffff;
    --nav-2: #eef5f2;
    --nav-text: #607676;
    --teal: #0f827c;
    --teal-soft: #e8f3f0;
    --blue: #3d74c6;
    --blue-soft: #edf3fc;
    --orange: #c67b37;
    --orange-soft: #fbf1e4;
    --red: #e46f56;
    --red-soft: #fcece7;
    --shadow: 0 16px 40px rgba(23, 42, 45, .055);
    --radius: 8px;
    --sidebar-w: 236px;
  }
  ```

  The sidebar should be white with a right hairline, not a dark block. Active navigation should be teal-tinted with ink text and a slim left accent. Keep `.sidebar-toggle`, `.sidebar-overlay`, `.pwa-network-banner`, `.ios-install-modal`, and all safe-area rules functional.

- [x] **Step 3: Add keyboard and reduced-motion foundations**

  Add:

  ```css
  :focus-visible { outline: 2px solid var(--teal); outline-offset: 3px; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: .01ms !important;
      animation-iteration-count: 1 !important;
      scroll-behavior: auto !important;
      transition-duration: .01ms !important;
    }
  }
  ```

  Ensure buttons, links, fields, modal close controls, and sidebar controls remain visibly focusable.

- [x] **Step 4: Refresh shared metadata and cache keys**

  In `templates/base.html`, set `theme-color` to `#F7F8F6`, keep the PWA title/manifest links, and bump the stylesheet cache query value to a redesign-specific value such as `v='premium-light-1'`.

- [x] **Step 5: Run a shared-shell smoke check**

  Start the app with:

  ```bash
  python app.py
  ```

  Open `/login`, inspect `/offline`, and after authentication inspect any route using `base.html`. Expected: no dark navigation block, no horizontal overflow at 390px, readable controls, and working sidebar toggle/overlay.

---

### Task 2: Recompose the live dashboard as a premium instrument

**Files:**
- Modify: `templates/dashboard.html` — add only semantic hooks for the signal hero and telemetry grouping; preserve all existing IDs.
- Modify: `static/css/style.css` — replace dashboard-specific visual rules with the asymmetric hero, refined telemetry grid, and chart surface.
- Modify: `static/js/dashboard.js` — update only chart colors/typography options and optional new-revision alert class toggling; preserve polling and DOM IDs.
- Modify: `static/js/charts.js` — align shared Chart.js defaults with light background, subdued gridlines, and the new palette.
- Test: live dashboard manual test with empty/loading and populated API states.

**Interfaces:**
- Consumes: `gaugeCanvas`, `liveChart`, all `val*`, `bar*`, `pm*`, `status*`, `motor1`, and `readingContract` IDs.
- Produces: premium dashboard layout without changing API response handling or chart data shape.

- [x] **Step 1: Add dashboard hooks without changing data contracts**

  Preserve these existing IDs exactly: `painBanner`, `signalTitle`, `signalDescription`, `gaugeCanvas`, `gaugeProbText`, `pmPainLevel`, `pmLastUpdate`, `pmPainFlag`, `hapticStatus`, `readingContract`, `liveChart`, and every telemetry ID consumed by `dashboard.js`.

  Add optional classes such as `signal-hero-copy--primary`, `gauge-panel--instrument`, and `telemetry-card--primary` only around existing markup so the CSS can distinguish hierarchy.

- [x] **Step 2: Implement the hero composition**

  Style `.dashboard-topline` as a compact context row. Style `.signal-hero` with a white surface, a teal/coral state edge, an asymmetric grid, and an understated shadow. Use `.hero-number` as the primary anchor, a calibrated arc via the existing canvas, and a compact assessment column. Alert state classes `.pm-badge.warn` and `.pm-badge.pain` remain semantic and readable.

  Keep the hero within one viewport at desktop widths and stack it explicitly at `< 760px`.

- [x] **Step 3: Refine telemetry rhythm and technical detail**

  Use a 12-column desktop grid: the heart-rate card spans 5 columns, posture and breathing each span 3, and the detail/response cards occupy the remaining rhythm. At `< 760px`, use one column. Keep `.telemetry-card--haptic` and all current card IDs working.

  Use quiet surface differences (teal wash, blue wash, coral wash only for alert) instead of six equal white cards.

- [x] **Step 4: Align chart styling**

  In `static/js/dashboard.js`, update live chart dataset colors to teal, cobalt, coral, and orange. In `static/js/charts.js`, use `#6F8585` tick text, `#E5EEEB` gridlines, white tooltips, and `DM Sans` labels. Keep Chart.js 4 configuration valid and disable animation for live updates.

- [x] **Step 5: Verify live states**

  Open the dashboard with no device sample and verify the waiting state remains readable. When API data is present, verify live polling updates probability, pain level, meters, status dot, haptic state, and trend chart. Confirm no console errors and no missing-element warnings.

---

### Task 3: Apply the system to history, diary, settings, auth, and offline pages

**Files:**
- Modify: `static/css/style.css` — style stats, charts, data tables, settings cards, auth surfaces, offline state, and diary modal using the same light instrument system.
- Modify: `templates/history.html` — add minimal semantic classes for summary/table grouping if needed; preserve IDs and inline API script behavior.
- Modify: `templates/diary.html` — add minimal semantic classes for journal/progress emphasis; preserve modal IDs and submit flow.
- Modify: `templates/settings.html` — add grouping hooks for device/account/Firebase sections; preserve form names and action.
- Modify: `templates/login.html`, `templates/register.html`, `templates/forgot_password.html`, `templates/reset_password.html`, `templates/offline.html` — align page-specific hierarchy and status copy without changing routes or form fields.
- Modify: `static/js/charts.js` and any inline page chart configurations — replace remaining purple/cyan/dark chart colors with semantic teal/cobalt/coral/orange values.
- Test: manual route sweep and chart/modal/form interaction smoke tests.

**Interfaces:**
- Consumes: existing Jinja data variables, `historyTableBody`, `diaryTableBody`, `diaryModal`, form action URLs, and chart canvas IDs.
- Produces: consistent secondary surfaces with no changes to server-side data shape or API endpoints.

- [x] **Step 1: Style history as a calm analytical page**

  Preserve the stats grid, chart canvas IDs, table IDs, search input, and filters. Use grouped metric cards with stronger value/label contrast, sparse table dividers, readable badges, and mobile overflow for the table only.

- [x] **Step 2: Style diary as a clinical journal**

  Preserve `openDiaryModal`, `closeModal`, `painSlider`, `sliderVal`, `diaryNotes`, `submitEntry`, `diaryCompareChart`, and `recoveryChart`. Add a stronger progress summary and a warm but restrained modal. Keep slider colors semantic: teal for low, orange for moderate, coral for high.

- [x] **Step 3: Style settings with clear configuration zones**

  Keep device pairing, account, and Firebase content. Use quiet header rows, full-width primary save action on small screens, and readable code treatments for device/path values.

- [x] **Step 4: Align auth and offline states**

  Use a light split-surface auth layout at desktop and a stacked layout at mobile. Make the brand mark, title, fields, error flashes, and links inherit the same tokens. Keep reset/login/register form semantics unchanged. Make offline state clear, centered, and actionable.

- [x] **Step 5: Run route and interaction sweep**

  Visit `/history`, `/diary`, `/settings`, `/login`, `/register`, `/forgot-password`, `/reset-password`, and `/offline` (or the existing registered route names). Verify charts initialize, diary modal opens/closes/submits, forms retain labels and actions, and table rows remain readable at 390px.

---

### Task 4: Responsive, accessibility, and visual QA pass

**Files:**
- Modify: `static/css/style.css` — final breakpoint tuning, motion polish, and overflow fixes.
- Modify: `templates/base.html` — final semantic/focus improvements if browser inspection identifies gaps.
- Test: browser screenshots or visual inspection at desktop/mobile plus shell interaction checks.

**Interfaces:**
- Consumes: all prior styles and page templates.
- Produces: final verified responsive/light premium UI with no functional regressions.

- [x] **Step 1: Verify desktop composition**

  At approximately 1440×900, confirm the sidebar, dashboard hero, telemetry grid, charts, and secondary-page cards have consistent margins and no two-line desktop nav.

- [x] **Step 2: Verify mobile composition**

  At approximately 390×844, confirm sidebar toggle/overlay, safe-area padding, stacked hero, one-column telemetry, scrollable tables, modal fit, and no horizontal page overflow.

- [x] **Step 3: Verify accessibility states**

  Keyboard-tab through nav, buttons, fields, modal controls, and form actions. Confirm visible focus outlines, status text not reliant on color alone, sensible heading order, and adequate contrast for muted text.

- [x] **Step 4: Verify reduced motion**

  Enable `prefers-reduced-motion: reduce` in browser devtools. Expected: no hero entrance movement, no continuous alert pulse, and live updates remain understandable.

- [x] **Step 5: Run final regression checks**

  Run:

  ```bash
  python -m py_compile app.py routes/*.py database.py config.py models.py
  ```

  Then reload the dashboard several times, check the browser console, and confirm the service worker and manifest still load. Report any backend or data issues separately from visual issues.
