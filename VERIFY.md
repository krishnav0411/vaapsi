# VERIFY.md — every headline claim, with its artifact and command

Nothing on the README should be taken on faith. Each claim below names the
artifact that proves it and the command that re-proves it.

| # | Claim | Artifact | Command |
|---|---|---|---|
| 1 | 364 backend tests pass | CI run (green badge) | `pytest -q` |
| 2 | 28 frontend tests pass | CI frontend job | `cd frontend && npm test` |
| 3 | Evaluation numbers are real and reproducible | `results/evaluation.json` | `make eval && python scripts/verify_numbers.py` — reruns 200 cases from seed 1403, byte-compares against the committed JSON |
| 4 | README's numbers can't drift from the artifact | `<!-- eval:start/end -->` block + drift guard | edit any digit in the block, rerun `python scripts/verify_numbers.py` → exits nonzero |
| 5 | The gauntlet holds all 16 attacks | `results/gauntlet_scorecard.json` | `python scripts/gauntlet.py` (exits 1 and records a defect if anything fails) |
| 6 | The ledger is tamper-evident | any episode timeline + `/app/ledger` | `make verify-chain`; or click "Run tamper demo" on `/app/ledger` and watch the verifier name the edited row on a sandbox copy |
| 7 | Public demo mode is fail-closed | `tests/test_demo_mode.py` (19 tests) + container smoke | `docker run -p 8100:8000 -e VAAPSI_PUBLIC_DEMO=1 <image>` → POST /api/kill → 404; boot with `RAZORPAY_KEY_ID` set → refuses to start |
| 8 | The live pipeline reached Razorpay | episode `ep_0b4de8d7d4…` timeline + `plink_TV8UE6fOGoo5Uh` | open the episode page; the 400 → DLQ → drain → link story is rendered from ledger rows |
| 9 | Zero false outreach so far | Overview card "FALSE OUTREACH 0" + ledger void rows | metric M5 query in `app/dashboard/api.py`; denominators published on the page |
| 10 | Cohorts assigned before any data | `EXPERIMENT.md` + `data/cohort_manifest.csv` | assignment written at creation, interleaved; addendum discloses the v1→v2 substrate change made *before* any halt |
| 11 | No secrets in the repo | `.env` gitignored; `.env.example` names only | `git log --all --diff-filter=A -- .env` (empty); secret scan in the repo's CI history |
| 12 | Kill switch is engine-side, one-way | `tests/test_day0.py` + live fire during the build | set `VAAPSI_KILL_SWITCH=1` → engine refuses all outbound regardless of UI |

## Reproduce the whole thing from a fresh clone

```bash
git clone https://github.com/krishnav0411/vaapsi && cd vaapsi
cp .env.example .env          # test keys of your own
make install && make test     # 364 tests
make eval                     # rebuilds the 200-case evaluation
make verify-chain             # replays the audit ledger
```

The dashboard ships pre-built in `frontend/dist/` — no Node needed to run it.

## What is NOT claimed

- No real money was moved; test mode end to end.
- The live recovery rate is genuinely **₹0 / 0%** while episodes wait out
  their policy windows — published with denominators, not hidden.
- The offline evaluation uses a *synthetic* outcome model — clearly labeled
  in the README block and in `results/evaluation.json` itself. It measures
  the decision pipeline, not the market.
