# Blade design system — repo and token notes

Notes from reading Razorpay's Blade repo directly (github.com/razorpay/blade,
MIT, docs at blade.razorpay.com), package v12.121.0. Checked on 2026-08-28.

## 1. Repository Status (github.com/razorpay/blade)

| Field | Value (verified via GitHub API, 2026-08-28) |
|---|---|
| Stars | 649 |
| Forks | 197 |
| Watchers/subscribers | 122 |
| Open issues+PRs | 283 |
| Language | TypeScript (React) |
| License | MIT |
| Default branch | master |
| Created | 2020-01-28 |
| Last push | 2026-08-28 (same day as research — commits land daily) |
| Archived | false |
| Latest release | @razorpay/blade-core@0.14.0 (2026-08-27) |
| npm | @razorpay/blade v12.111.0, published ~2026-08-06 (~22 days before research) |

**Verdict: fully live and actively maintained.** Recent commits (Aug 26–28, 2026): token pushes from Figma (`feat(tokens): update tokens from figma`), button focus-ring fixes, a new `blade-svelte` package, `blade-mcp` package (MCP server exposing Blade to AI tools), and Figma-coverage tooling. Some commits list a Claude co-author trailer, so the team clearly uses AI assistance in their workflow too.

**Monorepo packages:** `packages/blade` (main React component library), `packages/blade-core` (tokens + core), `packages/blade-svelte` (new Svelte port), `packages/blade-mcp` (MCP server), plus tooling packages.

Docs site: https://blade.razorpay.com/ — it is a **published Storybook** (1327 entries in index.json), not a traditional docs site. All docs are Storybook MDX pages.

## 2. Component Inventory (verified from repo tree + Storybook index)

204 component sections in Storybook. Major user-facing components (from `packages/blade/src/components/` and `components-*` Storybook ids):

- **Navigation:** TopNav, SideNav, BottomNav, AppBar, Breadcrumb, Tabs, TabNav, SegmentedControl, Pagination, SkipNav
- **Buttons:** Button (variants: primary/secondary/tertiary/transparent), IconButton, ButtonGroup, FloatingActionButton
- **Data display:** Table (with grouping, nesting, spanning, pagination), Card (plus InteractiveCard, TicketCard), Badge, Tag, Chip + ChipGroup, FilterChipGroup, Counter, Avatar + AvatarGroup, Indicator, TrustBadge, Amount, InfoGroup, Preview, Code
- **Charts (dedicated suite):** AreaChart, BarChart, DonutChart, LineChart, SankeyChart + chart color themes guide
- **Forms:** TextInput, TextArea, PasswordInput, SearchInput, PhoneNumberInput, OTPInput, CounterInput, ColorInput, SelectInput, AutoComplete, Dropdown (with select/button/link/treeview variants), Checkbox + CheckboxGroup, Radio + RadioGroup, Switch, DatePicker, TimePicker, FileUpload, InputGroup, Form, QuickFilterGroup
- **Feedback/overlays:** Modal (+ SimpleModal), Drawer, BottomSheet, Lightbox, Toast, Alert, Tooltip, Popover, SpotlightPopoverTour, Spinner, ProgressBar, Skeleton, EmptyState
- **Content:** Typography (Display, Heading, Text, Code), Link, List, Accordion, Collapsible, Divider, Carousel, AnnouncementBanner, Menu, ActionList, StepGroup, TreeView, ChatInput, ChatMessage, Icons (icon library)
- **Layout primitives:** Box (the universal layout primitive with styled props), Text as typography primitive
- **Accessibility utilities:** VisuallyHidden
- **Motion system:** motion primitives (Fade, Slide, Scale, Move, Morph, Elevate, Stagger, AnimateInteractions) with recipes

Docs sections beyond components: Guides (installation, theming, Blade MCP, Blade Svelte, chart color themes, component status, generative UI), Patterns (listview, creation view, detailed view, settings, confirmation, form group, GenUI), Recipes (simple dashboard, simple form, topnav dashboard), Tokens docs (theme, spacing, typography, border, elevation, motion, breakpoints), Utils (useTheme, makeSize/makeSpace/makeBorderSize/makeMotionTime).

## 3. Color Token Architecture (verified from `packages/blade-core/src/tokens/global/colors.ts`)

**Structure:** raw palette (primitives) → semantic theme tokens (surface/action/interactive). Raw colors are **HSLA with an opacity-multiplier system**: `hsla(H, S%, L%, ${opacity[N]})`. The `opacity` scale maps numeric names to alphas: 0→0, 1→0.01, 50→0.06, 100→0.09, 200→0.12, 300→0.18, 400→0.24, 500→0.32, 600→0.48, 700→0.56, 800→0.64, 900→0.72, 1000→0.8, 1100→0.88, 1200→0.94, 1300→1.0.

**IMPORTANT — primary color is BLUE, not purple:** the hypothesis "primary action purple #3D3AF6" is **wrong**. Blade's primary/action color is named **`azure`** — azure[500] = hsla(218, 89%, 51%) = **#1364F1** (a vivid blue). Purple exists only as a non-primary chart/data color (`orchid`, orchid[600] = #744ADE).

### Raw chromatic scales (11), each with steps 50–1000 + alpha variants (a50, a100, a150, a200, a400)
Resolved hex for the steps that semantic tokens actually use:

- **azure** (PRIMARY): 50 `#F5F9FF`, 100 `#D6E5FF`, 200 `#A8C8FF`, 300 `#75AAFF`, 400 `#4287FF`, **500 `#1364F1`**, **600 `#0E54CD`**, **700 `#0A44A9`**, 800 `#073688`, 900 `#052761`, 1000 `#021331`; alphas of 500: a50=0.09, a100=0.18, a150=0.24, a200=0.32, a400=0.64
- **emerald** (positive): 500 `#009954`, **600 `#008F47`**, **700 `#00753B`**
- **crimson** (negative): 500 `#DF3E30`, **600 `#D01E11`**, **700 `#AA180E`**
- **cider** (notice/orange): 500 `#F56D19`, **600 `#E05E00`**, **700 `#C75300`**
- **sapphire** (information): **600 `#008BD1`**, **700 `#0070A8`**
- sea (teal): 800 `hsla(180,61%,20%)` = #10555A-range; cloud (steel-blue): 50 `hsla(198,39%,95%)`
- forest (green), **orchid (purple): 600 `#744ADE`, 700 `#6038BC`**, magenta, topaz (gold/amber): 400 `#BD8A00`
- Data-color categories for charts map to: blue, green, red, orange, skyBlue, purple, pink, gold, gray

### Neutral scales
- **blueGrayLight** (light-mode neutrals): 0 `#FFFFFF` (pure white), 50 `#F7F7F7`, 100 `#F7F7F7`, 200 `#DEE1E3`, 300 `#C8CDD0`, 400 `hsla(205,8%,71%)`, 500 `hsla(203,8%,62%)`, 600 `hsla(202,8%,52%)`, 700 `#616D75`, 800 `hsla(206,9%,34%)`, 900 `hsla(206,10%,29%)` (#434B51), 1000 `hsla(205,10%,24%)`, 1100 `#292F32`, 1200 `hsla(200,11%,11%)`, **1300 `#050505`** (near-black text)
- **blueGrayDark** (dark-mode neutrals): 0 `#FFFFFF`, 50 `hsla(180,2%,92%)`, 100 `hsla(210,2%,84%)`, 200 `hsla(210,3%,76%)`, 300 `hsla(210,3%,69%)`, 400 `#96999C`, 500 `hsla(207,4%,52%)` (#808589), 600 `#73787D`, 700 `#585C5F`, 800 `#3B3D40`, 900 `hsla(210,4%,20%)`, 1000 `#27292B`, 1100 `#1F2123`, 1200 `#131415`, **1300 `#1B1C1D`**
- ashGrayLight / ashGrayDark (secondary neutral ramp, used by the newer "neutral" theme), plus static `white.*` and `black.*` alpha ramps (1→0.01 … 500→1.0)
- Compound alpha keys like `a912` = step-900 hue at opacity-step 12 (0.12) → e.g. blueGrayLight.a912 = hsla(206,10%,29%,0.12) (#434B51 @ 12%)

## 4. Semantic Theme Tokens (bladeTheme.ts, `onLight` / `onDark` — 434 leaves each)

### Surface (light mode)
- **Backgrounds:** `surface.background.gray.intense` = #FFFFFF (cards, sheets), `gray.moderate` = #F7F7F7 (page bg), `gray.subtle` = #F7F7F7; `primary.intense` = #1364F1 (primary button bg), `primary.subtle` = azure @ 9%; `sea.subtle` = sea[50], `sea.intense` = sea[800]; `cloud.subtle` = cloud[50], `cloud.intense` = cloud[800]
- **Borders:** `surface.border.gray.normal` = #C8CDD0, `gray.subtle` = #DEE1E3, `gray.muted` = blueGrayLight[900] @ 12% (#434B51 @ 0.12); `primary.normal` = #1364F1, `primary.muted` = azure @ 18%
- **Text:** `surface.text.gray.normal` = **#050505** (near-black), `gray.subtle` = #292F32, `gray.muted` = #616D75, `gray.disabled` = #434B51 @ 32%; `primary.normal` = #1364F1

### Surface (dark mode)
- **Backgrounds:** `gray.intense` = **#1F2123** (dark card bg), `gray.moderate` = **#131415** (dark page bg), `gray.subtle` = **#1B1C1D**; `primary.intense` = **#4287FF** (brighter azure for dark), `primary.subtle` = azure @ 32%
- **Borders:** `gray.normal` = #73787D, `gray.subtle` = #3B3D40, `gray.muted` = blueGrayDark[500] @ 18%; `primary.normal` = #4287FF
- **Text:** `gray.normal` = **#FFFFFF**, `gray.subtle` = #AEB0B2, `gray.muted` = #808589

### Interactive / action tokens (`interactive.*` — 174 per mode; this is what buttons/inputs consume)
- **Light:** `interactive.background.primary.default` = **#1364F1**, `.highlighted` = **#0E54CD**, `.disabled` = azure @ 18%; feedback: positive `#008F47`/hover `#00753B`, negative `#D01E11`/hover `#AA180E`, notice `#E05E00`/hover `#C75300`, information `#008BD1`/hover `#0070A8`; neutral button bg = `#000000` (hover #000 @ 88%); gray bg = blueGrayLight[900] @ 6%–12%; `staticWhite` = #FFFFFF ramps (for on-primary content), `staticBlack` = #000 ramps
- **Dark:** `interactive.background.primary.default` = **#4287FF**, `.highlighted` = **#1364F1**; neutral button bg flips to #FFFFFF
- Text/icon variants: `interactive.text.primary.normal` = #0E54CD (light) / #75AAFF (dark); `onPrimary` text/icon = #FFFFFF; `interactive.border.primary.default` = #1364F1 (light) / #4287FF (dark)

Full resolved JSON saved alongside this file: `interactive_tokens.json`, `theme_light_resolved.json`, `theme_dark_resolved.json`, `palette_resolved.json`.

### Brand theming
`createTheme.ts` ships `bladeTheme` (azure brand) and `bladeNeutralTheme`, plus a public `createTheme({ brandColors })` function that regenerates all interactive/surface/action tokens from a 50–1000 brand ramp (overrides map brandColors[600]→interactive.background.primary.default, [700]→highlighted, etc.). So Razorpay dashboards can be re-branded without forking tokens.

## 5. Typography (verified from `tokens/global/typography.ts` + `fontFamily/fontFamily.web.ts`)

- **Font families (web):**
  - text: **"Inter"** (fallback "Inter Fallback Arial", Arial — fallbacks defined in `packages/blade/fonts.css`, loaded from CDN)
  - heading: **"TASA Orbiter"** (fallback "TASA Orbiter Fallback Arial", Arial)
  - code: "Menlo", San Francisco Mono, Courier New, Roboto Mono, monospace
- **Weights:** regular 400, medium 500, semibold 600, bold 700 (only 4 weights)
- **Font-size scale (numeric keys 25–1100; desktop px / mobile px):**
  - 25 → 10/10, 50 → 11/11, 75 → 12/12, 100 → 14/14, 200 → 16/16, 300 → 18/16, 400 → 20/18, 500 → 24/20, 600 → 32/24, 700 → 40/32, 800 → 48/34, 900 → 56/36, 1000 → 64/38, 1100 → 72/40
  - Body text = 100 (14px desktop) and 200 (16px); dashboard headings typically 300–600
- Line-heights are aliased per size token (cross-platform px/rem/pt values, desktop/mobile variants)

## 6. Spacing (verified from `tokens/global/spacing.ts`)

Numeric keys 0–11, px values (applied as px/rem/pt per platform):
`0=0, 1=2, 2=4, 3=8, 4=12, 5=16, 6=20, 7=24, 8=32, 9=40, 10=48, 11=56`

Used via the `Box` primitive styled props (padding/margin/gap accept these keys).

## 7. Border & Elevation (verified from `tokens/global/border.ts`, `elevation/elevation.web.ts`)

- **Border radius:** none 0, 2xsmall 2, xsmall 4, small 8, medium 12, large 16, xlarge 20, 2xlarge 24, max 9999 (pill), round 50%
- **Border width:** none 0, thinner 0.5, thin 1, thick 1.5, thicker 2
- **Elevation (onLight):** lowRaised `0 2px 4px 0 hsla(200,10%,18%,0.06)`, midRaised `0 16px 12px 0 hsla(200,10%,18%,0.06)`, highRaised `0 8px 24px -4px hsla(200,10%,18%,0.06)` — all very subtle 6% alpha shadows
- **Elevation (onDark):** same geometry, black @ 0.32 alpha

## 8. Dark Mode (verified)

- First-class, token-level: every semantic color is a `{ onLight, onDark }` pair in `bladeTheme.ts` (434 mapped leaves per mode). Consumers switch via `BladeProvider theme="light"|"dark"|"system"` (`ColorSchemeNamesInput = 'dark' | 'light' | 'system'`).
- Elevation tokens are also mode-aware. Dark surfaces: page #131415, card #1F2123, borders #73787D/#3B3D40, text #FFFFFF/#AEB0B2; primary action brightens to #4287FF.
- Additional theme variants: `bladeNeutralTheme` (neutral/ash-gray brand) + `createTheme()` for custom brands.

## 9. Misc build-relevant facts

- Motion: `motion` tokens + makeMotionTime; dedicated Motion primitives (Fade/Slide/Scale/Move/Morph/Stagger).
- Breakpoints token file exists (xs/s/m/l/xl style), used for responsive typography decisions.
- Blade is cross-platform: web (React), React Native (mobile), and now Svelte. Tokens are unitless numbers rendered to px/rem/pt per platform.
- Ship a `themeToCSSVariables` utility — themes compile to CSS custom properties.
- The team publishes a **Blade MCP server** (`packages/blade-mcp`) exposing tokens/components to AI coding tools — evidence they actively support AI-assisted UI building.
- npm install: `npm install @razorpay/blade` (peer deps: react, react-dom; styled-components is bundled via their own engine). Docs quickstart: https://blade.razorpay.com/?path=/docs/guides-installation--docs

## 10. Sources

- https://github.com/razorpay/blade (repo, stats via api.github.com/repos/razorpay/blade, 2026-08-28)
- https://blade.razorpay.com/ (Storybook docs; index.json = 1327 entries)
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/colors.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/theme/bladeTheme.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/typography.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/fontFamily/fontFamily.web.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/spacing.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/border.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/opacity.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/global/elevation/elevation.web.ts
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/theme/theme.ts (types)
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade-core/src/tokens/theme/createTheme.ts (brand theming)
- https://raw.githubusercontent.com/razorpay/blade/master/packages/blade/src/components/Button/BaseButton/buttonTokens.ts
- https://www.npmjs.com/package/@razorpay/blade (v12.111.0)
- https://engineering.razorpay.com/cutting-deep-through-blade-23a72bcc3bcc (adoption case study)
