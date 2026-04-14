# Marrow — Brand Guidelines

**Version:** 1.0 | **Date:** 2026-04-14 | **Owner:** Ali Naserifar

> The single source of truth for what Marrow is called, how it speaks, how it looks, and what it refuses to be. If a decision isn't here, default to the principle that wins: **clinical precision over marketing polish**.

---

## 1. The Name

### Marrow

**Pronunciation:** /ˈmæroʊ/ — *MA-row*. Two syllables. Rhymes with "narrow" and "sparrow."

**What it means:** Marrow is the vital substance inside the bone — the dense, nutritive core that the rest of the structure exists to protect and carry. To "get to the marrow" of something is to reach its essential, load-bearing center after stripping away everything that isn't.

**Why we chose it:**
- **Literal product fidelity.** The product extracts the dense conceptual core of a 300-page book and discards nothing essential. Marrow is the core; the rest is bone you can leave behind.
- **Anti-summary positioning.** "Summary" implies loss. "Marrow" implies extraction of the part that was always the point.
- **One word, two syllables, memorable.** No backronym, no compound noun, no SaaS-y suffix.
- **Tactile and physical.** In a category dominated by abstract names (Notion, Mem, Glean, Obsidian), Marrow is biological and concrete.

**What it is NOT:**
- Not an acronym. Do not write `MARROW` or invent a backronym like "Multi-Agent Reading & Reduction Output Workbench." If anyone asks what it stands for, the answer is "marrow."
- Not stylized. Always write **Marrow**, never `marrow`, `MARROW`, `M4RR0W`, `Marrow.ai`, or `Marrow™`. The CLI binary is lowercase (`marrow`) because shells are case-sensitive — that is the only exception.
- Not "the Marrow app." It is "Marrow." Drop the article.

### Tagline

> **Read the marrow.**

Three words. No punctuation. Imperative mood. Plays on "read the room" without explaining the joke. Use it on the README, the website, and nowhere else. Do not extend it ("Read the marrow of any book," "Read the marrow, skip the bone") — the line works because it stops where it stops.

### Optional sub-tagline (for longer surfaces only)

> **Lossless book briefs for people who refuse to skim.**

Use this only when the three-word tagline needs a single line of context (e.g., social card, conference slide). Never both at once.

---

## 2. Brand Essence

### One-liner
Marrow turns a 300-page book into a 50-page conceptual brief that loses nothing important and proves it.

### Mission
Make deep reading affordable in time without making it shallow in content.

### Vision
A world where the bottleneck on knowledge isn't reading speed — it's choosing what to read.

### Values

| Value | What it means in practice |
|---|---|
| **Lossless** | If a fact survives in the source, it survives in the brief. We measure this. We publish the number. |
| **Traceable** | Every sentence in a Marrow brief jumps to its exact source paragraph in one click. No claim is unfalsifiable. |
| **Local** | Your books stay on your machine. Marrow runs offline by default. |
| **Honest about cost** | We tell you the dollar cost per book, the runtime, and the failure modes before you start. |
| **Anti-magic** | Every stage is inspectable. Every decision is logged. Nothing is "an LLM figures it out." |

---

## 3. Voice & Tone

Marrow speaks like a senior engineer who has read the papers and has nothing to sell. The voice is **clinical, precise, and quietly confident**. It never uses three words where one will do, never claims a benchmark it can't cite, and never reaches for emotion when a number is available.

### Voice principles

1. **Verbs over adjectives.** "Compresses 300 pages to 50" beats "powerful AI compression."
2. **Numbers over claims.** "92% leaf-recall" beats "highly accurate."
3. **Concrete over abstract.** "Jumps to the source paragraph" beats "delivers transparency."
4. **Short sentences.** A reader should never lose the thread halfway through a clause.
5. **No first-person plural marketing.** "We believe knowledge is..." is forbidden. State the thing.

### Tone modulation by surface

| Surface | Register |
|---|---|
| README | Technical, dense, link-heavy |
| Documentation | Clinical, second-person ("you run", not "the user runs") |
| CLI output | Terse, factual, color-coded by status |
| Error messages | Direct, actionable, no apology theater |
| Release notes | Bulleted, past tense, link to commits |
| Social posts | One observation per post. No threads about "the future of reading." |

### Voice examples

| ✅ Use | ❌ Avoid |
|---|---|
| "Marrow compresses a 300-page book to ~50 pages with 92% leaf-recall." | "Marrow harnesses cutting-edge AI to revolutionize how you experience books." |
| "Cost: ~$4 per book." | "Affordable, scalable AI summarization." |
| "Run `marrow run book.pdf`." | "Just point Marrow at any document and watch the magic happen." |
| "The pipeline failed at stage 05. Resume with `marrow run --resume`." | "Oops! Something went wrong. We're working on it." |
| "Every sentence cites its source chunk by UUID." | "Marrow provides industry-leading source attribution." |
| "Lossless, by measurement." | "Truly lossless, we promise." |

### Forbidden phrases

The following words and phrases never appear in Marrow's writing — not in marketing, not in docs, not in commit messages:

- "AI-powered," "AI-driven," "powered by AI"
- "Revolutionary," "game-changing," "next-generation," "cutting-edge"
- "Seamless," "effortless," "frictionless," "magical"
- "Empower," "unlock," "unleash," "transform"
- "Harness," "leverage" (as a verb)
- "Best-in-class," "world-class," "industry-leading"
- "Just," "simply" (as in "just point Marrow at...")
- "Reimagine," "rethink," "redefine"
- "Solution" (when "tool" or "thing" works)
- Any sentence that begins with "In today's fast-paced world."

If a draft contains any of these, rewrite the draft before shipping it.

---

## 4. Logo & Wordmark

### Wordmark construction

The Marrow wordmark is the word **Marrow** set in **Spectral SemiBold** at a tracking of -10 (tighter than default). The capital M is the only uppercase letter. The wordmark is always horizontal and never set on a curve, never outlined, never given a drop shadow, and never recolored from the approved palette.

```
Marrow
^^^^^^
Spectral SemiBold, tracking -10, color = Ink (#0F0F0F) on light, Bone (#F5EFE3) on dark
```

### Symbol / icon

The Marrow icon is a single solid circle inside a thin-stroke circular ring — a literal cross-section of bone. The inner circle is **Marrow Red**; the outer ring is **Ink** on light backgrounds and **Bone** on dark.

```
   ┌─────────────┐
   │      ___    │
   │     /   \   │      Inner disk (filled): Marrow Red #7A1F1F
   │    | ●●● |  │      Outer ring (stroke): Ink or Bone, 8% of icon width
   │     \___/   │      Inner disk = 60% of outer diameter
   │             │      No gradient. No glow. No drop shadow.
   └─────────────┘
```

The icon is **always** square. Minimum render size 16×16 px. Maximum size unbounded. The inner disk must remain clearly visible at 16×16 — if it isn't, increase the disk-to-ring ratio.

### Lockup rules

- **Symbol + wordmark** is the default lockup. Symbol on the left, wordmark on the right, vertically centered, with one icon-height of clear space between them.
- **Symbol alone** is allowed for app icons, favicons, and avatars.
- **Wordmark alone** is allowed inside body text and in places too narrow for the lockup.
- **Never** combine the symbol with any other visual element (no nested icons, no text inside the disk, no extra orbits).

### Clear space

Reserve at least one icon-height of empty space on every side of the lockup. Nothing — no other logos, no text, no decorative borders — enters this zone.

### What you may not do

- Don't recolor the inner disk to anything but Marrow Red.
- Don't separate the disk from the ring.
- Don't animate the icon "pulsing" or "throbbing."
- Don't add a tagline inside the lockup.
- Don't use the icon as a bullet point or list marker.

---

## 5. Color System

### Primary palette

| Name | Hex | RGB | Role |
|---|---|---|---|
| **Marrow Red** | `#7A1F1F` | 122, 31, 31 | Brand accent. The single saturated color in the system. Use sparingly: logo disk, primary CTA, key data point in a chart. Never for body text. |
| **Bone** | `#F5EFE3` | 245, 239, 227 | Light surface. The "paper" color. Backgrounds, light-mode pages, slide decks. |
| **Ink** | `#0F0F0F` | 15, 15, 15 | Primary text. Near-black, never pure black. Use for body type, headings, the wordmark on light backgrounds. |
| **Ash** | `#6B6F76` | 107, 111, 118 | Secondary text. Captions, metadata, timestamps, the words that exist to support other words. |

### Status palette

| Name | Hex | RGB | Role |
|---|---|---|---|
| **Lichen** | `#5C7060` | 92, 112, 96 | Success / PASS verdict. Muted sage, never bright green. Used in the CLI summary table when a brief passes the lossless gate. |
| **Ember** | `#D9622C` | 217, 98, 44 | Warning. Used for coverage audit warnings, budget approaching cap, validation iteration counter. |
| **Rust** | `#8B3A1F` | 139, 58, 31 | Failure / FAIL verdict. Distinct enough from Marrow Red to read as "error" not "brand," but in the same family. Used only for hard failures. |

### Neutral support palette

| Name | Hex | RGB | Role |
|---|---|---|---|
| **Vellum** | `#FAF7EF` | 250, 247, 239 | Paler tint of Bone. Card backgrounds on Bone surfaces, table-row stripes. |
| **Slate** | `#2A2D33` | 42, 45, 51 | Dark surface. Dark-mode background, terminal background in screenshots. |
| **Mist** | `#C9CCD1` | 201, 204, 209 | Border lines, dividers, table grid lines. Never used for type. |

### Color rules

1. **Marrow Red is the only saturated color on any surface.** If you need a second accent, use Ember — and only if Ember is communicating warning state. Never two saturated colors competing for attention.
2. **Body type is always Ink on light backgrounds and Bone on dark backgrounds.** Never set body type in Marrow Red.
3. **Lichen, Ember, and Rust are reserved for status communication.** Do not use them decoratively. A green check is allowed; a green section header is not.
4. **Backgrounds are Bone (light mode) or Slate (dark mode).** No gradients. No pattern fills. No vintage paper textures.
5. **Charts use the full palette in a fixed order:** Marrow Red → Lichen → Ember → Ash → Slate. Never invent new colors for new data series; cycle the palette and rely on labels.

### Accessibility

| Pair | Contrast | Verdict |
|---|---|---|
| Ink on Bone | 16.4:1 | AAA |
| Bone on Slate | 14.2:1 | AAA |
| Marrow Red on Bone | 8.7:1 | AAA |
| Ash on Bone | 5.1:1 | AA |
| Lichen on Bone | 5.4:1 | AA |
| Ember on Slate | 5.8:1 | AA |
| Rust on Bone | 7.2:1 | AAA |

All approved pairings are at least WCAG AA. Marrow text never falls below AA. If a designer proposes a new pair, run the contrast check before approving.

---

## 6. Typography

### Type stack

| Role | Family | Fallbacks | License | Where to get it |
|---|---|---|---|---|
| **Display / Headings** | Spectral | Iowan Old Style, Charter, Georgia, serif | OFL (free) | Google Fonts |
| **Body / UI** | Inter | -apple-system, Segoe UI, Helvetica, Arial, sans-serif | OFL (free) | Google Fonts / rsms.me/inter |
| **Monospace / CLI / Code** | JetBrains Mono | Menlo, Consolas, "Courier New", monospace | OFL (free) | jetbrains.com/lp/mono |

All three families are free, open-source, and broadly available. Marrow never depends on a paid font for its primary identity.

### Why Spectral

Spectral is a contemporary serif designed by Production Type for Google. It has the literary feel of an old-book serif but reads cleanly at small sizes on screens. It signals "this product is about books" without descending into Garamond pastiche.

### Why Inter

Inter is the de facto standard for technical product UI. It is exceptionally legible at every size, has a strong number-glyph set (important — Marrow displays numbers constantly), and is unobtrusive enough to let Spectral and Marrow Red carry the personality.

### Why JetBrains Mono

JetBrains Mono is the developer-native monospace. It has visual ligatures off by default (we keep them off — explicit is better than implicit), and its character density makes long terminal output readable.

### Type scale (web / docs)

| Level | Family | Weight | Size | Line height | Tracking |
|---|---|---|---|---|---|
| Display | Spectral | SemiBold | 56 px | 1.1 | -2% |
| H1 | Spectral | SemiBold | 40 px | 1.15 | -1% |
| H2 | Spectral | Medium | 28 px | 1.2 | 0 |
| H3 | Inter | SemiBold | 20 px | 1.3 | 0 |
| H4 | Inter | SemiBold | 16 px | 1.4 | 0 |
| Body | Inter | Regular | 16 px | 1.55 | 0 |
| Small | Inter | Regular | 14 px | 1.5 | 0 |
| Caption | Inter | Medium | 12 px | 1.4 | +2% |
| Mono | JetBrains Mono | Regular | 14 px | 1.5 | 0 |

### Type rules

1. **Headings are Spectral. Body is Inter. Code is JetBrains Mono.** Do not mix.
2. **Never set body in italic.** Italic exists for emphasis inside body, not as a body style.
3. **Numbers in tables and the CLI are always tabular.** Use Inter's tabular figures (`font-feature-settings: "tnum"`) so columns align.
4. **No more than two weights per surface.** Regular + SemiBold is the default. Light is forbidden — it always looks fragile on screens.
5. **No all-caps headings.** Sentence case for everything except the wordmark itself.

---

## 7. Iconography

Marrow uses a single icon set: **Lucide** (https://lucide.dev), Apache 2.0. The line weight is uniform at 1.5px. The icons are always rendered in **Ink** on light backgrounds and **Bone** on dark, with one exception: the brand icon, which uses Marrow Red as specified above.

**Rules:**
- One icon set, project-wide. Never mix Lucide with Heroicons or Phosphor.
- Icons accompany text labels by default. Icon-only buttons exist only when space is genuinely scarce (mobile, toolbars).
- Stage icons (used in the CLI summary table) always pair with their stage number. Never use an icon as the sole identifier of a stage.

---

## 8. The Terminal Is a Brand Surface

Marrow is a CLI tool. The terminal output is the most-seen brand surface, more than any landing page will ever be. Treat it as such.

### CLI output rules

1. **Use Rich for all output.** Plain `print()` is forbidden in user-facing code paths.
2. **Status colors map to the status palette** — Lichen for success, Ember for warning, Rust for failure, Ash for neutral metadata.
3. **The wordmark renders in ASCII** at the top of every `marrow run` invocation:

```
╭──────────────────────────────╮
│  ●   marrow                   │
│      read the marrow          │
╰──────────────────────────────╯
```

4. **Stage banners are Spectral-flavored** in their typographic feel: a single-line title in cyan-tinted Bone, a one-line subtitle in Ash, a horizontal rule below.

```
─── stage 03 — graphrag indexing ──────────────────────────────
    extracting entities, building communities, auditing coverage
```

5. **Final summary tables use tabular alignment.** Stage column left-aligned, duration and cost right-aligned, status column color-coded.

```
  Stage             Duration    Cost     Status
  ─────────────────────────────────────────────
  01 ingest          4m 12s    $0.00    ✓ ok
  02 chunk           6m 03s    $0.00    ✓ ok
  03 graph          12m 47s    $1.31    ✓ ok
  04 claims         18m 22s    $0.00    ✓ ok
  05 synthesize     19m 51s    $1.84    ✓ ok
  05b validate      14m 33s    $0.42    ⚠ 2 iters
  06a evaluate      11m 08s    $0.31    ✓ pass
  06b export         0m 12s    $0.00    ✓ ok
  ─────────────────────────────────────────────
  Total            87m 08s    $3.88    ✓ pass
```

6. **No emoji in CLI output** except the four allowed status glyphs: `✓` (success), `⚠` (warning), `✗` (failure), `●` (in-progress). These are Unicode, not emoji.

---

## 9. Naming Conventions

### CLI binary
The CLI binary is always lowercase: `marrow`. Subcommands are also lowercase, single words where possible: `marrow run`, `marrow batch`, `marrow status`, `marrow clean`, `marrow ask`, `marrow config`.

### File outputs
Generated Obsidian files follow this exact pattern:
- `<book-slug>_Source.md`
- `<book-slug>_Brief.md`
- `<book-slug>_Evaluation.md`

The book slug is `kebab-case`, ASCII only, derived from the book title via slug-generation. Example: `thinking-fast-and-slow_Brief.md`.

### Working directory
`runs/<book-slug>/` — never `output/`, never `data/`, never `marrow_runs/`. Always `runs/`.

### Config files
`configs/default.yaml`, `configs/cheap.yaml`, `configs/premium.yaml`. Singular `config` is reserved for the resolved object inside Python; the directory is plural.

### Releases
Semantic versioning, prefixed with `v`. Codenames are bones: `v1.0 — femur`, `v1.1 — sternum`, `v2.0 — vertebra`. The codename is never user-facing — it lives in CHANGELOG.md and release notes only.

### Repository
The canonical repo name is `marrow`. The Python package is `marrow`. The PyPI distribution is `marrow-cli` (because `marrow` is taken on PyPI by an older project).

---

## 10. Don'ts (the short list)

- **Don't call it an AI tool.** It uses LLMs. So does grep with semantic search. Lead with what it does, not what's inside it.
- **Don't promise lossless without measuring.** Every claim of "lossless" must link to a HAMLET leaf-recall number on a real book.
- **Don't add a "powered by" badge to anything.** Marrow is not powered by anything. Marrow does the thing.
- **Don't put screenshots of the brief in marketing.** The brief is the user's intellectual property — show the CLI, the architecture diagram, the cost ledger, never a real brief.
- **Don't gate features behind a hosted account.** Marrow is local-first and stays that way. If a hosted version ever exists, it is a separate product with a different name.
- **Don't decorate.** No background patterns, no illustrations of brains and books, no isometric line drawings of graphs. The icon and the wordmark and the ledger numbers are enough.

---

## 11. Asset checklist

Anything Marrow ships with should be available in these formats:

| Asset | Light | Dark | SVG | PNG @1x | PNG @2x | Min size |
|---|---|---|---|---|---|---|
| Wordmark | ✓ | ✓ | ✓ | ✓ | ✓ | 80px wide |
| Icon | ✓ | ✓ | ✓ | ✓ | ✓ | 16×16 |
| Lockup | ✓ | ✓ | ✓ | ✓ | ✓ | 120px wide |
| Favicon | ✓ | — | ✓ | — | — | 16×16, 32×32 |
| App icon | ✓ | — | ✓ | — | — | 512×512 |
| Social card | ✓ | ✓ | — | ✓ | ✓ | 1200×630 |
| README header | ✓ | ✓ | — | ✓ | ✓ | 1280×320 |

All assets live under `brand/` in the main repo. Light variants live in `brand/light/`, dark in `brand/dark/`, source SVGs in `brand/src/`. Never edit a PNG directly — re-export from the SVG.

---

## 12. Pre-launch checklist

Before any new surface (page, post, screenshot, slide, talk) goes public, run through this list:

- [ ] Wordmark is **Marrow** — capital M only, never stylized.
- [ ] Tagline is **Read the marrow.** — exactly three words, no punctuation other than the period.
- [ ] No forbidden phrases (§3) appear anywhere.
- [ ] Marrow Red is the only saturated color (or, if status colors are present, they're communicating actual status).
- [ ] Body type is Inter Regular at 16 px or larger. Headings are Spectral.
- [ ] Every numerical claim links to a measured source.
- [ ] No "AI-powered," no "revolutionary," no apology theater.
- [ ] Contrast ratios verified.
- [ ] Logo has at least one icon-height of clear space.
- [ ] If it's a CLI screenshot, the terminal background is Slate, the wordmark banner is at the top, and the status colors map correctly.

If a single box is unchecked, the surface doesn't ship.

---

## 13. Governance

This document is the brand. It supersedes any prior conversation, slide, or sketch. Changes require an explicit version bump and a changelog entry below.

### Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-04-14 | Initial brand: name, palette, type, voice, CLI, governance |

---

**End of brand guidelines.** When in doubt: clinical precision, measured claims, no decoration. Read the marrow.
