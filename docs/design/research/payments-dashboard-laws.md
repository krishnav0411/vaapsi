# Payments dashboard design research

Notes on what makes a dashboard read like a real payments product rather than
a template. **Products looked at:** Stripe Dashboard, Mercury, Ramp, Modern
Treasury, Wise (plus Fey/Revolut for cross-reference), on 2026-08-28.
**Method:** public marketing screenshots & product pages, official
design-system docs (Wise Neptune docs, Stripe Apps design docs), third-party
design teardowns with extracted CSS tokens (SaaSFrame DESIGN.md extraction,
webdesignhot Stripe DESIGN.md tokens), Modern Treasury's own
design-retrospective blog, Config 2021 Stripe design talk coverage.

---

## 1. "Money at a glance" — Overview Layout Patterns

### The universal skeleton
Every one of these products uses the same three-tier vertical structure on the home/overview page:

```
[ Tier 1: KPI band — 3–5 metric cards/tiles, full-bleed or hairline-separated ]
[ Tier 2: Chart(s) — one primary time-series, sparklines inline in KPI cards  ]
[ Tier 3: Recent-activity table — 5–10 rows, "View all →" affordance          ]
```

### Per-product hero metric (the one number the screen opens on)
| Product | Hero metric | Why (role) | Density |
|---|---|---|---|
| **Stripe** | Transaction volume + success rate + error breakdown | Developers/technical finance inspecting a payment system | Very high |
| **Mercury** | Current balance + cash trend (implied runway) | Early-stage founder who isn't a banker | Low |
| **Ramp** | Savings vs. prior period (not spend!) | Finance team whose job is controlling spend | High |
| **Modern Treasury** | Balances (near real-time) + payment ops pipeline | Finance leadership + ops; dashboard mirrors the API | Very high |
| **Wise** | Pending transfers with exact status (not balance) | Anxious sender waiting for money to land | Low–medium |

**Law:** the hero metric is the answer to the primary user's *first question*, and everything else is subordinate. A dashboard opening on "total transaction count" is designed for the product team, not the user. (Masterly framework: Role → Metric → Density → Action.)

### KPI card anatomy (consistent across all five)
- Label in small, muted text (11–13px).
- Value in large, tabular-numeral figures (24–40px range), currency symbol smaller and muted.
- Period delta: small arrow glyph (↑/↓ or triangle) + signed percentage, colored by *sentiment of the change relative to the goal* (green good / red bad — but color only the delta, never the value itself).
- Inline sparkline (~60–100px wide, single 1.5px line, no axes, no gridlines, no fill or a 4–6% alpha fill) communicating *trend shape*, not precision.
- **Charts vs. sparklines is a genuine hierarchy tradeoff** (Gummble): sparkline = trend at a glance; full chart = precision. Full interactive charts appear once per overview at most; sparklines are embedded inside KPI cards. Getting the wrong one in the wrong slot misleads the user about what question to ask.

### Recent-activity table placement
Always directly under the KPI band — transaction lists are "the workhorse UI" where users spend most of their time (Gummble). Every row is clickable through to a detail/inspection page. Stripe's Home page pairs analytics widgets with action-surface notifications (unresolved disputes, identity verifications) so the overview is never purely decorative.

### Ramp's differentiator: every chart is clickable
"Every chart is clickable. Drill from a spike to the exact transactions" (ramp.com/reporting). Drill-down from aggregate → raw rows is the interaction that makes a finance dashboard feel like a real tool rather than a poster.

---

## 2. Status / Semantic Color Systems (with exact hues where verified)

### Stripe (tokens extracted from stripe.com-derived DESIGN.md, webdesignhot 0.2)
| Token | Hex | Role |
|---|---|---|
| `success` | **#15BE53** | succeeded payments; success-text darker variant **#108C3D** |
| `success-bg` | rgba(21,190,83,0.2) | pill/tint background, 20% alpha |
| `danger` | **#EA2261** | failed payments — a *crimson/ruby*, not tomato red |
| `warning` | **#9B6829** | pending — a muted brown-amber *text* hue, never a bright yellow block |
| `accent-info` | #2874AD | informational status (settling, in transit) |
| neutral | gray text | refunded/uncaptured/canceled states are gray, not colored |
| Text | #061B31 (navy-black, AAA 17.4:1) | never pure #000 |
| Brand | #533AFD purple | interactive/CTA only — never used for semantics |

### Wise (official Neptune design system, docs.wise.design — CSS custom properties)
| Token | Hex | Role |
|---|---|---|
| `sentiment-positive` | **#2F5711** | dark olive-green; AA on white |
| `sentiment-negative` | **#A8200D** | dark brick red |
| `sentiment-warning` | **#EDC843** | soft yellow |
| `content-primary` | #0E0F0C | near-black warm ink |
| `interactive-primary` | #163300 Forest Green | links/buttons in product UI |
| `border-neutral` | rgba(14,15,12,0.12) | hairline separators |
| Brand #9FE870 Bright Green | accent only | never a status color |

### Modern Treasury
Status system mirrors payment-operations lifecycle states (Created / Pending Approval / Approved / Sent / Returned / Canceled) as **small pill labels with a leading dot**, restrained hues — the status vocabulary *is the API's* vocabulary. (Design principle: "the front-end parallels the API... as respectful as possible to the data coming in.")

### Mercury / Ramp (observed pattern, no verified hex)
- Mercury: muted green for incoming credits, neutral dark ink for debits, amber chip for pending; status is subordinate to the balance hero — almost no chroma on the home screen.
- Ramp: green for savings/under-budget, red/amber reserved for *policy violations* ("flagged transactions"), because in spend management red means "act now," not merely "money left."

Same pattern across all five products: semantic hues are all **low-to-mid saturation, AA-contrast-on-white, used in 20%-alpha tint pills or 8px dots + dark text** — never full-saturation fills, never neon. Red is reserved for actionable failure; amber for in-flight; gray for terminal-neutral (refunded/canceled); green for succeeded. Brand accent color is never reused for semantics.

---

## 3. Table Design Patterns

- **Row height:** compact 36–44px rows (Stripe/Datadog-class density); generous padding reserved for marketing tables, never data tables.
- **Typography:** amounts in **tabular figures** — Stripe's financial-table recipe: `font-feature-settings: 'tnum'`, 12px Söhne weight 300, line-height 1.33, letter-spacing −0.36px (12px caption-tabular token). Wise: Inter Medium 500/SemiBold 600 for all UI text; numerals align in columns.
- **Monospace only for identifiers:** Stripe ships SourceCodePro 12px w500 strictly for code/API IDs/charge IDs — money values are *not* monospace; they're proportional with tabular figures. (Monospace amounts read "terminal toy"; tabular figures read "ledger.")
- **Alignment:** amounts right-aligned; description left; status as a compact pill/dot+label; secondary metadata (IDs, timestamps) in muted gray at smaller size.
- **Hover:** single subtle background wash (Stripe: rgba-brand at ~5%, e.g. `brand-bg-hover: rgba(83,58,253,0.05)`), no elevation change, no shadow pop. Whole row clickable.
- **Separation:** hairline 1px borders only (`#E5EDF5` / `#D4DEE9` for Stripe; rgba(14,15,12,0.12) for Wise). No zebra striping in these products' core tables.
- **Header:** small caps or 12px muted gray, non-sticky on short lists.
- **No truncation of financial data:** Modern Treasury's explicit principle — "the dashboard doesn't round down or truncate data — information is shown as explicitly and granularly as possible." Cents always shown; round only in marketing.
- **Search/filter above the table**, persistent; saved views (Stripe's filter sets, Ramp's custom dashboards).

---

## 4. What They Deliberately AVOID

1. **Gradients in the product UI.** Stripe's famous gradients live in *marketing*, never in dashboard data surfaces. The dashboard palette is white surfaces + navy ink + hairlines.
2. **Heavy shadows.** Wise: "Minimal shadow use; elevation communicated via Background Elevated surfaces and bottom sheets" + hairline borders. Stripe's ambient shadow is rgba(23,23,23,**0.06–0.08**) — barely perceptible. Drop-shadow-on-everything is the #1 AI-demo tell.
3. **Card-soup.** Stripe's own Config 2021 case study ("Going Card-less"): they moved *away* from a dashboard made of cards to full-width sections separated by hairlines — cards are for containment of genuinely distinct objects, not layout decoration.
4. **Decorative charts.** No 3D, no gauge/donut walls, no rainbow series. One primary time-series + sparklines. Charts must be clickable/drillable (Ramp) or they're suspicion-worthy.
5. **Emoji in data UI.** Status is conveyed by pills, dots, arrows, and text — zero emoji in tables or metrics across all five products.
6. **Rounded-everything.** Stripe's tables/buttons use small radii (~4–8px); radii stay small on data surfaces. (Wise is the intentional brand exception with 16–40px radii — a deliberate brand signature, and even Wise keeps product UI mostly white and shadow-free.)
7. **Decorative trust signals.** Certification badges everywhere = category weakness signal; structural honesty (Wise shows the exact fee before commitment, statuses visible in real time) is what actually builds trust.
8. **Pure black text** — navy (#061B31) or warm ink (#0E0F0C) instead; softer global contrast, harder semantic contrast.
9. **Wrong-density copying:** "Applying Stripe's visual language without its logic" — Stripe's event-log format works because every entry links to a replicable API object; as pure decoration it's noise formatted to look sophisticated (Masterly).
10. **Removing friction from high-stakes flows** — confirmations and visible processing states on large/irreversible transfers are deliberate trust features, not UX debt.

---

## 5. Density Calibration (data points per screen)

| Product | Approx. home-screen inventory |
|---|---|
| **Stripe** | Very high: 5–7 analytics widgets (volume chart, balance, payouts) + notification banner(s) + recent-payments table (~5–10 rows; full Transactions list paginates ~50 rows). Every data point links to inspection. |
| **Mercury** | Low: 3–4 account tiles (balance + 4dp sparkline) + 8–10 recent transactions + one primary action cluster (Move/Add/Request). Nothing else. |
| **Ramp** | High: savings KPI + budget-vs-actuals bars + vendor/Team breakdowns + live transaction feed; dashboards are pre-built for the weekly questions finance asks. |
| **Modern Treasury** | Very high ("single pane of glass"): balances grid, payment pipeline by status, reconciliation views; granular amounts, no rounding. |
| **Wise** | Low–medium: 1–3 balances + active/pending transfers with exact status + one send action. Anxiety-first hierarchy, minimal chrome. |

How to calibrate: density follows user role and session frequency — daily professional ops surfaces get density as a *feature*; glance-and-leave founder/consumer surfaces get one number + one action. "Density is not a proxy for seriousness."

---

## 6. The 10 Transferable Laws

1. **Open on the role's first question.** One hero metric dominates; everything else is subordinate (Ramp=savings, Mercury=balance, Stripe=success rate, Wise=pending-transfers).
2. **KPI cards = label + tabular value + colored delta arrow + sparkline.** Sparklines for trend, full chart only when precision matters; one full chart per screen max.
3. **Recent-activity table directly below**, 5–10 rows, every row drillable to detail (Ramp: every chart clickable too).
4. **Status hues: muted, AA-contrast, tint-pill delivery.** Stripe: green #15BE53 (text #108C3D, 20% alpha bg), crimson #EA2261 for failed, brown-amber #9B6829 for pending, blue #2874AD info, gray for terminal-neutral. Wise: positive #2F5711, negative #A8200D, warning #EDC843. Never neon, never full-saturation fills.
5. **Tabular figures for every number; monospace only for IDs.** Stripe recipe: `'tnum'`, 12px, w300, lh 1.33, ls −0.36px.
6. **Ink-first palette:** navy/warm-ink text on white, hairline rgba(…,0.12) borders instead of shadows; shadows if any ≤ rgba(23,23,23,0.08).
7. **Card-less, hairline-separated sections** (Stripe went card-less at Config 2021) — no drop-shadowed box museum.
8. **Never round or truncate money** (Modern Treasury: explicit, granular, cents always).
9. **Calibrate density to the user's role**, not to how serious you want to look; don't export Stripe's developer density to consumers.
10. **Earn trust structurally, not decoratively:** visible exact fees (Wise), real-time statuses, deliberate friction on high-stakes actions, zero emoji, minimal radii on data surfaces, and no gradients outside marketing.

---

## Sources

1. Masterly — *Fintech Dashboard Design: Ramp, Mercury & Stripe Patterns* (2026-06-13) — https://www.themasterly.com/blog/fintech-dashboard-design-guide (Role-Metric-Density-Action framework, per-product hero metrics + density)
2. Modern Treasury — *Behind the Scenes: Designing Our New UI* (Duncan Graham, 2024-02-28) — https://www.moderntreasury.com/journal/behind-the-scenes-designing-our-new-ui (no rounding/truncation, single pane of glass, API-mirroring, workflow-centric principles)
3. webdesignhot — *Stripe · DESIGN.md* extracted design tokens (2026-05-02) — https://www.webdesignhot.com/design.md/stripe/ (Söhne type scale, success #15BE53 / #108C3D, danger #EA2261, warning #9B6829, info #2874AD, tabular-figure table recipe, shadow alphas, brand #533AFD)
4. SaaSFrame — *Wise Design System (Neptune) tokens* — https://www.saasframe.io/saas/wise (Inter 500/600, Wise Sans display, sentiment colors #2F5711/#A8200D/#EDC843, border rgba(14,15,12,0.12), minimal shadows, 4px spacing system, radii scale, between-cards 12px)
5. UW/UX (Angel Lin, Medium) — *Behind the Gradient: Design at Stripe* (2026-04-11) — https://uwux.medium.com/behind-the-gradient-design-at-stripe-476dcf61a51a (Connie Yang's four principles, Config 2021 "Going Card-less" dashboard case study)
6. Stripe Docs — *Web Dashboard* — https://docs.stripe.com/dashboard/basics (Home = analytics/charts + notifications like unresolved disputes; Transactions page with statuses/filters/exports)
7. Stripe Docs — *Design your app (Stripe Apps)* — https://docs.stripe.com/stripe-apps/design (intentionally limited custom styling, color-contrast accessibility bar, platform consistency)
8. Ramp — *Real Time Reporting* — https://ramp.com/reporting (every chart clickable, drill to transactions, budget-vs-actuals live, pre-built dashboards)
9. Gummble — *Fintech Dashboard UI Design: Fey, Mercury & Revolut* (2026-06-06) — https://gummble.com/blog/fintech-dashboard-ui-design (hero-number stakes, charts-vs-sparklines tradeoff, transaction lists as workhorse UI, trust as visible design element, empty states)
10. Toimi.pro — *Top 10 Fintech Website Designs 2026* (2026-04-22) — https://toimi.pro/blog/best-fintech-website-designs/ (typography as brand infrastructure: Söhne, Wise Sans; Mercury "luxury-brand aesthetic applied to startup banking"; tabular figures for financial legibility)
11. SaaSFrame — *Mercury screens library* — https://www.saasframe.io/saas/mercury (Dashboard / Transactions table / Payments / Vault / Treasury screen inventory)
12. Wise Design — https://wise.design (brand direction; docs at docs.wise.design linked from it)
