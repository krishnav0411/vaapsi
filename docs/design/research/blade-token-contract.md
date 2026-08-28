# Razorpay Merchant Dashboard — Design-Token Contract (2025–2026 era)

Primary source of truth: `razorpay/blade` GitHub repo @ master (`@razorpay/blade` v12.121.0, published ~Jul 2026, actively shipped). Blade is Razorpay's official, MIT-licensed, open-source design system: "The Design System that powers Razorpay" — cross-platform (React Web + React Native), documented at blade.razorpay.com (a public Storybook), with Figma community file, blade-mcp server, ESLint plugin, and a bundled `blade-dashboard-template` (Vite + React 19 + styled-components + framer-motion) intended for dashboard builds.

> Worth correcting from my earlier notes: Blade's default theme (`bladeTheme`) is **NOT purple**. Its primary is **azure blue** (hsl 218°). Razorpay's marketing "purple" identity is legacy/brand-side; the current product design language (Blade + the new "RazorSense" launch) is blue-led with purple available as a named accent (`orchid`, used for data-viz only in the default theme). Purple IS available as a swappable brand color via Blade's `createTheme({ brandColor })` white-labelling API. A "Razorpay-style" rebuild should default to azure primary + neutral gray surfaces, not purple.

---

## 1. COLOR TOKENS

### 1.1 Architecture
Three layers: **global primitives** (`global/colors.ts` — HSL scales with alpha variants) → **theme aliases** (`bladeTheme.ts` — `onLight`/`onDark` modes with semantic roles: `surface`, `feedback`, `interactive`, `action`, `data`) → components consume only alias tokens. All values defined in HSLA; alpha steps come from a shared opacity ramp (0.01→1.0, 16 steps).

Chromatic families: `azure` (blue, PRIMARY), `emerald` (green/positive), `crimson` (red/negative), `cider` (orange/notice), `sapphire` (cyan-blue/information), `sea`, `cloud`, `forest`, `orchid` (purple), `magenta` (pink), `topaz` (gold). Neutrals: `blueGrayLight` (light mode), `blueGrayDark` (dark mode), `ashGrayLight/Dark`, static `white`/`black` alpha ramps. Usage counts in theme: azure/emerald/crimson/cider/sapphire 86 refs each (core system); orchid/magenta/topaz 32 (data-viz only).

### 1.2 Exact values (HSL→hex computed; alpha composited over white where noted)

**Primary (interactive blue)**
| Token | HSL | Hex | Use |
|---|---|---|---|
| `azure.500` | 218° 89% 51% | **#1364F1** | Primary button default, links, focus, active nav |
| `azure.600` | 218° 87% 43% | **#0E54CD** | Primary hover/highlight, primary text on light |
| `azure.400` | 218° 100% 63% | #75AAFF-ish (73% L → #4D94FF family) | Muted primary text/icon |
| `azure.300` | 217° 100% 73% | #75AAFF | Primary on dark mode |
| `azure.a50` (9% α) | — | #EAF1FE | Subtle primary tint bg |
| `azure.a100` (18% α) | — | #D4E3FD | Primary border muted, disabled bg |
| `azure.a200` (12% α) | — | #E3ECFD | Dark-mode primary subtle |

**Neutrals (light mode surfaces/text/borders — the dashboard chrome)**
| Token | HSL | Hex | Use |
|---|---|---|---|
| `blueGrayLight.0/.50/.100` | 0° 0% 97% | **#F7F7F7** | Page background (`subtle`), card `moderate` bg |
| `blueGrayLight.0` intense | white | **#FFFFFF** | Card/surface `intense` bg |
| `blueGrayLight.200` | 204° 8% 88% | **#DEE1E3** | Subtle border, divider |
| `blueGrayLight.300` | 203° 8% 80% | **#C8CDD0** | Normal border, input borders |
| `blueGrayLight.400` | 205° 8% 71% | #B0B6BA | Hover border |
| `blueGrayLight.700` | 204° 9% 42% | **#616D75** | Muted text/icon |
| `blueGrayLight.1100` | 200° 10% 18% | **#292F32** | Subtle text/icon |
| `blueGrayLight.1300` | 0° 0% 2% | **#050505** | Normal text/icon (near-black) |
| `blueGrayLight.a932` (32% α) | — | #C3C5C7 | Disabled text/icon |
| `blueGrayLight.a912` (12% α) | — | #E8E9EA | Disabled borders/ghost |
| Gray interactive row hover | 205° 8% 71% @6%/12% | #FAFBFB / #F5F6F7 | Table row hover/highlight |

Dark mode: `blueGrayDark` ramp — 1300 #1C1C1E-family (210° 4% 11% ≈ #1B1D1E), 1200 #131315, 1100 (6% 13% ≈ #1F2123), 1000 #26282B, 800 #3B3D40.

**Status semantics (feedback tokens — payment states)**
| State | bg subtle (badge pill) | bg intense | text normal | border |
|---|---|---|---|---|
| `positive` (captured/completed/success) | emerald.a50 → **#E8F5EE** | emerald.600 **#008F47** | emerald.700 **#00753B** | emerald.600 |
| `negative` (failed/refunded-fail) | crimson.a50 → **#FBEBEA** | crimson.600 **#D01E11** | crimson.600 #D01E11 | crimson.600 |
| `notice` (pending/awaited) | cider.a50 → **#FCF0E8** | cider.600 **#E05E00** | cider.700 **#C75300** | cider.600 |
| `information` | sapphire.a50 → **#E8F5FB** | sapphire.600 **#008BD1** | sapphire.700 #0070A8 | sapphire.600 |
| `neutral` | blueGray.a909 → #EEEFEF | blueGray.1100 #292F32 | blueGray.500 | blueGray.900 |

Intense state colors: emerald.600 **#008F47**, crimson.600 **#D01E11**, cider.600 **#E05E00**, sapphire.600 **#008BD1** (all hover → .700: #00753B / #B8190E-family / #C75300 / #0070A8). Light tints (50-step): #E6F4ED emerald, #FDF3F2 crimson, #FFF6F0 cider, #E7F7FD sapphire, #F5F9FF azure, #F1E5FF orchid.

**Purple accent (data-viz / legacy brand)**
`orchid.500` = 258° 93% 68% → **#8F62F9**; orchid.600 #7E4DF0-family (58% L); orchid.700 54% 48% ≈ **#6038BC**; orchid.50 #F1E5FF. In `bladeTheme` orchid appears ONLY under `data.categorical.purple` (chart series), not as component primary.

**Marketing-site palette (razorpay.com, for brand-adjacent surfaces):** text `#192839` (dark navy), muted `#40566D` / `#768EA7`, brand blue `#305EFF` (+ `#1043F5`, `#4D7FFF`, `#75A3FF` tints), light bg `#F8FAFC`/`#F1F5FA`, success green `#009E5C`/`#48D08C`/`#006C3F`, light-mint `#B6ECD1`/`#EBFFF0`, danger red `#F0263C`, light-blue `#C5E5FF`.

### 1.3 Status→color mapping (official Table example, `exampleData.ts`)
`Completed → positive`, `Pending → notice`, `Failed → negative`, else `primary`. Rendered as `<Badge size="medium" color={...}>`. RazorpayX real-world states (captured/authorized/refunded/pending/failed) map green/amber/red the same way. Payment-ID cells use `<Code>` (monospace, `Menlo/SF Mono/Roboto Mono` stack); amounts use the `Amount` component.

---

## 2. TYPOGRAPHY

Named fonts (Blade `fontFamily` tokens, web): 
- **Text/UI: `"Inter"`** (variable, wght 100–900, bundled woff2 with cyrillic/greek/vietnamese subsets + `Inter Fallback Arial`)
- **Headings: `"TASA Orbiter"`** (variable, wght 125–950, width 75–125%, bundled woff2; a/dotpunch × Fontshare typeface) — native name "TASA Orbiter Display"
- **Code: `"Menlo", San Francisco Mono, Courier New, Roboto Mono, monospace`**
- Marketing site (razorpay.com) additionally uses **Inter Tight / Inter Display / TASA Orbiter Display** (legacy Lato appears only as leftover placeholder).

Type scale (desktop / mobile px), weights regular 400, medium 500, semibold 600, bold 700:
| Token | desktop | mobile | line-height (desktop) |
|---|---|---|---|
| 25 | 10 | 10 | 13 |
| 50 | 11 | 11 | 16 |
| 75 | 12 | 12 | 17 |
| 100 | **14** | 14 | 20 |
| 200 | 16 | 16 | 24 |
| 300 | 18 | 16 | 24 |
| 400 | 20 | 18 | 26 |
| 500 | 24 | 20 | 32 |
| 600 | 32 | 24 | 38 |
| 700 | 40 | 32 | 46 |
| 800 | 48 | 34 | 56 |
| 900 | 56 | 36 | 64 |
| 1000 | 64 | 38 | 70 |
| 1100 | 72 | 40 | 78 |

Letter-spacing: -3.3% (display), -1.3% (large headings), 0% (UI). Dashboard body size = 14 (`100`), table/header text 12–14, button text 12 (xsmall/small) or 14 (medium), 16 (large). Verdict on notes's typography question: they moved OFF Lato/Feather long ago; current stack is **Inter (UI) + TASA Orbiter (headings) + Menlo-family mono (IDs/amounts)**.

---

## 3. SPACING / DENSITY / RADII / ELEVATION / MOTION / BREAKPOINTS

**Spacing ramp (px):** 0, 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 56 (`spacing.0`–`spacing.11`). Dense-data rhythm: table cell padding & controls use 8/12; card padding 16/20/24; section gaps 24/32; page margins 40/48/56.

**Border radius:** none 0, 2xsmall 2, xsmall 4, small **8**, medium 12, large 16, xlarge 20, 2xlarge 24, max 9999 (pill), round 50%. Buttons: 8px (xsmall–medium), 12px (large); badges/pills = 9999. Cards/inputs ≈ 8–12px. Border widths: 0.5 / 1 / 1.5 / 2 px.

**Elevation (light mode):** none; lowRaised `0 2 4 0 hsla(200,10%,18%,0.06)`; midRaised `0 16 12 0 …0.06`; highRaised `0 8 24 -4 …0.06` — extremely soft, flat-leaning shadows. Buttons emulate 3D via layered inset box-shadows (border-color bottom + white top edges) rather than drop shadows.

**Motion:** durations 80/160/200/280/360/480/640/960ms (2xquick→2xgentle); easings `entrance cubic-bezier(0,0,0.2,1)`, `exit cubic-bezier(0.17,0,1,1)`, `standard cubic-bezier(0.3,0,0.2,1)`, `emphasized cubic-bezier(0.5,0,0,1)`. Motion presets shipped 2024 (RFC 2024-08-21); modal/dropdown entrance ~200–280ms.

**Breakpoints (mobile-first):** base 0, xs 320, s 480, **m 768 (desktop threshold)**, l 1024, xl 1200.

**Density:** compact controls — button min-heights 28/32/36/48 (xsmall/small/medium/large); icon-only 28/32/36/48; table rows single-line, 12–14px text, status as Badge pill (size medium), payment IDs in mono `Code` component, amounts via `Amount` component (formatted, with currency + i18n). Tooltip-titled headers with info IconButtons.

---

## 4. COMPONENT PATTERNS (from source)

**Buttons** — variants: primary (filled), secondary (white surface + border), tertiary (subtle), transparent; feedback colors: positive/negative/notice/information/neutral + `primary`. Sizes xsmall 28 / small 32 / medium 36 / large 48 high; padding-x 8/8/12/16 (spacing.3/.3/.4/.5); radius 8/8/8/12; text 12/12/14/16. Primary default bg `#1364F1`, hover `#0E54CD`, on-primary text white, disabled bg azure@18% tint with white text. Secondary: white bg + gray border (`blueGrayLight.200`, hover `.400`), brand-colored text (`azure.600`).

**Tables** — `Table/TableHeader/TableHeaderRow/TableHeaderCell/TableBody/TableRow/TableCell` + `TableToolbar` (+native), pagination examples. Dense default; sortable columns; custom-cell pattern with Badge status pills, Code IDs, Amount values.

**Badge (status pill)** — colors: positive/negative/notice/information/neutral/primary; sizes small/medium; subtle bg = 9% alpha of hue, text = .700 shade, border = .600. → Completed #E8F5EE/#00753B, Pending #FCF0E8/#C75300, Failed #FBEBEA/#D01E11, Info #E8F5FB/#0070A8.

**Navigation** — components: AppBar/BottomBar/SideNav patterns; dashboard convention: fixed left sidebar (~240–256px width token), icon+label items, active item = azure.500 icon/text + azure@9% tint bg or left indicator; collapsible. Top bar with global search + account switcher. (Merchant dashboard pages documented in i18nify blog: "Merchant Dashboard, Blade, and Checkout" all standardized on Blade.)

**Forms/inputs** — Input with label+hint+error, Dropdown (with action lists), Datepicker with QuickSelection PresideSideBar presets, OTP input, TextArea; all on 8/12px rhythm, 4px focus ring azure.

---

## 5. THEMING / WHITE-LABELLING (relevant for a rebuild)

`createTheme({ brandColor })` (current API; `overrideTheme` deprecated) takes any hex/rgb/hsl, auto-generates an 11-step chromatic scale (tinycolor brighten/darken), sets `.600` as interactive primary default, `.700` hover, auto-selects WCAG-AA(A)-readable foregrounds, and overrides surface/border/icon/text primary roles for both light+dark. Example in source: `createTheme({ brandColor: '#19BEA2' })`. So the dashboard can be rebranded purple by passing an orchid-family hex (e.g. `#6038BC` or `#7C3AED`) — Razorpay's own white-label product flows do this.

`blade-dashboard-template` (in-repo): Vite 6 + React 19 + TypeScript + styled-components 5 + framer-motion 11 + react-router 6 + `@razorpay/blade` ^12.58 + `@razorpay/i18nify-*`. Imports `@razorpay/blade/fonts.css` for Inter/TASA. This is Razorpay's blessed starting point for dashboard-shaped apps.

---

## 6. DESIGN PHILOSOPHY (public artifacts)

- **RazorSense** (razorpay.com/razorsense, launched ~2025–26): "The new Razorpay design language, built for the future. Where every product moment thinks, responds, and feels more alive." Built for "Humans in the AI era" — every state has a feeling; emotion states Calm/Joyful/Caution/Regret; the Razorpay glyph is the "atomic core" of every edge/angle; "the Flutes" are the living pulse/AI interface; components shown: Card, Button, Insights, Skeleton Loader, Thinking State, Ray Loading Progress Bar, Success State. Links to Blade as "the full design system."
- **Blade adoption essay** (engineering.razorpay.com, May 2024): trust is the driver — "A user is more likely to click on a payment button if they can associate it with a brand." Foundations: design consistency, developer productivity, cross-platform, reusability, responsiveness, accessibility (WCAG-checked token math in createTheme: level AAA large-text).
- **Merchant Experience re-architecture** (engineering.razorpay.com, Dec 2022): monolith → PWA; added the design system to kill inconsistency ("designers, PMs, and developers were duplicating efforts… increased the gap between our products and brands"); LCP 18s→improved, 89% onboarding dropout attack; ≥1.5X lift in key conversion metrics.
- **Motion craft** (medium.com/razorpay-design, Oct 2024): component animation audited across all BUs; motion tokens with preset structure; "functional, not decorative" motion baked at the design-system level.

Net philosophy: **functional over decorative; information-dense but scannable (compact tables, 9%-tint pills, mono IDs); trust-building via brand association, soft near-flat surfaces (0.06-alpha shadows), high-contrast near-black text (#050505) on #F7F7F7, restrained single-blue accent**; decorative purple/orchid reserved for data-viz; expressive motion introduced at the RazorSense layer (thinking states, pulses) on top of an austere token base.

---

## 7. READY-TO-USE TOKEN CONTRACT (distilled)

```css
:root {
  /* color */
  --primary-500: #1364F1;      /* primary fill, links, active nav */
  --primary-600: #0E54CD;      /* primary hover, primary text */
  --primary-tint-09: #EAF1FE;  /* active nav bg, selected row tint */
  --primary-tint-18: #D4E3FD;  /* disabled primary, focused borders */
  --bg-page: #F7F7F7;          /* canvas */
  --bg-surface: #FFFFFF;       /* cards, sidebar, topbar */
  --border-subtle: #DEE1E3;    /* dividers */
  --border-normal: #C8CDD0;    /* inputs, secondary buttons */
  --text-normal: #050505;      /* near-black */
  --text-subtle: #292F32;
  --text-muted: #616D75;
  --text-disabled: #C3C5C7;
  --row-hover: #FAFBFB;  --row-hover-strong: #F5F6F7;
  /* status pills (bg / text / border) */
  --positive-bg: #E8F5EE; --positive-fg: #00753B; --positive-solid: #008F47;
  --negative-bg: #FBEBEA; --negative-fg: #D01E11; --negative-solid: #D01E11;
  --notice-bg:   #FCF0E8; --notice-fg:   #C75300; --notice-solid:   #E05E00;
  --info-bg:     #E8F5FB; --info-fg:     #0070A8; --info-solid:     #008BD1;
  --neutral-bg:  #EEEFEF; --neutral-fg:  #292F32;
  --success-solid: #008F47; --danger-solid: #D01E11; --warning-solid: #E05E00;
  /* purple accent (data-viz / optional brand) */
  --accent-purple-500: #8F62F9; --accent-purple-700: #6038BC; --accent-purple-tint: #F1E5FF;
  /* typography */
  --font-ui: "Inter", "Inter Fallback Arial", Arial, sans-serif;
  --font-heading: "TASA Orbiter", "TASA Orbiter Fallback Arial", Arial, sans-serif;
  --font-mono: "Menlo", "SF Mono", "Courier New", "Roboto Mono", monospace;
  --fs-25: 10px; --fs-50: 11px; --fs-75: 12px; --fs-100: 14px; --fs-200: 16px;
  --fs-300: 18px; --fs-400: 20px; --fs-500: 24px; --fs-600: 32px; --fs-700: 40px;
  --lh-100: 20px; --lh-75: 17px; --lh-200: 24px; --lh-300: 24px;
  /* spacing (px) */
  --sp-1: 2px; --sp-2: 4px; --sp-3: 8px; --sp-4: 12px; --sp-5: 16px; --sp-6: 20px;
  --sp-7: 24px; --sp-8: 32px; --sp-9: 40px; --sp-10: 48px; --sp-11: 56px;
  /* radius / borders */
  --radius-xs: 2px; --radius-sm: 4px; --radius-md: 8px; --radius-lg: 12px;
  --radius-xl: 16px; --radius-pill: 9999px;
  --bw-thin: 1px; --bw-thick: 1.5px; --bw-bolder: 2px;
  /* elevation */
  --shadow-low: 0 2px 4px 0 hsla(200,10%,18%,0.06);
  --shadow-mid: 0 16px 12px 0 hsla(200,10%,18%,0.06);
  --shadow-high: 0 8px 24px -4px hsla(200,10%,18%,0.06);
  /* controls */
  --btn-h-xs: 28px; --btn-h-sm: 32px; --btn-h-md: 36px; --btn-h-lg: 48px;
  --btn-px-sm: 8px; --btn-px-md: 12px; --btn-px-lg: 16px;
  --sidebar-w: 256px; --breakpoint-desktop: 768px; --breakpoint-lg: 1024px; --breakpoint-xl: 1200px;
  /* motion */
  --dur-quick: 160ms; --dur-base: 200ms; --dur-moderate: 280ms;
  --ease-entrance: cubic-bezier(0,0,0.2,1); --ease-exit: cubic-bezier(0.17,0,1,1);
  --ease-standard: cubic-bezier(0.3,0,0.2,1);
}
```

Layout pattern: fixed left sidebar (256px, white on #F7F7F7 canvas, icon+label, azure active state), top AppBar with search + account switcher, content column max ~1136–1200px, cards white with 1px #DEE1E3 border + low shadow, dense tables with 12px header text / 14px cells, right-aligned mono amounts, Badge status pills, toolbar with filters + Datepicker quick-presets, pagination at bottom.

---

## SOURCES

1. Blade repo (tokens/theme/button/table sources read directly @ master): https://github.com/razorpay/blade
2. Blade docs (public Storybook): https://blade.razorpay.com/
3. Figma: https://www.figma.com/community/file/1341658976127676210/blade-design-system
4. npm @razorpay/blade v12.121.0: https://npmjs.com/package/@razorpay/blade
5. RazorSense design language: https://razorpay.com/razorsense/
6. Blade adoption essay: https://engineering.razorpay.com/cutting-deep-through-blade-23a72bcc3bcc
7. Merchant experience re-architecture: https://engineering.razorpay.com/redesigning-rearchitecting-the-merchant-experience-d788bb44e526
8. Motion in Blade: https://medium.com/razorpay-design/behind-the-scenes-of-animating-a-design-system-component-60b77290ba08
9. i18nify (dashboard+Blade standardization): https://razorpay.com/blog/i18nifyjs-approach-to-internationalisation/
10. raw token sources: https://raw.githubusercontent.com/razorpay/blade/master/packages/blade/src/tokens/global/colors.ts (+ typography.ts, spacing.ts, border.ts, motion.ts, opacity.ts, size.ts, breakpoints.ts, fontFamily/fontFamily.web.ts, elevation/elevation.web.ts), …/src/tokens/theme/bladeTheme.ts, createTheme.ts, …/src/components/Button/BaseButton/buttonTokens.ts, …/src/components/Table/docs/exampleData.ts, …/blade-dashboard-template/package.json, packages/blade/fonts.css
