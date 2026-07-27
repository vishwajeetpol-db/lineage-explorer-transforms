# BrickTrace — Capability Matrix Visual (Nano Banana prompt)

> Copy the prompt below into **Nano Banana** (Google Gemini 2.5 Flash Image) to
> generate a presentable capability-matrix infographic for BrickTrace. Two
> variants are included: a **full infographic** and a **compact slide tile grid**.
> Content mirrors [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md) (v2.5.5).

---

## Prompt A — Full capability-matrix infographic (recommended)

```text
Create a sleek, modern product capability-matrix infographic for a data-lineage
tool called "BrickTrace". 16:9 landscape, high resolution, presentation-ready.

STYLE:
- Dark theme. Background near-black charcoal (#0A0A0F) with a subtle radial glow.
- Accent gradient: Databricks-style orange-red (#FF4520 → #FF3621). Secondary
  accents: indigo (#6366F1), sky blue, emerald, violet, amber — one hue per group.
- Clean sans-serif typography (like Inter/SF Pro). Generous spacing. Flat design
  with soft rounded cards (16px radius), thin 1px light borders, gentle shadows.
- Small line icons inside each capability card. No photos, no clip-art.

HEADER:
- Top-left: a hexagonal "lineage network" logo mark — six small circular nodes
  around a central stacked-layers (data) glyph, connected by glowing orange lines.
- Title "BrickTrace" (Brick in white, Trace in orange-red). Tagline below in grey:
  "Unified Unity Catalog lineage — table, column & transformation, end to end."
- Top-right small pill badge: "v2.5.5".

LAYOUT — five titled sections as grouped card clusters:

1) CORE LINEAGE (indigo accent): cards for
   "Table & Column Lineage", "Expression-level Transformation",
   "Runtime Plan Capture", "Delta Sharing Overlay", "Serverless Cost on Nodes".

2) ANALYSIS SUITE (orange-red accent): cards for
   "Impact Analysis", "Root Cause", "Governance & Classification",
   "Access & Security", "ML Model Lineage", "LLM Column Transforms".

3) PLATFORM & UX (sky-blue accent): cards for
   "Interactive Graph (ReactFlow)", "Search & Discovery",
   "Light / Dark Theme", "Excel Export", "Admin Dashboard".

4) OPEN & SCALE (emerald accent): cards for
   "OpenLineage Import/Export", "Multi-Platform Bridge",
   "Versioned Snapshots", "Distributed Cache & Pagination", "Notifications & Webhooks".

5) TRUST (violet accent): a single wide banner card:
   "Metadata-only — reads UC system tables, never your row data."

FOOTER: thin strip reading
"19 of 20 scorecard capabilities complete • Deployed as a Databricks App • FastAPI + React".

Each capability = a small rounded card with a line icon on the left, a bold
one-line label, and a tiny grey sub-label. Keep text crisp and legible; do not
invent extra labels beyond those listed. Balanced grid, aligned columns,
professional and uncluttered.
```

---

## Prompt B — Compact slide tile grid (for a single deck slide)

```text
Design a clean 16:9 presentation slide titled "BrickTrace — Capabilities".
Dark charcoal background (#0A0A0F), orange-red accent (#FF4520), modern flat
style with rounded cards and thin borders. Hexagonal data-lineage logo mark
top-left next to the title.

Show a neat 4-column x 5-row grid of 20 small capability tiles, each with a
minimal line icon and a short bold label:
Table Lineage, Column Lineage, Transformation Logic, Runtime Plan Capture,
Impact Analysis, Root Cause, Governance, Access & Security, ML Lineage,
LLM Transforms, Data Quality, Business Glossary, Search & Discovery,
Versioned Snapshots, OpenLineage, Multi-Platform, Notifications, Observability,
Scalability, Interactive Graph.

Color-code tiles subtly by theme (lineage=indigo, analysis=orange-red,
platform=sky, quality=emerald). One-word category legend at the bottom.
Elegant, minimal, boardroom-ready. Crisp legible text, no filler.
```

---

## Tips for best results

- Nano Banana renders **short label text** well but can garble long paragraphs —
  keep card labels to a few words (the prompts above already do).
- If text comes out misspelled, regenerate or add: *"ensure all text is spelled
  exactly as written and clearly legible."*
- To match the app exactly, attach the real logo (`frontend/public/bricktrace-logo.logo`,
  a PNG) as a reference image and say *"use this logo, top-left."*
- For a light-mode version, swap the background line to:
  *"Light theme: off-white background (#F5F7FA), dark slate text, same orange-red accent."*
- Aspect ratio: request **16:9** for slides, **1:1** for a Slack/README hero image,
  **4:5** for a portrait one-pager.
