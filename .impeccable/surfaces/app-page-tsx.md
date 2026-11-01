---
version: 1
slug: "app-page-tsx"
primary_target: "app/page.tsx"
related_targets: ["app/_components/chrome.tsx","app/store/[id]/StoreDetail.tsx"]
---

# Surface brief — board page (`app/page.tsx`)

---
version: 1
slug: "app-page-tsx"
primary_target: "app/page.tsx"
related_targets: ["app/_components/chrome.tsx", "app/store/[id]/StoreDetail.tsx", "app/globals.css"]
---

**Scope.** The whole stakeholder app (board `/`, store detail `/store/[id]`, shared chrome) rebuilt as one replacement visual world - the 2026-11-01 user-initiated redesign.

**Mode & audience.** Operate. A non-technical stakeholder answers one question per store - decide now, cannot evaluate, nothing to do - and trusts each verdict's provenance. Weekly (not daily) use; office light; sometimes projected.

**Job on the surface.** Scan 85 stores in groups, land on the magenta-tagged row, read its reason and figures, walk to the why page, record approvals via the token-gated dialog, interrogate records through the per-store ask panel.

**Direction - SHELF-EDGE LABEL (pinned palette: `#2abf95`, `#2a1b6a`, `#cf348d`).**
The board behaves like the shelf a grocery data scientist owns. Each store wears a shelf-edge label: dense, hairline-ruled, structured, product truth printed verbatim in the existing glossary vocabulary. Color does named jobs and only those: `#2a1b6a` indigo carries the shell's ink (headings, price/decision zone, approve buttons, identity lines); `#2abf95` green carries the live-data system (topline sweep, live dot, "no action needed", ok-state fills) with its deepened `#0e7a55` ink for words; `#cf348d` magenta is the grocer's clearance sticker - the single "act now" polarity, marking only stores whose time is short. Signatures: the slightly die-cut magenta state sticker on needs-intervention rows (the reduced-to-clear tag), and the topline sweep as the shelf's power-on while data wakes. Neutrals (mint ground, hairlines, muted captions) keep the labels honest; no new structural chrome from the source world - controls stay standard web controls, the layout stays the gate sentence + three groups the truth requires. World voice arrives only as type, palette, density, and the sticker signature; never as costume (no ruler ticks cosplay, no receipt paper).

## Direction contract

THESIS: a stakeholder reads the board the way a grocer reads a shelf - every store a label, every label honest; the magenta clearance sticker is the one thing that says act now. Refuses the category default of dashboard cards with icon+title+text and refuses colored karaoke of all states equally; live/ok stays quiet, the clearance corner speaks once.

OWN-WORLD: pale-mint ground, white label plates with hairline rules; indigo ink and indigo decision/price zones; green reserved for live and safe fills; magenta only as the clearance sticker and its tint family. Roboto body, Space Grotesk figures at tabular width; uppercase tracked shelf-strip identity lines. Stripped of all content the page still reads: structured product labels on a shelf - state tag top-left, reason as the label's name, facts as the price field, decision as the stamped zone.

STORY: visitor scans three shelf groups, sees 6 magenta-tagged labels, reads one label's reason/figures/committed action, clicks through to the same vocabulary on the why page, records approvals, and trusts figures because provenance rides each panel.

FIRST VIEWPORT: indigo-underlined brand band (wordmark + product name + live dot); shelf-strip identity line (campaign window, data as of) above the Space Grotesk h1; gate sentence 27px figure; three state banded tiles (green/slate/magenta top bands) whose counts add to the gate figure; "Need a decision" strip with magenta tab open on stores 293/299/317, first magenta sticker visible above the fold; approve action right-aligned in indigo.

FORM: code-led rebuild of the same anatomy assigned by product truth (gate sentence, three collapsible groups, per-store labels, bulk review, token sheet, why-page panel grid, ask panel). Position: shelf-label world carries type/palette/density/sticker signature. SEED REFERENCE: seed:4684238ba66cc65c (roll assigned a jet-age ticket-wallet world; the user-pinned grocery shelf-edge label direction and the dunnhumby palette trio override it per new-work - named translation: the shelf replaces the wallet as the paper world, and the assigned paper-ephemera ritual survives only as the printed-label density and the stamped decision zone).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
