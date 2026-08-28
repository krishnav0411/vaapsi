# Razorpay Blade design notes

Working notes on Razorpay's design system, used when building the React
dashboard. Blade is their open-source system (github.com/razorpay/blade,
MIT, docs at blade.razorpay.com), and these values were read directly from
the token sources in the published package (v12.121.0) plus their Storybook.

One thing worth flagging for anyone assuming Razorpay = purple: their
marketing brand leans purple, but the product UI in Blade's default theme is
blue-led. The primary is azure #1364F1 and purple (orchid #8F62F9) only
appears in data-viz. Since this dashboard imitates the product, not the
logo, it uses azure.

## Colors used here

- Primary `#1364F1` (azure.500), hover `#0E54CD` (azure.600), tint
  backgrounds `#EAF1FE`, focused/disabled borders `#D4E3FD`
- Canvas `#F7F7F7`, cards `#FFFFFF`, borders `#DEE1E3` / `#C8CDD0` /
  hover `#B0B6BA`
- Text `#050505`, subtle `#292F32`, muted `#616D75`, disabled `#C3C5C7`
- Row hover `#FAFBFB`, stronger `#F5F6F7`
- Status pills, background/text:
  - positive (paid, verified): `#E8F5EE` / `#00753B`
  - negative (failed, dlq): `#FBEBEA` / `#D01E11`
  - notice (pending, awaited): `#FCF0E8` / `#C75300`
  - information (sent): `#E8F5FB` / `#0070A8`
  - neutral (new, voided): `#EEEFEF` / `#292F32`
- Purple `#8F62F9` and tint `#F1E5FF`, charts only

## Type and shape

- Inter for UI (400–700), TASA Orbiter for headings (600/700), monospace for
  ids and amounts
- Body 14/20, table headers 12px, page titles 24px TASA, hero numbers 32–40px
  with tabular numerals on anything money-shaped
- Spacing ramp 2–56px, table padding 8/12, card padding 16–24
- Radii: 8px buttons, 8–12 cards, pills for badges
- Shadows stay near-flat: `0 2px 4px 0 hsla(200,10%,18%,0.06)` low and a
  slightly wider one for elevated surfaces. Nothing heavier.

## Layout

1. The overview opens on the number that matters for this product: rupees
   recovered (or recovery rate, treatment vs control).
2. KPI band of 3–5 cards: small muted label, large tabular value, colored
   delta. One chart per screen at most.
3. Below that, a recent-activity table and a link to the full episodes table
   with status pills, monospace ids, right-aligned amounts, sortable columns.
4. Episode detail is a vertical timeline with state-colored nodes.
5. The mode banner (normal/degraded/killed) uses Blade alert styling.
6. Compact over airy. No decorative gradients, no emoji in the UI, no
   rounded-everything.
