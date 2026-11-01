---
name: Retail Decision Intelligence Agent
description: A dunnhumby-branded store-recovery decision surface built as shelf-edge labels - every store a label, the magenta clearance sticker says act now.
colors:
  primary: "#2a1b6a"
  brand-green: "#2abf95"
  brand-green-ink: "#0e7a55"
  brand-magenta: "#cf348d"
  brand-magenta-ink: "#a4285e"
  neutral-bg: "#f2f7f4"
  surface: "#ffffff"
  panel: "#f7f9fa"
  line: "#d7e2df"
  muted: "#54616b"
  ink: "#212048"
  slate: "#6f74a8"
  green-soft: "#e7f6f0"
  indigo-soft: "#eef0f8"
  rose: "#f9edf5"
  rose-line: "#f0c2dd"
  amber: "#f5e9b8"
  amber-ink: "#8a5a12"
typography:
  display:
    fontFamily: "Space Grotesk, Roboto, sans-serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.055em"
  title:
    fontFamily: "Space Grotesk, Roboto, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.03em"
  body:
    fontFamily: "Roboto, system-ui, sans-serif"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Roboto, system-ui, sans-serif"
    fontSize: "10.5px"
    fontWeight: 700
    letterSpacing: "0.14em"
    lineHeight: 1.4
rounded:
  sm: "4px"
  md: "6px"
spacing:
  sm: "8px"
  md: "12px"
  lg: "18px"
  xl: "22px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0 20px"
  button-primary-hover:
    backgroundColor: "#1e134f"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: "0 16px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
---

# Design System: Retail Decision Intelligence Agent

## Overview

**Creative North Star: "The Shelf Edge"**

The product is a working grocery shelf, not a dashboard. Every store is a shelf-edge label: a white plate with hairline rules, a state tag top-left, the reason printed as the label's name, the facts as the price field, and the decision stamped in a pale-indigo zone. The visitor's eye travels the shelf like a grocer's: three groups, one polarity checked first - the magenta clearance sticker, the grocer's "reduced, time is short" tag, marks every store that needs a human decision. Everything else on the shelf is settled paper; live and safe states stay quiet by design.

The palette is the pinned dunnhumby brand trio (`#2abf95`, `#2a1b6a`, `#cf348d`) doing named jobs: indigo carries all shell ink and decision zones, green carries only the live-data system, magenta is spent only on the act-now polarity. Color never decorates; every colored pixel means something. Depth is settled paper on paper - layered, ambient, nearly invisible - and the one authored motion is the topline sweep, the shelf powering on while the feed wakes.

**Key Characteristics:**
- One signature moment per surface: the die-cut magenta clearance sticker (rotated -1.4deg) on needs-intervention rows.
- Hairline (1px `#d7e2df`) rules structure every plate; no card wears a colored border above 1px on any side.
- Words print only in deepened inks (`#0e7a55`, `#a4285e`) so every word clears WCAG AA on the tint it prints on; the raw brand trio appears only in non-text positions.
- All live figures are Space Grotesk with `tabular-nums`; Roboto carries the body (dunnhumby.com fidelity).
- The state system is a triad, never a rainbow: act-now (magenta), no-signal (slate/indigo tint), settled (green).

## Colors

The palette is committed at three names and kept restrained everywhere else; tints exist so large fields stay pale while inks stay dark.

### Primary
- **Price-Rail Indigo** (`#2a1b6a`): the shell's ink - headings, store IDs, decision-zone values, primary buttons, the 2px nav seal. Prints 14.4:1 on white.

### Secondary
- **Shelf Green** (`#2abf95`): the live-data system only - the topline price-rail, the live dot, ok-state fills, hover mixes. Never used for words.
- **Green Ink** (`#0e7a55`): the same green family deepened for text; 5.3:1 on white, 4.9:1 on the mint ground.
- **Clearance Magenta** (`#cf348d`): the act-now polarity - the clearance sticker outline/fill, the magenta tab, the "6" count on its plate. Non-text only.
- **Magenta Ink** (`#a4285e`): the magenta family deepened for text; 6.4:1 on the rose tint, 6.9:1 on white.

### Tertiary
- **Slate Tag** (`#6f74a8`): the no-signal marker (marker tabs, ids) where a verdict does not exist yet.

### Neutral
- **Shelf Paper** (`#f2f7f4`): the ground the labels sit on.
- **Label White** (`#ffffff`): every plate.
- **Hairline** (`#d7e2df`): all dividing rules.
- **Print Grey** (`#54616b`): captions, count subtitles; 6.0:1 on mint.
- **Body Ink** (`#212048`): paragraph text.
- **Safe Tint** (`#e7f6f0`) / **Review Tint** (`#eef0f8`) / **Rose Tint** (`#f9edf5`) with **Rose Line** (`#f0c2dd`): the three state plate fills, keyed to the triad.
- **Amber Tag** (`#f5e9b8`) with **Amber Ink** (`#8a5a12`): guard/degraded chips ('red for critical, amber for degraded' lives here).

### Named Rules
**The Clearance-Sticker Rule.** `#cf348d` appears only where the meaning is "act now" - the die-cut sticker, its plate count, its tab. A screen with zero needs-intervention stores contains zero magenta. Its rarity is the point.

**The Deep-Ink Rule.** Words never print in the raw brand trio on light ground; green words print `#0e7a55`, magenta words print `#a4285e`. The raw trio is for dots, bars, sweeps, and fills.

**The Edge Rule.** No card container carries a colored border above 1px on any side (including painted pseudo-element stripes). State color enters through backgrounds, tags, counts, and the sticker - never through card edges.

**The One Polarity Rule.** Magenta names the only urgent state. Degraded-but-not-urgent wears amber; missing-signal wears slate; nothing else competes for the red-family voice except guard-critical chips.

## Typography

**Display Font:** Space Grotesk (with Roboto fallback), used only for figures and nameplates.
**Body Font:** Roboto (with system-ui fallback) - matches dunnhumby.com; `impeccable-disable` noted for the documented Space Grotesk brand pairing.

**Character:** Roboto is the label's fine print: plain, dense, honest. Space Grotesk is the printed numerals: tight-tracked, tabular, slightly engineered - the price string, not the prose.

### Hierarchy
- **Display** (700, 30px/1.05 at -0.055em): page nameplates ("Store recovery review", "Store 289").
- **Headline** (700, 21px): dialog titles and section h1 on detail page (hero scales to `clamp(30px, 4.5vw, 46px)`).
- **Title** (600-700, 17-19px): group titles, store IDs, recommended action.
- **Body** (400, 13-15.5px/1.55): reasons, narrative, captions; capped near 80ch.
- **Label** (700, 10-10.5px, +0.12em to +0.16em, uppercase): the tracked shelf-strip identity line, fact labels, zone kickers.

### Named Rules
**The Figure Rule.** Every live figure - counts, percentages, timestamps, store IDs - is Space Grotesk with `font-variant-numeric: tabular-nums`, because the board re-renders every 60s and proportional digits jitter as they change.

## Layout

A single centered column: `.shell` max-width 1160px, 32px side padding. Content stacks flat: brand band → identity strip → gate sentence → three count plates (12px gap) → three collapsible shelf groups (16px apart) → store labels (10px apart) → footer. On the detail page a `1.25fr / 1fr` label grid (why / evidence) with the ask panel beneath. Breakpoints: 900px collapses the detail grid and hero; 640px is the label-scale pass (full-width buttons, stacked facts, sticker stood back up, sheet-style dialog docked to the bottom); 380px drops the decision grid to one column. The 60s poll is a layout fact: every figure sits in a fixed-width tabular slot; nothing reflows when numbers tick.

## Elevation & Depth

Settled paper on paper. Resting plates carry one barely-there ambient layer (`0 1px 1px rgba(21,14,62,.02), 0 6px 18px rgba(21,14,62,.03)`); hover deepens it one step with no vertical lift except on interactive controls (buttons rise 1px, cards press 1px). The clearance sticker is the only element with a cast shadow as identity (`0 2px 5px rgba(42,27,106,.10)`) and it is a sticker, not a card. Dialogs use the only structural shadow (`0 24px 60px rgba(21,14,62,.22)`).

### Named Rules
**The Settled-Paper Rule.** Shadows are ambient and always layered rgba-indigo, never hard-offset; the shelf is calm, state changes are the only drama.

## Shapes

The label language is crisp: 6px radius on every plate (cards, buttons, tiles, panels), 4px on small stamps (state tags, inputs, chips), and one intentional exception - the 6px die-cut sticker that prints rotated -1.4deg. Corners are never pill-shaped for controls; pills exist only for the scrollbar. Borders are hairline 1px `#d7e2df`; tinted plates may swap in their family's line color (rose line, green mix) at the same 1px weight. White plates on the mint ground; nothing floats above the shelf except the sticker and the dialog.

## Components

### Buttons
- **Shape:** 6px radius, 38px min-height.
- **Primary:** Price-Rail Indigo fill, white 700 label (`approve-button`, `button-primary`); hovers to `#1e134f` and lifts 1px with a 16px indigo shadow; disabled at 55% opacity in progress.
- **Ghost:** no fill, hairline border, Print Grey label; hover turns the border indigo.
- **Focus:** 3px outline in the state's own ink, +3px offset.

### Chips / State tags
- **Style:** 4px radius, 1px hairline, pale fill, 11px label.
- **Triangles:** settled-ok = Safe Tint + Green Ink text; neutral states = panel + Print Grey; guard-critical = Rose + Magenta Ink; degraded = Amber Tag + Amber Ink.
- **The clearance sticker (signature):** on needs-intervention rows the tag becomes a white label with a 1.5px magenta edge, 700 Magenta Ink text, rotated -1.4deg with the sticker shadow. Stands back up at ≤640px where row space is tight.

### Cards / Containers
- **Corner Style:** 6px; **Border:** 1px Hairline (tinted family lines on state plates); **Background:** Label White or the state tint; **Internal Padding:** 13-22px; **Shadow Strategy:** Settled-Paper Rule.
- No colored card edges of any kind (see The Edge Rule); the group's state shows in a small printed tab (14x9px) next to the title.

### Inputs / Fields
- **Style:** white fill, 1px hairline, 4px radius, 42px min-height; placeholder Print Grey.
- **Focus:** 2px indigo outline (never a glow), caret in Green Ink.
- **Error:** rose plate with Rose Line border and Magenta Ink text below the field.

### Navigation
- A flat white band sealed by a hard 2px Price-Rail Indigo bottom edge - the shelf's printed edge. Wordmark + product lockup left, live status (green dot) right; collapses to wordmark + status at ≤640px. No glass, no blur, no floating pill.

### The topline (signature system component)
- A full-width 5px Shelf Green rail at the very top of the page; while the feed loads, one indigo segment sweeps it (1.6s ease-in-out loop) and the rail settles flat green. Reduced motion parks it. It is the only authored motion on the surface.

## Do's and Don'ts

### Do:
- **Do** reserve `#cf348d` for act-now moments only; if a screen has no urgent store, it has no magenta.
- **Do** print green and magenta words in the deep inks (`#0e7a55`, `#a4285e`) - the raw duo only for dots, bars, sweeps, fills.
- **Do** set every live figure in Space Grotesk `tabular-nums`.
- **Do** rotate exactly one element - the clearance sticker at -1.4deg - and stand it back up at narrow widths.
- **Do** keep plate rules at 1px Hairline and radius at 6px/4px.

### Don't:
- **Don't** put a colored border (or painted stripe) thicker than 1px on any card edge; state rides backgrounds, tabs, counts, and stickers.
- **Don't** add glass, blur, or floating pills to the brand band; it is sealed flat with the 2px indigo edge.
- **Don't** introduce a second urgent color family (no orange alerts competing with the clearance magenta; amber is for degraded guards only).
- **Don't** rotate anything else or exceed -1.4deg; one sticker per row is the whole show.
- **Don't** use decorative kickers or icon+title+text dashboard cards; content lines earn their uppercase tracking only by carrying campaign/freshness truth - new screens must justify any uppercase strip by the content it prints.
