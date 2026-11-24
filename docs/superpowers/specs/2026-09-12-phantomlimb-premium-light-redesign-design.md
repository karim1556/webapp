# PhantomLimb Premium Light Redesign

Date: 2026-09-12
Status: Approved direction; implementation-ready

## Design read

PhantomLimb is a live phantom-limb pain monitoring PWA for patients who need a calm, trustworthy view of wearable signals. The redesign should feel like a premium clinical instrument: light, quiet, exact, and human. The page should communicate the present state of the body without turning care into a cockpit.

Design dials:

- `DESIGN_VARIANCE`: 8 — asymmetric composition and a distinct signal-hero treatment, with disciplined repetition elsewhere.
- `MOTION_INTENSITY`: 7 — orchestrated state transitions and data emphasis, with reduced-motion fallbacks.
- `VISUAL_DENSITY`: 4 — enough telemetry to be useful, with strong hierarchy and breathing room.

## Visual system

### Palette

- Porcelain canvas: `#F7F8F6`
- White surface: `#FFFFFF`
- Ink: `#172A2D`
- Secondary ink: `#385256`
- Muted text: `#6F8585`
- Quiet border: `#DDE8E5`
- Connected teal: `#0F827C`
- Soft teal wash: `#E8F3F0`
- Signal coral: `#E46F56`
- Soft coral wash: `#FCECE7`
- Cobalt detail: `#3D74C6`
- Soft cobalt wash: `#EDF3FC`

The default experience remains light. Alert colors are semantic and sparse: coral appears when pain likelihood or a warning needs attention, not as a decorative accent.

### Typography

- Display and page titles: `DM Sans`, 600–700, tight tracking, used for warmth and legibility.
- Body and labels: `DM Sans`, 400–600.
- Measurements and technical metadata: `IBM Plex Mono`, 500–600.

Typography creates hierarchy instead of oversized cards: small labels are quiet, titles are compact and confident, and numbers become the main visual anchors where the data requires it.

### Shape, spacing, depth

- 8px radius for cards and controls, 12px only for major hero surfaces.
- Hairline borders on every surface; shadows are soft and reserved for overlays and install dialogs.
- Base spacing follows a 4px rhythm, with 24px/32px/48px section steps.
- Avoid decorative gradients, excessive blur, pill-shaped everything, and large shadowed containers.

## Shell and navigation

The authenticated shell keeps the existing responsive sidebar behavior and route structure, but becomes a slim instrument rail. The brand mark and user identity are compact. Navigation is grouped into Monitor, Reflect, and Account through spacing rather than extra headings. The active item uses a pale teal wash, ink text, and a slim teal rule. Install App remains available as a secondary action, and logout stays isolated in the footer.

At widths below 760px, the sidebar becomes an off-canvas panel with the existing toggle and overlay behavior. Content keeps safe-area padding. The visual treatment should imply a mobile bottom surface through spacing and fixed controls only if it can be done without changing route semantics; the current sidebar toggle remains the accessibility fallback.

## Dashboard

The dashboard opens with a two-part top composition:

1. A compact context header with greeting, current time, and device connection state.
2. A live signal hero with an asymmetric grid: the pain likelihood title and probability lead on the left, a calibrated arc gauge on the right, and a compact assessment footer below. The hero uses porcelain/white surfaces, a teal hairline, and coral only for an active pain state.

Telemetry follows with a concise section heading and six existing cards. Cards are visually differentiated by data role rather than arbitrary color: heart rate is the primary wide card, movement and breathing are compact support cards, EMG/orientation are technical detail cards, and haptic feedback is a response card with a restrained motor indicator.

The signal trend chart stays below telemetry with cleaner axes, low-contrast gridlines, a concise legend, and a clear last-sample state. All existing IDs used by `dashboard.js` and Chart.js remain unchanged.

## Secondary pages

- History: keep the stats and table data model, but make the first row a calm summary band and use grouped metrics with stronger date/value alignment. Avoid a dense spreadsheet feel.
- Diary: keep entry creation and modal behavior; emphasize the progress summary and make the entry list read like a clinical journal rather than an admin table.
- Settings: keep all configuration fields and forms; divide device connection, alert preferences, and account information with quiet section rules and clearer field labels.
- Auth: use the same light instrument language, with a restrained split surface on desktop and a stacked layout on mobile. Keep the existing form actions, error messages, and reset flows.
- Offline: make the offline state feel intentional and actionable, with a simple status mark, clear explanation, and return action.

## Motion and state behavior

- On load, the shell fades in while the live signal hero settles upward by a small distance.
- Live metric changes use number transitions only when values change; no constant shimmer.
- Status changes use color and text, never color alone.
- Pain alert state may use a brief coral edge pulse once per new revision, then remain still.
- All motion is disabled or reduced under `prefers-reduced-motion: reduce`.

## Implementation boundaries

- Preserve Flask routes, authentication, PWA manifest/service worker, API contracts, polling cadence, Chart.js integration, and all element IDs consumed by scripts.
- Prefer a focused CSS system rewrite plus small template class refinements over changing application logic.
- Reuse existing SVG icon markup; no new external image assets are required for this product dashboard.
- Add accessible focus-visible styles, contrast-safe text, semantic status text, and maintain existing mobile safe-area behavior.

## Verification

Run the Flask app locally and verify:

- Dashboard renders in authenticated and empty/loading states.
- Live polling updates all telemetry IDs and pain state correctly.
- History, diary, settings, login, register, forgot/reset, and offline pages inherit the new system without broken layouts.
- Desktop widths around 1440px and mobile widths around 390px remain readable.
- Sidebar toggle, overlay, flash dismiss, install flow, and modal controls continue working.
- No console errors from the redesign, and no required API data bindings are removed.
