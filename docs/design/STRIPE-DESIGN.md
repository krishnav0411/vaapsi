# Stripe dashboard design notes (legacy)

The first dashboard pass used Stripe's design tokens, read from their public
dashboard, before the whole thing got redone in Razorpay's Blade language.
These notes stay because the older server-rendered pages at `/dashboard`
still render with them. If the CSS and this file disagree, this file wins,
for those pages.

## Tokens

```css
:root {
  /* brand */
  --vaapsi-purple: #533afd;        /* primary CTA, links, active states */
  --vaapsi-purple-hover: #4434d4;
  --vaapsi-purple-light: #b9b9f9;  /* subdued hover bg, ghost borders */
  --vaapsi-navy: #061b31;          /* headings, never pure black */
  --vaapsi-brand-dark: #1c1e54;    /* dark sections */
  /* text */
  --vaapsi-label: #273951;
  --vaapsi-body: #64748d;
  /* surfaces */
  --vaapsi-border: #e5edf5;
  --vaapsi-bg: #ffffff;
  /* status */
  --vaapsi-success-bg: rgba(21, 190, 83, 0.2);
  --vaapsi-success-text: #108c3d;
  --vaapsi-warn: #9b6829;          /* gated/warning */
  --vaapsi-danger: #ea2261;        /* accents only, never buttons */
  /* shadows, the stripe signature */
  --vaapsi-shadow-elevated: rgba(50, 50, 93, 0.25) 0px 30px 45px -30px,
                            rgba(0, 0, 0, 0.1) 0px 18px 36px -18px;
  --vaapsi-shadow-standard: rgba(23, 23, 23, 0.08) 0px 15px 35px 0px;
  --vaapsi-shadow-ambient: rgba(23, 23, 23, 0.06) 0px 3px 6px;
  /* radii, 4-8px, no pills */
  --vaapsi-r-sm: 4px;
  --vaapsi-r-md: 5px;
  --vaapsi-r-lg: 6px;
  --vaapsi-r-xl: 8px;
}
```

## Fonts

Source Sans 3 for UI, Source Code Pro for numbers and ids, from Google Fonts:

```html
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;500;600&family=Source+Code+Pro:wght@400;500;700&display=swap" rel="stylesheet">
```
