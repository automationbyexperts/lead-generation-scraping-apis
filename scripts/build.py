"""Build a curated catalog of the most used Apify Store actors in one Store category.

Reads the public Apify Store API only (no token), keeps the most popular live actors, files each
one into a topic group by keyword, and regenerates README.md, groups/, llms.txt, data/actors.json
and data/actors.csv. Everything the build needs is in scripts/config.json.
Standard library only, safe to run in CI.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / 'scripts' / 'config.json').read_text(encoding='utf-8'))

REFERRAL = 'fpr=youssef'
OWNER = 'automationbyexperts'
REPO = CONFIG['repo']
REPO_URL = f'https://github.com/{OWNER}/{REPO}'
SITE = 'https://automationbyexperts.com'
UTM = f'utm_source=github&utm_medium=referral&utm_campaign={REPO}'
MAINTAINER = 'fayoussef'
CONTACT_EMAIL = 'youssefarhan24@gmail.com'
README_ROWS = 15          # rows per group shown in README.md; the full list lives in groups/
MIN_USERS_30D = 1         # an actor nobody ran in 30 days is not worth recommending

# Build fails if any of these reach a generated file.
FORBIDDEN = ('dataimpulse', 'sk-or-v1-', 'CAP-', 'apify_api_')
# A price inside someone else's Store text goes stale, so it is cut with its "per 1,000 results" tail.
PRICE_PHRASE = re.compile(r'(from |only |just |at )?\$\s?\.?\d[\d.,]*\s*[kKmM]?(\s*(/|per)\s*[\d.,]*\s*[kK]?(\s*\w+){1,2})?', re.IGNORECASE)


# ---------------------------------------------------------------- fetching

def fetch_json(url: str):
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': f'{REPO}-catalog-builder'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as exc:  # network hiccup: retry with a capped backoff
            last = exc
            print(f'  retry {attempt + 1} for {url}: {type(exc).__name__}', file=sys.stderr)
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f'GET {url} failed: {last}')


def store_page(params: dict) -> dict:
    return fetch_json('https://api.apify.com/v2/store?' + urllib.parse.urlencode(params))['data']


def category_actors() -> list[dict]:
    items, offset, limit = [], 0, CONFIG['fetchLimit']
    while offset < limit:
        page = store_page({'category': CONFIG['storeCategory'], 'sortBy': 'popularity',
                           'limit': 100, 'offset': offset})
        items.extend(page['items'])
        offset += len(page['items'])
        print(f'  fetched {offset} of {min(limit, page["total"])}')
        if not page['items'] or offset >= page['total']:
            break
    return items, page['total']


def regional_actors() -> list[dict]:
    """Local sources (a Brazilian company registry, a Spanish job board) rarely reach the category's
    top by popularity, so a translated catalog searches for them by name. Store search is loose and
    matches descriptions, so a hit only counts when its title or slug names a keyword of the target group."""
    found = []
    for extra in CONFIG.get('extraSearches', []):
        group = next(g for g in CONFIG['groups'] if g['slug'] == extra['group'])
        for term in extra['terms']:
            for raw in store_page({'search': term, 'limit': 100})['items']:
                head = f'{raw.get("title") or ""} {raw["name"].replace("-", " ")}'.lower()
                if any(re.search(rf'\b{re.escape(k)}', head) for k in group['keywords']):
                    found.append(raw)
        print(f'  regional search for {extra["group"]}: {len(found)} hits so far')
    return found


def maintainer_actors() -> dict[str, dict]:
    page = store_page({'username': MAINTAINER, 'limit': 100})
    return {a['name']: a for a in page['items']}


# ---------------------------------------------------------------- text helpers

EMOJI = re.compile('[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]')


def clean(text: str) -> str:
    text = (text or '').replace('\u2014', ' - ').replace('\u2013', '-')
    text = EMOJI.sub('', text)
    text = PRICE_PHRASE.sub('', text)
    text = re.sub(r'\|(\s*\|)+', '|', text)
    text = re.sub(r'\s+([,.;:])', r'\1', re.sub(r'\s+', ' ', text))
    text = re.sub(r'([,;:])[,;:]+', r'\1', text)
    return text.strip(' -|,.:')


def short(text: str, limit: int = 150) -> str:
    text = clean(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(' ', 1)[0].rstrip(',.;:(') + '...'


def cell(text: str) -> str:
    return text.replace('|', '\\|').replace('[', '(').replace(']', ')')


def anchor(title: str) -> str:
    slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
    return '#' + slug


def link(actor: dict) -> str:
    return f'https://apify.com/{actor["username"]}/{actor["name"]}?{REFERRAL}'


# ---------------------------------------------------------------- shaping

def shape(raw: dict) -> dict:
    stats = raw.get('stats') or {}
    rating = raw.get('actorReviewRating')
    return {
        'id': f'{raw["username"]}/{raw["name"]}',
        'username': raw['username'],
        'name': raw['name'],
        'title': clean(raw.get('title') or raw['name']) or raw['name'],
        'developer': clean(raw.get('userFullName') or raw['username']),
        'description': clean(raw.get('description') or ''),
        'totalUsers': stats.get('totalUsers') or 0,
        'monthlyUsers': stats.get('totalUsers30Days') or 0,
        'rating': round(rating, 1) if rating else None,
        'reviews': raw.get('actorReviewCount') or 0,
        'url': link(raw),
    }


def assign_group(actor: dict) -> str:
    """First group whose keyword appears in the title or slug, then in the description."""
    head = f'{actor["title"]} {actor["name"].replace("-", " ")}'.lower()
    body = actor['description'].lower()
    for text in (head, body):
        for group in CONFIG['groups']:
            if any(re.search(rf'\b{re.escape(k)}', text) for k in group['keywords']):
                return group['slug']
    return CONFIG['fallbackGroup']['slug']


# Every sentence a reader sees. config.json "ui" overrides any key, which is how the translated
# catalogs reuse this exact file. Placeholders are filled with str.format.
UI_EN = {
    'statsLine': '**{count} actors in use** | **{storeTotal} screened** | **{topics} topics** | Updated {today}',
    'startFree': 'Start free on Apify',
    'llmsLink': 'llms.txt for AI assistants',
    'customLink': 'Custom scrapers',
    'languages': 'Languages',
    'whatIs': 'What is this?',
    'howItWorks': 'Every entry is a hosted cloud tool (an "Actor") on the Apify platform: open it, fill in the input, '
                  'click Start, and export the results as JSON, CSV or Excel, or call it from code through the Apify '
                  'API. Apify gives every new account free monthly credit, enough to try most of these actors '
                  'without paying.',
    'descriptionNote': '',
    'contents': 'Contents',
    'topTitle': 'Top {n} most used',
    'topIntro': 'Ranked by how many different people ran them in the last 30 days.',
    'colActor': 'Actor', 'colWhat': 'What it does', 'colUsers': 'Monthly users', 'colRating': 'Rating',
    'by': 'by',
    'seeAll': 'See all {n} {title} actors',
    'ownTitle': 'Maintained by us',
    'ownIntro': 'Actors built and maintained by [{brand}]({site}), the team behind this list. Bugs reported '
                'through the Apify issue tab are usually fixed within days.',
    'chooseTitle': 'How to choose an actor',
    'choose': [
        '**Monthly users** is the best single signal: it counts distinct people who ran the actor in the last 30 '
        'days, so abandoned tools sink to the bottom.',
        '**Rating** comes from Apify Store reviews. A 4.5+ rating with dozens of reviews means the output is reliable.',
        '**Pricing model** differs per actor (pay per result, pay per event, or a monthly rental). The live price '
        'is always shown on the actor page, so compare there before a big run.',
        '**Run a small test first.** Limit the input to 10 or 20 results, check the fields you need, then scale up.',
    ],
    'runTitle': 'How to run one',
    'run': [
        '[Create a free Apify account]({signup}) (no card needed).',
        'Open any actor above and click **Try for free**.',
        'Fill in the input form (search terms, URLs or filters) and click **Start**.',
        'Download the results from the **Output** tab, or schedule the actor and send results to Google Sheets, '
        'Zapier, Make, n8n or a webhook.',
    ],
    'fromCode': 'From code, every actor has the same API:',
    'faqTitle': 'FAQ',
    'faqUpdatedQ': 'How often is this list updated?',
    'faqUpdatedA': 'A GitHub Action rebuilds it every week from the public Apify Store API, so new actors appear '
                   'and dead ones drop off automatically.',
    'faqAddQ': 'Can I add my actor?',
    'faqAddA': 'Publish it in the Apify Store under the {category} category. Once people start running it, it '
               'enters the list on the next weekly build. No pull request needed.',
    'customTitle': 'Need a custom scraper?',
    'customText': 'If no actor here fits your source, [{brand}]({site}) builds and maintains custom Apify '
                  'scrapers. Email {email} with the site and the fields you need.',
    'footer': 'If this list saved you time, a star helps other people find it. Links to Apify carry a referral '
              'code that supports the upkeep of this list at no extra cost to you.',
    'groupPageTitle': '{title}: {n} Apify actors',
    'groupPageIntro': 'Part of [{catalog}](../README.md). Sorted by monthly users. Updated {today}.',
    'groupPageCta': '[Create a free Apify account]({signup}) to run any of these.',
    'llmsIntro': 'Updated {today}. Monthly users = distinct users in the last 30 days. Links include a referral code.',
    'llmsUsers': '{n} monthly users',
}
UI = {**UI_EN, **CONFIG.get('ui', {})}
THOUSANDS = CONFIG.get('thousandsSeparator', ',')


def number(n: int) -> str:
    return f'{n:,}'.replace(',', THOUSANDS)


def rating_text(actor: dict) -> str:
    if not actor['rating'] or actor['reviews'] < 1:
        return '-'
    return f'{actor["rating"]} ({actor["reviews"]})'


def table(actors: list[dict], rank: bool = False) -> list[str]:
    cols = f'{UI["colActor"]} | {UI["colWhat"]} | {UI["colUsers"]} | {UI["colRating"]} |'
    head = f'| # | {cols}' if rank else f'| {cols}'
    sep = '|---|---|---|---:|---|' if rank else '|---|---|---:|---|'
    rows = [head, sep]
    for i, a in enumerate(actors, 1):
        title = cell(a['title'])
        desc = cell(short(a['description']))
        line = (f'| [{title}]({a["url"]}) <br><sub>{UI["by"]} {cell(a["developer"])}</sub> | {desc} | '
                f'{number(a["monthlyUsers"])} | {rating_text(a)} |')
        rows.append(f'| {i} {line}' if rank else line)
    return rows


# ---------------------------------------------------------------- rendering

def render(actors, groups, featured, store_total, today) -> dict[str, str]:
    c = CONFIG
    files: dict[str, str] = {}
    top = sorted(actors, key=lambda a: -a['monthlyUsers'])[:c['topCount']]
    site = f'{SITE}/apify?{UTM}'
    signup = f'https://apify.com/?{REFERRAL}'
    top_title = UI['topTitle'].format(n=c['topCount'])

    out = [
        f'# {c["title"]}',
        '',
        f'> {c["tagline"]}',
        '',
        UI['statsLine'].format(count=number(len(actors)), storeTotal=number(store_total), topics=len(groups),
                               today=today),
        '',
        f'[{UI["startFree"]}]({signup}) | [{UI["llmsLink"]}](llms.txt) | '
        f'[JSON](data/actors.json) | [CSV](data/actors.csv) | [{UI["customLink"]}]({site})',
        '',
    ]
    if c.get('languages'):
        langs = ' | '.join(f'**{l["label"]}**' if l['repo'] == REPO else
                           f'[{l["label"]}](https://github.com/{OWNER}/{l["repo"]})' for l in c['languages'])
        out += [f'{UI["languages"]}: {langs}', '']
    out += [
        f'## {UI["whatIs"]}',
        '',
        c['intro'].format(count=number(len(actors)), storeTotal=number(store_total)),
        '',
        UI['howItWorks'],
        '',
    ]
    if UI['descriptionNote']:
        out += [UI['descriptionNote'], '']
    out += [f'## {UI["contents"]}', '', f'- [{top_title}]({anchor(top_title)})']
    for g in groups:
        out.append(f'- [{g["title"]}]({anchor(g["title"])}) ({len(g["actors"])})')
    for key in ('ownTitle', 'chooseTitle', 'runTitle', 'faqTitle', 'customTitle'):
        if key != 'ownTitle' or featured:
            out.append(f'- [{UI[key]}]({anchor(UI[key])})')
    out += ['', f'## {top_title}', '', UI['topIntro'], '', *table(top, rank=True), '']
    for g in groups:
        out += [f'## {g["title"]}', '', g['blurb'], '']
        out += table(g['actors'][:README_ROWS])
        if len(g['actors']) > README_ROWS:
            out += ['', f'[{UI["seeAll"].format(n=len(g["actors"]), title=g["title"])}](groups/{g["slug"]}.md)']
        out.append('')
    if featured:
        out += [f'## {UI["ownTitle"]}', '', UI['ownIntro'].format(brand=c['brand'], site=site), '',
                *table(featured), '']
    out += [f'## {UI["chooseTitle"]}', '', *[f'- {line}' for line in UI['choose']], '']
    out += [f'## {UI["runTitle"]}', '']
    out += [f'{i}. {line.format(signup=signup)}' for i, line in enumerate(UI['run'], 1)]
    out += [
        '',
        UI['fromCode'],
        '',
        '```python',
        'from apify_client import ApifyClient',
        '',
        'client = ApifyClient("<YOUR_APIFY_TOKEN>")',
        f'run = client.actor("{c["exampleActor"]}").call(run_input={json.dumps(c["exampleInput"], ensure_ascii=False)})',
        'for item in client.dataset(run["defaultDatasetId"]).iterate_items():',
        '    print(item)',
        '```',
        '',
        f'## {UI["faqTitle"]}',
        '',
    ]
    for q, a in c['faq']:
        out += [f'### {q}', '', a, '']
    out += [
        f'### {UI["faqUpdatedQ"]}', '', UI['faqUpdatedA'], '',
        f'### {UI["faqAddQ"]}', '', UI['faqAddA'].format(category=c['storeCategoryLabel']), '',
        f'## {UI["customTitle"]}', '',
        UI['customText'].format(brand=c['brand'], site=site, email=CONTACT_EMAIL), '',
        '---', '',
        UI['footer'], '',
    ]
    files['README.md'] = '\n'.join(out)

    for g in groups:
        page = [
            f'# {UI["groupPageTitle"].format(title=g["title"], n=len(g["actors"]))}',
            '',
            g['blurb'],
            '',
            UI['groupPageIntro'].format(catalog=c['title'], today=today),
            '',
            *table(g['actors'], rank=True),
            '',
            UI['groupPageCta'].format(signup=signup),
            '',
        ]
        files[f'groups/{g["slug"]}.md'] = '\n'.join(page)

    llms = [f'# {c["title"]}', '', f'> {c["tagline"]}', '', UI['llmsIntro'].format(today=today), '']
    for g in groups:
        llms += [f'## {g["title"]}', '']
        for a in g['actors']:
            users = UI['llmsUsers'].format(n=number(a['monthlyUsers']))
            llms.append(f'- [{a["title"]}]({a["url"]}): {short(a["description"], 220)} ({users})')
        llms.append('')
    files['llms.txt'] = '\n'.join(llms)
    return files


# ---------------------------------------------------------------- main

def main() -> None:
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    raw, store_total = category_actors()
    raw += regional_actors()
    seen, actors = set(), []
    for r in raw:
        a = shape(r)
        if r.get('notice', 'NONE') not in ('NONE', None) or a['id'] in seen:
            continue
        if a['monthlyUsers'] < MIN_USERS_30D:
            continue
        seen.add(a['id'])
        actors.append(a)

    previous = ROOT / 'data' / 'actors.json'
    if previous.exists():
        old = len(json.loads(previous.read_text(encoding='utf-8'))['actors'])
        if len(actors) < old * 0.8:
            sys.exit(f'Refusing to build: {len(actors)} actors vs {old} last time, Store API looks broken.')

    actors.sort(key=lambda a: (-a['monthlyUsers'], -a['totalUsers']))
    for a in actors:
        a['group'] = assign_group(a)

    groups = []
    for g in CONFIG['groups'] + [CONFIG['fallbackGroup']]:
        members = [a for a in actors if a['group'] == g['slug']]
        if members:
            groups.append({**g, 'actors': members})

    own = maintainer_actors()
    featured = [shape(own[n]) for n in CONFIG['featured'] if n in own]
    for n in CONFIG['featured']:
        if n not in own:
            print(f'NOTE: featured actor {n} not in the Store listing, skipped', file=sys.stderr)

    files = render(actors, groups, featured, store_total, today)
    files['data/actors.json'] = json.dumps({
        'title': CONFIG['title'], 'updated': today, 'storeCategory': CONFIG['storeCategory'],
        'groups': [{'slug': g['slug'], 'title': g['title'], 'count': len(g['actors'])} for g in groups],
        'actors': actors,
    }, ensure_ascii=False, indent=1) + '\n'

    for name, text in files.items():
        lowered = text.lower()
        for bad in FORBIDDEN:
            if bad.lower() in lowered:
                sys.exit(f'Refusing to write {name}: contains forbidden string {bad!r}')
        if '\u2014' in text:
            sys.exit(f'Refusing to write {name}: contains an em dash')

    # A quiet week should not produce a commit just because the date moved.
    readme = ROOT / 'README.md'
    if readme.exists():
        strip = lambda t: re.sub(r'\d{4}-\d{2}-\d{2}', '', t)
        old_json = previous.read_text(encoding='utf-8') if previous.exists() else ''
        if strip(readme.read_text(encoding='utf-8')) == strip(files['README.md']) and \
                strip(old_json) == strip(files['data/actors.json']):
            print('No content change, leaving files alone.')
            return

    groups_dir = ROOT / 'groups'
    if groups_dir.exists():
        for f in groups_dir.glob('*.md'):
            f.unlink()
    for name, text in files.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    with (ROOT / 'data' / 'actors.csv').open('w', encoding='utf-8', newline='') as fh:
        cols = ['title', 'developer', 'url', 'group', 'monthlyUsers', 'totalUsers', 'rating', 'reviews', 'description']
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(actors)

    print(f'Built {len(actors)} actors in {len(groups)} groups, {len(featured)} featured.')
    for g in groups:
        print(f'  {g["slug"]}: {len(g["actors"])}')


if __name__ == '__main__':
    main()
