---
name: Retail Decision Intelligence Agent
description: A dunnhumby-branded store-recovery decision surface built as a carbon-copy register - every store a stamped stub, approvals stamp and nothing ever erases.
colors:
  primary: "#2a1b6a"
  brand-green: "#2abf95"
  brand-green-ink: "#0e7a55"
  brand-magenta: "#cf348d"
  brand-magenta-ink: "#a4285e"
  carbon: "#3b3f66"
  neutral-bg: "#f6f4ef"
  paper: "#fffdf8"
  panel: "#f2efe6"
  line: "#d9d3c6"
  punch: "#e3ddcd"
  muted: "#5d6167"
  ink: "#212048"
  slate: "#6f74a8"
  no-signal: "#edece4"
  rose: "#f8e9f1"
  rose-line: "#ecc9dd"
  amber: "#f5e9b8"
  amber-ink: "#8a5a12"
typography:
  display:
    fontFamily: "Space Grotesk, Roboto, sans-serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.045em"
  title:
    fontFamily: "Space Grotesk, Roboto, sans-serif"
    fontSize: "19px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.03em"
  body:
    fontFamily: "Roboto, system-ui, sans-serif"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.55
  mono:
    fontFamily: "IBM Plex Mono, monospace"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.02em"
  label:
    fontFamily: "IBM Plex Mono, monospace"
    fontSize: "10px"
    fontWeight: 600
    letterSpacing: "0.16em"
    lineHeight: 1.5
rounded:
  sm: "0px"
  md: "2px"
spacing:
  sm: "8px"
  md: "12px"
  lg: "18px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    padding: "0 22px"
  button-primary-hover:
    backgroundColor: "#1e134f"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
  card:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.sm}"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
---

# Design System: Retail Decision Intelligence Agent

## Overview

**Creative North Star: "The Stub Register"**

The product is a carbon-copy register of the engine's recommendations, not a dashboard. Every store is a stub with a perforated edge; the engine's reason prints as the stub's ruled fields; every verdict prints as a stamp. The magenta ACT NOW stamp - the register's one urgent polarity - marks every store that needs a human decision; approved records take the green stamp, and nothing is ever erased, it cancels. The clerk reads top to bottom like a form: masthead band, gate sentence, ruled count cells, stamped forms, the docket.

The palette is the pinned dunnhumby trio (`#2abf95`, `#2a1b6a`, `#cf348d`) doing named jobs on paper: indigo owns the full-bleed masthead band and register ink, green owns only the live system, magenta exists solely to stamp act-now. Depth is stock sitting on stock - no shadows anywhere on the shelf - and nothing eases: state changes step. This is the second world the user commissioned in one day; it replaced the quieter shelf-label world the user found "a little bit same."

**Key Characteristics:**
- One signature moment per stub: the double-ring magenta ACT NOW stamp, pressed askew (-2deg), on every needs-intervention record.
- Full-bleed `#2a1b6a` masthead band with the white wordmark (single-color inversion) and the green LED rail above it.
- Perforation punches (`6px dotted`) open every store stub; hairlines (`#d9d3c6`) rule every field.
- Carbon figures print in IBM Plex Mono; Space Grotesk prints nameplates; Roboto carries fine print.
- Sharp corners (0-2px), flat stock-on-stock depth, stepped motion that never eases.

## Colors

Warm coupon stock replaces the cool editorial ground; every color still works as a named form field.

### Primary
- **Register Indigo** (`#2a1b6a`): the masthead band, headings, store IDs, decision print, primary tabs; the deep keyline `#1e134f` seats it. 14.4:1 on paper.

### Secondary
- **Shelf Green** (`#2abf95`): the LED rail, live dots, verdict marks for settled states - never words.
- **Green Ink** (`#0e7a55`): green words - LIVE legends, CLEAR counts, verification. 5.1:1 on paper.
- **Stamp Magenta** (`#cf348d`): the ACT NOW stamp, its form frame, the act-now count. Non-text or stamped text only.
- **Stamp Ink** (`#a4285e`): magenta family words, 6.2:1 on paper; all errata slips.

### Tertiary
- **Slate** (`#6f74a8`): no-signal tab marks and review fills (`#edece4`) where no verdict exists.

### Neutral
- **Coupon Stock** (`#f6f4ef`): the ground.
- **Stub Paper** (`#fffdf8`): every plate; the docket.
- **Neutral Fill** (`#f2efe6`): review cells, skeleton slips.
- **Hairline** (`#d9d3c6`) / **Punch** (`#e3ddcd`): rules and perforations.
- **Carbon** (`#3b3f66`): mono figure ink, 8.2:1 on paper.
- **Print Grey** (`#5d6167`): captions; **Body Ink** (`#212048`): prose.
- **Amber Tag** (`#f5e9b8`) + **Amber Ink** (`#8a5a12`): degraded guard chips.

### Named Rules
**The Stamp Rule.** One stamp per stub, and the state it prints never vanishes - approval stamps GREEN, nothing erases, it cancels. `#cf348d` prints only where the meaning is "act now"; a register with no urgent store holds no magenta anywhere.

**The Carbon Rule.** All live figures - counts, percentages, windows, store IDs in boxes - print in IBM Plex Mono carbon ink (`#3b3f66`), always tabular. Space Grotesk is reserved for nameplates; Roboto carries prose.

**The Stock-on-Stock Rule.** No shadows anywhere on the shelf. Depth is paper ("#fffdf8") on stock ("#f6f4ef") separated by hairlines, with perforations and ruled fields - the docket floats on dimmed ink, the only exception.

**The Only-Frame Rule.** Stubs always wear 1px hairlines. Exactly one element per surface may carry a 2px colored frame: the ACTION REQUIRED form head (its stamp frame). No colored border ever rides a single side of a card.

**The Deep-Ink Rule.** Words print only in deep inks (`#a4285e`, `#0e7a55`, carbon `#3b3f66`, indigo `#2a1b6a`); the raw trio is reserved for dots, rails, fills, and stamp bodies.

## Typography

**Nameplate Font:** Space Grotesk (Roboto fallback) - dunnhumby.com body face rule holds; `impeccable-disable` documented for the Grotesk pairing.
**Body Font:** Roboto (system-ui fallback).
**Register Font:** IBM Plex Mono - carbon figures, stamps, tracked legends.

**Character:** Space Grotesk prints the big verdicts ("Store 289", problem counts); Plex Mono does the register's machine voice - everything tabular, stamped, or uppercase-tracked; Roboto is the clerk's fine print.

### Hierarchy
- **Display** (700, 30px/1.05, -.045em): page nameplates; detail hero scales to `clamp(30px, 4.5vw, 46px)`.
- **Title** (700, 19-21px): group names, dialog titles, recommended action.
- **Mono figure** (500-600, 15-34px, tabular): the 53 gate figure, count cells 6/47/32, health/recovery values, store-ID boxes.
- **Label** (600-700, 9.5-11px, +0.12em to +0.22em, uppercase): field labels (HEALTH, RECOVERY), zone kickers (DECISION NEEDED), the header identity strip (campaign window + data freshness - a content line, not decoration).
- **Body** (400, 13-15px/1.55): reasons, narrative; ~80ch cap.

### Named Rules
**The Tabular Ledger Rule.** The board re-renders every 60s; every number sits in mono tabular slots so re-renders never jitter or reflow rows. State text lives in fixed cells that change in place (raised from the transit challenger).

## Layout

One centered column (1160px, 32px gutters) printed on the stock ground. Order: full-bleed masthead band → header strip (identity line left, freshness right, 2px rule) → gate record with the three ruled count cells → three register forms (16-18px apart) → perforated stubs (10px apart) → footer. Detail page prints two sheets at 1.25fr/1fr (why / evidence) over the advisory memo. Breakpoints: 900px stacks the sheets; 640px is the clerk's pocket pass (square stamps, full-width tabs, mono labels shrink to 9px, the docket docks bottom); 380px drops decision prints to one column.

## Elevation & Depth

None. The register is flat paper: no resting shadows, no hover shadows, no blurred glows. Depth comes from layering brighter paper on the stock ground, hairline rules, and perforation punches. The approval docket is the sole floating object - it dims the register behind ink-dark glass (`rgba(30,19,79,.55)`) without casting a shadow of its own.

### Named Rules
**The No-Shadow Ledger Rule.** If a state needs emphasis, it gets a stamp or a frame - never a shadow, never a lift.

## Shapes

Sharp is the register's form language: 0px on plates, tabs and count cells; 2px maximum on small chips and stamp inner rings. Borders are 1px hairlines everywhere except the two stamped exceptions (the ACT form head's 2px frame and the stamps' 2px rings with inset paper gaps that read as rubber-stamp double-rings). Perforation (`6px dotted #e3ddcd`) is the one recurring silhouette - every stub is torn from the book.

## Components

### Buttons (register tabs)
- **Shape:** square corners, 38px min-height, 2px border.
- **Primary:** Register Indigo fill, paper-white mono 700 uppercase +0.16em label ("APPROVE"); presses to `#1e134f`.
- **Ghost:** paper fill, 1px carbon outline, Print Grey mono label ("CANCEL"); hover turns indigo.
- **Dashed actions** ("SHOW ALL 6 STORES", "BULK REVIEW"): 1px dashed carbon frame - a perforation command, not a primary.
- **Focus:** 2-3px solid outline in carbon/indigo ink, instant (steps).

### Stamps (state tags - the signature component)
- **ACT NOW stamp:** paper fill, 2px `#cf348d` ring with inset paper gap + inner ring (rubber-stamp double edge), Stamp Ink uppercase mono text, rotated -2deg.
- **Settled stamp:** same ring in Green Ink, held straight.
- **Quiet tag:** 1px carbon outline, paper fill, carbon mono uppercase - neutral states whisper.
- **Held-back outline:** dashed carbon outline for pending/disabled record states (raised from the darkroom challenger).
- Stamps stand back up at ≤640px.

### Cards / Containers
- **Paper plates** (`#fffdf8`) on stock, 1px hairline borders, square corners; store stubs add the perforated left edge and a 28px-perforation padding; the review sheet is a tinted folder (`#edece4`).

### Fields
- **Inputs:** paper fill, 1px carbon frame, mono text, no radius; indigo 2px focus outline; mono placeholder in Print Grey. Caret green.
- **Errors:** dashed magenta frame, rose fill, Stamp Ink text ("errata slip").

### Navigation
- The masthead: full-bleed indigo band, white wordmark (single-color `brightness(0) invert(1)` inversion of the brand asset), paper-white product lockup, LED strip (green) above, 4px `#1e134f` keyline below. Collapses to wordmark + status at ≤640px. No glass, no pills.

### The LED rail (signature system component)
- 4px green rail across the very top. While the feed wakes, a register block STEPS across it (10 frames, 1.4s, `steps(10)`) - never a smooth sweep - and settles flat green. Reduced motion parks it.

## Do's and Don'ts

### Do:
- **Do** stamp verdicts - one stamp per stub, stamped in the state's ink (`#a4285e`/`#0e7a55`), upright on mobile.
- **Do** print every live figure in IBM Plex Mono with tabular numerals (`font-variant-numeric: tabular-nums`).
- **Do** keep the ground warm (`#f6f4ef`) and plates paper (`#fffdf8`); depth = stock-on-stock flat.
- **Do** carry the trio's brand jobs exactly: indigo band + ink, green live system, magenta stamps only.

### Don't:
- **Don't** cast a shadow - emphasis comes from stamps, frames, and paper contrast.
- **Don't** ease anything: motion steps (`steps()`); no fades longer than the LED step.
- **Don't** put colored borders >1px on stubs or panels; the ACTION REQUIRED frame and stamp rings are the only 2px color in the system.
- **Don't** erase history: approvals add a green stamp; error slips print and stay.
- **Don't** invent serial numbers or fictional form copy - the register voice prints product truth only; any uppercase strip must carry campaign/freshness/field content to exist.
