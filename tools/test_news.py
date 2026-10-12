import contextlib
import datetime as dt
import importlib.util
import io
import json
import pathlib
import socket
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location('news_publisher', pathlib.Path(__file__).with_name('refresh-news.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
NOW = dt.datetime(2026, 10, 12, 13, 30, tzinfo=dt.timezone.utc)


def rss(title='Energy &amp; growth', link='https://www.eia.gov/pressroom/releases/a.php', date='Mon, 12 Oct 2026 08:00:00 EDT'):
    return f'<rss><channel><item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()


def atom(date='2026-10-02T07:51:08.289-04:00'):
    return f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Employment situation</title><link rel="self" href="https://www.bls.gov/feed/empsit.rss"/><link rel="alternate" href="https://www.bls.gov/news.release/archives/empsit_10022026.htm"/><updated>{date}</updated></entry></feed>'.encode()


class Response:
    def __init__(self, body, headers=None, status=200):
        self.body, self.headers, self.status = body, headers or {'Content-Type': 'application/rss+xml'}, status
        self.read_max = None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, maximum):
        self.read_max = maximum
        return self.body[:maximum]


class Opener:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class GovernmentNewsTests(unittest.TestCase):
    def test_bls_atom_original_link_and_publication_time(self):
        result = p.parse_feed('bls-jobs', atom(), NOW)
        self.assertEqual(result, [{'title': 'Employment situation', 'url': 'https://www.bls.gov/news.release/archives/empsit_10022026.htm', 'publishedAt': '2026-10-02T11:51:08.289Z'}])

    def test_eia_relative_link_and_est_edt_offsets(self):
        self.assertEqual(p.parse_feed('eia', rss(link='/pressroom/releases/a.php'), NOW)[0]['url'], 'https://www.eia.gov/pressroom/releases/a.php')
        self.assertEqual(p.parse_feed('eia', rss(), NOW)[0]['publishedAt'], '2026-10-12T12:00:00.000Z')
        self.assertEqual(p.parse_feed('eia', rss(date='Mon, 12 Oct 2026 08:00:00 EST'), NOW)[0]['publishedAt'], '2026-10-12T13:00:00.000Z')

    def test_bls_variable_fraction_precision_keeps_exact_instants(self):
        for fraction, milliseconds in [('2', '200'), ('21', '210'), ('213', '213')]:
            with self.subTest(fraction=fraction):
                result = p.parse_feed('bls-cpi', atom(f'2026-08-12T07:51:16.{fraction}-04:00'), NOW)
                self.assertEqual(result[0]['publishedAt'], f'2026-08-12T11:51:16.{milliseconds}Z')

    def test_headlines_plain_and_no_inferred_dates(self):
        result = p.parse_feed('eia', rss(title='<![CDATA[<script>bad()</script><b>Energy</b> &amp; markets \u202e]]>', date=''), NOW)
        self.assertEqual(result[0]['title'], 'Energy & markets')
        self.assertIsNone(result[0]['publishedAt'])

    def test_bad_future_or_ambiguous_dates_fail_entire_feed(self):
        for date in ['Fri, 09 Oct 09:00:00 EST', '################# EST', '2026-10-12T18:00:00Z', '2026-02-30T12:00:00Z', 'Mon, 12 Oct 2026 08:00:00 CST', 'tomorrow']:
            with self.subTest(date=date), self.assertRaises(p.SourceError):
                p.parse_feed('eia', rss(date=date), NOW)

    def test_unsafe_xml_urls_and_root_rejected(self):
        bodies = [b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + rss(), b'<!ENTITY x "x">' + rss(), b'<rss>', b'<html>Denied</html>', b'x' * (p.MAX_BYTES + 1), b'<rss><channel>' + b'<a>' * 33 + b'</a>' * 33 + b'</channel></rss>']
        for body in bodies:
            with self.subTest(body=body[:30]), self.assertRaises(p.SourceError):
                p.parse_feed('eia', body, NOW)
        for url in ['http://www.eia.gov/pressroom/a', 'https://eia.gov.evil.test/a', '//evil.test/a', 'https://user:password@www.eia.gov/a', 'https://www.eia.gov:8443/a', '/other/a', 'https://127.0.0.1/a']:
            with self.subTest(url=url), self.assertRaises(p.SourceError):
                p.parse_feed('eia', rss(link=url), NOW)

    def test_duplicate_tracking_links_and_large_lists_bounded(self):
        entries = ''.join(f'<item><title>Headline {i}</title><link>https://www.eia.gov/pressroom/{i}</link><pubDate>Mon, 12 Oct 2026 08:00:00 EDT</pubDate></item>' for i in range(60))
        entries += '<item><title>Duplicate</title><link>https://www.eia.gov/pressroom/1?utm_source=rss#x</link></item>'
        result = p.parse_feed('eia', f'<rss><channel>{entries}</channel></rss>'.encode(), NOW)
        self.assertEqual(len(result), 50)
        self.assertEqual(len({item['url'] for item in result}), 50)

    def test_transport_fixed_url_truthful_identity_and_bounded_body(self):
        response = Response(atom())
        opener = Opener([response])
        p.fetch_feed('bls-cpi', NOW, opener=opener)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, p.FEEDS['bls-cpi'][0])
        self.assertIn('HorizonTerminal', request.get_header('User-agent'))
        self.assertEqual(timeout, 30)
        self.assertEqual(response.read_max, p.MAX_BYTES + 1)

    def test_redirects_403_content_types_and_oversize_do_not_retry(self):
        denial = urllib.error.HTTPError(p.FEEDS['bls-cpi'][0], 403, 'sensitive upstream denial', {}, io.BytesIO())
        cases = [denial, p.SourceError('redirect', 302), Response(b'<html/>', {'Content-Type': 'text/html'}), Response(atom(), {'Content-Length': str(p.MAX_BYTES + 1)}), Response(b'x' * (p.MAX_BYTES + 1))]
        for outcome in cases:
            opener = Opener([outcome])
            with self.subTest(outcome=type(outcome).__name__), self.assertRaises(p.SourceError):
                p.fetch_feed('bls-cpi', NOW, opener=opener)
            self.assertEqual(len(opener.calls), 1)
        with self.assertRaises(p.SourceError):
            p.RejectRedirects().redirect_request(None, None, 302, '', {}, 'https://evil.test/')

    def test_one_retry_for_timeouts_and_5xx_respects_retry_after(self):
        for failure, delay in [(socket.timeout(), 1), (urllib.error.HTTPError(p.FEEDS['bls-cpi'][0], 503, 'unavailable', {'Retry-After': '2'}, io.BytesIO()), 2)]:
            opener, waits = Opener([failure, Response(atom())]), []
            self.assertEqual(len(p.fetch_feed('bls-cpi', NOW, opener=opener, sleeper=waits.append)), 1)
            self.assertEqual(waits, [delay])
            self.assertEqual(len(opener.calls), 2)
        opener = Opener([socket.timeout(), socket.timeout()])
        with self.assertRaises(p.SourceError):
            p.fetch_feed('bls-cpi', NOW, opener=opener, sleeper=lambda _: None)
        self.assertEqual(len(opener.calls), 2)
        opener = Opener([urllib.error.HTTPError(p.FEEDS['bls-cpi'][0], 503, 'unavailable', {'Retry-After': '3600'}, io.BytesIO())])
        with self.assertRaises(p.SourceError):
            p.fetch_feed('bls-cpi', NOW, opener=opener, sleeper=lambda _: self.fail('Must not retry before Retry-After'))
        self.assertEqual(len(opener.calls), 1)

    def test_retained_success_dates_and_items_survive_failed_checks(self):
        with contextlib.redirect_stderr(io.StringIO()):
            initial = p.refresh(None, NOW, fetcher=lambda feed_id, now: p.parse_feed(feed_id, rss() if feed_id == 'eia' else atom(), now))
            def fail(feed_id, now):
                raise p.SourceError('http-status', 403)
            later = NOW + dt.timedelta(hours=14)
            result = p.refresh(initial, later, fetcher=fail)
        self.assertEqual(result['generatedAt'], p.timestamp(later))
        for row, before in zip(result['feeds'], initial['feeds']):
            self.assertTrue(row['failed'])
            self.assertEqual(row['items'], before['items'])
            self.assertEqual(row['checkedAt'], p.timestamp(NOW))
            self.assertEqual(row['lastAttemptAt'], p.timestamp(later))

    def test_previous_snapshot_untrusted_metadata_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            good = p.refresh(None, NOW, fetcher=lambda *_: [])
        for change in [lambda v: v['feeds'][0].update(feedUrl='https://evil.test/'), lambda v: v['feeds'][0].update(checkedAt='2099-01-01T00:00:00.000Z'), lambda v: v['feeds'][0].update(items=[{'title': '<script>x</script>', 'url': 'https://www.bls.gov/a', 'publishedAt': None}]), lambda v: v['feeds'].__setitem__(1, v['feeds'][0])]:
            value = json.loads(json.dumps(good))
            change(value)
            with self.assertRaises(p.SourceError):
                p.validate_previous(value, NOW)

    def test_failure_diagnostics_never_include_exception_or_response_text(self):
        def fail(*_):
            raise ValueError('private password URL response body')
        messages = io.StringIO()
        with contextlib.redirect_stderr(messages):
            result = p.refresh(None, NOW, fetcher=fail)
        self.assertNotIn('private', messages.getvalue())
        self.assertEqual(len(result['feeds']), 3)
        self.assertTrue(all(row['failed'] and row['checkedAt'] is None and not row['items'] for row in result['feeds']))


if __name__ == '__main__':
    unittest.main()
