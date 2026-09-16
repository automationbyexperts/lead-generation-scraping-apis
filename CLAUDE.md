# CLAUDE.md

`Lead Generation Scraping APIs`, published as `github.com/automationbyexperts/lead-generation-scraping-apis` (local folder
`all_public_actors/APIFY_GITHUB/lead-generation-scraping-apis`). A curated list of the most used third-party Apify Store
actors in one Store category, modelled on cporter202/social-media-scraping-apis. It earns through
the Apify affiliate program: every Apify link carries `?fpr=youssef`. Youssef's own relevant actors
sit in the "Maintained by us" section. Sibling repos: `web-scraping-apis` (own actors only) and
the other category catalog in `APIFY_GITHUB/`.

This folder is **not an actor**. Never run `apify push` here.

## How it is built

`scripts/build.py` (standard library only, no token) reads the public Store API
(`store?category=...&sortBy=popularity`, first `fetchLimit` actors), drops actors with a notice or
zero users in the last 30 days, files each into a group by keyword (title and slug first, then
description, first matching group wins) and writes `README.md`, `groups/*.md`, `llms.txt`,
`data/actors.json` and `data/actors.csv`. **Everything except `scripts/` is generated**: change
`scripts/config.json` (titles, groups, keywords, featured actors, FAQ) instead of editing output.

`scripts/build.py` is identical across the category catalogs; only `config.json` differs. Fix a
bug in one and copy the file to the others.

## Rules

- No em dashes. Store text is cleaned; the build fails if one survives.
- No prices: third-party Store descriptions often quote rates, and `clean()` strips them with
  their "per 1k results" tail so the list never shows a stale price.
- The build refuses to run if the actor count drops below 80% of the last build (Store API outage).
- A week where only the date changed writes nothing, so the weekly Action makes no empty commit.

## Weekly refresh

`.github/workflows/refresh.yml` runs `build.py` every Monday 06:17 UTC and commits on change.
