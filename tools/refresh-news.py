#!/usr/bin/env python3
"""Fetch only fixed, public US government headline feeds. No credentials required."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import email.utils
import html
import json
import pathlib
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UTC = dt.timezone.utc
MAX_BYTES = 262_144
MAX_ITEMS = 50
TIMEOUT_SECONDS = 30
MAX_RETRY_WAIT_SECONDS = 30
USER_AGENT = 'HorizonTerminal/1.0 (private financial research; public government headline RSS reader)'
FEEDS = {
    'bls-cpi': ('https://www.bls.gov/feed/cpi.rss', 'bls.gov'),
    'bls-jobs': ('https://www.bls.gov/feed/empsit.rss', 'bls.gov'),
    'eia': ('https://www.eia.gov/rss/press_rss.xml', 'eia.gov'),
}
ATOM = '{http://www.w3.org/2005/Atom}'
OUTPUT = pathlib.Path(__file__).resolve().parents[1] / 'data' / 'government-news.json'


class SourceError(Exception):
    """A fixed diagnostic category, never upstream response text."""

    def __init__(self, reason: str, status: int | None = None, retry_after: float = 0):
        super().__init__(reason)
        self.reason, self.status, self.retry_after = reason, status, retry_after


def timestamp(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise SourceError('timestamp')
    return value.astimezone(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def read_timestamp(value: object, now: dt.datetime, tolerance: int = 60) -> dt.datetime:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', value):
        raise SourceError('timestamp')
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise SourceError('timestamp') from error
    if result < dt.datetime(2000, 1, 1, tzinfo=UTC) or result > now + dt.timedelta(seconds=tolerance):
        raise SourceError('timestamp')
    return result


def article_url(value: object, feed_id: str, decode: bool = True) -> str:
    if not isinstance(value, str):
        raise SourceError('article-url')
    raw = html.unescape(value) if decode else value
    if feed_id == 'eia' and raw.startswith('/pressroom/'):
        raw = 'https://www.eia.gov' + raw
    if not raw or len(raw) > 2048 or re.search(r'[\x00-\x20\x7f\\]', raw):
        raise SourceError('article-url')
    try:
        url = urllib.parse.urlsplit(raw)
        host = url.hostname or ''
        port = url.port
    except ValueError as error:
        raise SourceError('article-url') from error
    allowed = FEEDS[feed_id][1]
    if url.scheme != 'https' or url.username or url.password or port is not None or not (host == allowed or host.endswith('.' + allowed)):
        raise SourceError('article-url')
    query = [(key, val) for key, val in urllib.parse.parse_qsl(url.query, keep_blank_values=True)
             if not key.lower().startswith('utm_') and key.lower() not in ('fbclid', 'gclid')]
    normalized = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, urllib.parse.urlencode(query), ''))
    if len(normalized) > 2048:
        raise SourceError('article-url')
    return normalized


def headline(raw: object) -> str:
    if not isinstance(raw, str) or len(raw) > 8192:
        raise SourceError('headline')
    value = html.unescape(raw)
    value = re.sub(r'<(script|style)\b[^>]*>[\s\S]*?</\1\s*>', ' ', value, flags=re.I)
    value = re.sub(r'<[^>]*>', ' ', value)
    value = re.sub(r'[<>\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', ' ', value)
    value = ' '.join(value.split())
    if not value or len(value) > 500:
        raise SourceError('headline')
    return value


def publication(raw: str | None, now: dt.datetime) -> str | None:
    if raw is None or raw == '':
        return None
    raw = raw.strip()
    if not raw or len(raw) > 100:
        raise SourceError('publication-time')
    raw = re.sub(r'\s(EST|EDT)$', lambda match: ' -0500' if match[1] == 'EST' else ' -0400', raw)
    iso = re.fullmatch(r'\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-]\d{2}:?\d{2})', raw)
    rss = re.fullmatch(r'(?:[A-Za-z]{3},?\s+)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\s+(?:GMT|UTC|[+-]\d{4})', raw)
    if not iso and not rss:
        raise SourceError('publication-time')
    try:
        # Python 3.9 accepts only three or six fractional digits. BLS publishes
        # legitimate one/two-digit fractions too; padding preserves the instant.
        canonical = re.sub(r'\.(\d{1,3})(?=Z|[+-])', lambda match: '.' + match[1].ljust(6, '0'), raw)
        value = dt.datetime.fromisoformat(canonical.replace('Z', '+00:00')) if iso else email.utils.parsedate_to_datetime(raw)
    except (ValueError, TypeError, OverflowError) as error:
        raise SourceError('publication-time') from error
    if value.tzinfo is None or value < dt.datetime(2000, 1, 1, tzinfo=UTC) or value > now + dt.timedelta(minutes=5):
        raise SourceError('publication-time')
    return timestamp(value)


def parse_feed(feed_id: str, body: bytes, now: dt.datetime) -> list[dict]:
    if feed_id not in FEEDS or not body or len(body) > MAX_BYTES or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', body, re.I):
        raise SourceError('xml-validation')
    try:
        root = ET.fromstring(body.decode('utf-8-sig', errors='strict'))
    except (ValueError, UnicodeError, ET.ParseError) as error:
        raise SourceError('xml-validation') from error
    stack, nodes = [(root, 1)], 0
    while stack:
        element, depth = stack.pop()
        nodes += 1
        if depth > 32 or nodes > 10_000:
            raise SourceError('xml-validation')
        stack.extend((child, depth + 1) for child in element)
    atom = root.tag == ATOM + 'feed'
    if atom:
        entries = root.findall(ATOM + 'entry')
    elif root.tag == 'rss' and (channel := root.find('channel')) is not None:
        entries = channel.findall('item')
    else:
        raise SourceError('xml-validation')
    if len(entries) > 1000:
        raise SourceError('xml-validation')
    items, seen = [], set()
    for row in entries:
        title = row.find(ATOM + 'title' if atom else 'title')
        raw_title = ''.join(title.itertext()) if title is not None else None
        if atom:
            links = row.findall(ATOM + 'link')
            selected = next((link for link in links if link.get('rel') in (None, 'alternate')), None)
            link = selected.get('href') if selected is not None else None
            date = row.findtext(ATOM + 'published')
            if date is None:
                date = row.findtext(ATOM + 'updated')
        else:
            link = row.findtext('link')
            date = row.findtext('pubDate')
            if date is None:
                date = row.findtext('{http://purl.org/dc/elements/1.1/}date')
        url = article_url(link, feed_id)
        item = {'title': headline(raw_title), 'url': url, 'publishedAt': publication(date, now)}
        if url not in seen:
            seen.add(url)
            items.append(item)
    items.sort(key=lambda item: (item['publishedAt'] or '', item['url']), reverse=True)
    return items[:MAX_ITEMS]


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError('redirect', code)


def retry_delay(headers: object, now: dt.datetime) -> float:
    raw = headers.get('Retry-After', '').strip()
    if not raw:
        return 1
    if re.fullmatch(r'\d{1,7}', raw):
        return float(raw)
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        return max(0, (parsed - now).total_seconds()) if parsed.tzinfo else float('inf')
    except (ValueError, TypeError, OverflowError):
        return float('inf')


def fetch_feed(feed_id: str, now: dt.datetime, opener=None, sleeper=time.sleep) -> list[dict]:
    opener = opener or urllib.request.build_opener(RejectRedirects())
    request = urllib.request.Request(FEEDS[feed_id][0], headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml',
        'Accept-Encoding': 'identity',
    })
    for attempt in range(2):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise SourceError('http-status', response.status)
                content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if content_type and content_type not in ('application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'):
                    raise SourceError('content-type', response.status)
                length = response.headers.get('Content-Length')
                if length and (not length.isdigit() or int(length) > MAX_BYTES):
                    raise SourceError('response-size', response.status)
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise SourceError('response-size', response.status)
            return parse_feed(feed_id, body, now)
        except urllib.error.HTTPError as error:
            delay = retry_delay(error.headers, dt.datetime.now(UTC))
            if attempt == 0 and 500 <= error.code <= 599 and delay <= MAX_RETRY_WAIT_SECONDS:
                error.close()
                sleeper(delay)
                continue
            error.close()
            raise SourceError('http-status', error.code) from None
        except (TimeoutError, socket.timeout):
            if attempt == 0:
                sleeper(1)
                continue
            raise SourceError('timeout') from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)) and attempt == 0:
                sleeper(1)
                continue
            raise SourceError('timeout' if isinstance(error.reason, (TimeoutError, socket.timeout)) else 'network') from None
    raise SourceError('network')


def empty_feed(feed_id: str) -> dict:
    return {'id': feed_id, 'feedUrl': FEEDS[feed_id][0], 'checkedAt': None,
            'lastAttemptAt': None, 'failed': True, 'items': []}


def validate_previous(value: object, now: dt.datetime) -> dict[str, dict]:
    if not isinstance(value, dict) or value.get('schemaVersion') != 1 or not isinstance(value.get('feeds'), list) or len(value['feeds']) != len(FEEDS):
        raise SourceError('previous-snapshot')
    read_timestamp(value.get('generatedAt'), now)
    result = {}
    for row in value['feeds']:
        if not isinstance(row, dict) or row.get('id') not in FEEDS or row['id'] in result or row.get('feedUrl') != FEEDS[row['id']][0] or type(row.get('failed')) is not bool:
            raise SourceError('previous-snapshot')
        checked = read_timestamp(row['checkedAt'], now) if row.get('checkedAt') is not None else None
        attempted = read_timestamp(row['lastAttemptAt'], now) if row.get('lastAttemptAt') is not None else None
        if (checked and (not attempted or checked > attempted)) or (not row['failed'] and (not checked or checked != attempted)):
            raise SourceError('previous-snapshot')
        items = row.get('items')
        if not isinstance(items, list) or len(items) > MAX_ITEMS or (items and not checked):
            raise SourceError('previous-snapshot')
        seen = set()
        for item in items:
            title = item.get('title') if isinstance(item, dict) else None
            if not isinstance(item, dict) or not isinstance(title, str) or not title or len(title) > 500 or title != title.strip() or re.search(r'[<>\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', title) or item.get('url') != article_url(item.get('url'), row['id'], decode=False) or item['url'] in seen:
                raise SourceError('previous-snapshot')
            seen.add(item['url'])
            if item.get('publishedAt') is not None:
                read_timestamp(item['publishedAt'], checked, 300)
        result[row['id']] = row
    return result


def refresh(previous: object, now: dt.datetime, fetcher=fetch_feed) -> dict:
    try:
        old = validate_previous(previous, now)
    except SourceError:
        old = {feed_id: empty_feed(feed_id) for feed_id in FEEDS}
    attempt_time = timestamp(now)

    def check(feed_id: str) -> dict:
        try:
            items = fetcher(feed_id, now)
            row = {**empty_feed(feed_id), 'checkedAt': attempt_time, 'lastAttemptAt': attempt_time,
                   'failed': False, 'items': items}
            print(json.dumps({'source': feed_id, 'status': 'ok', 'items': len(items)}), file=sys.stderr)
            return row
        except SourceError as error:
            print(json.dumps({'source': feed_id, 'status': 'failed', 'reason': error.reason,
                              **({'httpStatus': error.status} if error.status is not None else {})}), file=sys.stderr)
        except Exception:
            print(json.dumps({'source': feed_id, 'status': 'failed', 'reason': 'unknown'}), file=sys.stderr)
        return {**old[feed_id], 'lastAttemptAt': attempt_time, 'failed': True}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(check, FEEDS))
    result = {'schemaVersion': 1, 'generatedAt': attempt_time, 'feeds': rows}
    validate_previous(result, now)
    return result


def main() -> int:
    now = dt.datetime.now(UTC)
    try:
        raw = OUTPUT.read_bytes()
        previous = json.loads(raw) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError):
        previous = None
    result = refresh(previous, now)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temporary.replace(OUTPUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
