# Vaapsi frontend

React + Vite + TypeScript + Tailwind, styled to Razorpay's Blade design
system. Blade is open source, so rather than eyeballing screenshots I pulled
their actual token values; every color, spacing, radius, and shadow in
`src/index.css` traces to a Blade token.

## Commands

```sh
npm install
npm run dev      # dev server, proxies /api and /dashboard to :8000
npm run build    # tsc -b && vite build, output in dist/
```

The built `dist/` is committed, and the FastAPI app serves it at `/app`, so a
fresh clone shows the working dashboard without installing node.

## Fonts

Razorpay's fonts come from their Blade npm package. I ran `npm pack
@razorpay/blade`, extracted it, and copied `fonts.css` plus all 8 woff2 files
into `public/fonts/`. TASA Orbiter (their display face) is in the package as
a variable font, and Inter ships as variable subsets. No CDN, no fallback
fonts, nothing hotlinked.

`fonts.css` originally referenced files with relative paths; I rewrote those
to root-absolute paths (`/fonts/fonts/...`) so they resolve the same when the
css gets inlined into the bundle.

## Design tokens

- Tailwind v4 `@theme` holds the Blade colors (azure primary #1364F1, hover
  #0E54CD, tint #EAF1FE, canvas #F7F7F7, the five status pill colors, and the
  purple #8F62F9 which Blade reserves for charts), font stacks, and
  `--spacing: 1px` so numeric utilities map to Blade's pixel ramp directly.
- `:root` custom properties (`--v-*`) carry the same values as plain css vars.
- `@utility tnum` applies tabular numerals to money columns and metric
  values, following what every payments dashboard does with figures.

One quirk worth remembering: the css minifier rewrites
`hsla(200,10%,18%,0.06)` to the equivalent `#292f320f`. Same color, different
spelling, and the css regression tests accept both.

## Dev proxy

`vite.config.ts` proxies `/api` and `/dashboard` to `http://localhost:8000`
so the React app and the FastAPI backend share an origin during development.
