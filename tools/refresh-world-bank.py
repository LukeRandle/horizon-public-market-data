"""Publish validated, attributed World Bank monthly commodity data. Standard library only."""
from __future__ import annotations
import argparse, datetime as dt, html, io, json, math, os, pathlib, re, urllib.request, zipfile
import xml.etree.ElementTree as ET
ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = 'https://www.worldbank.org/en/research/commodity-markets'
PATTERN = re.compile(r'^https://thedocs\.worldbank\.org/en/doc/[a-f0-9]{32}-[0-9]{10}/related/CMO-Historical-Data-Monthly\.xlsx$')
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
SPECS = json.loads((ROOT / 'tools/world-bank-series.json').read_text())
MAX_BYTES = 2_500_000
class SourceError(ValueError): pass
class FixedHostsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError('Source redirect rejected; review the new official source before changing the allowlist.')
def download(url: str) -> bytes:
    if url != PAGE and not PATTERN.fullmatch(url): raise SourceError('Unapproved commodity source URL.')
    request = urllib.request.Request(url, headers={'User-Agent': 'HorizonTerminal/1.0 private-commodity-research', 'Cache-Control':'no-cache', 'Accept':'text/html,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    with urllib.request.build_opener(FixedHostsRedirect()).open(request, timeout=30) as response:
        if response.status != 200: raise SourceError('Official source did not return HTTP 200.')
        value = response.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES: raise SourceError('Official source exceeds size limit.')
        return value
def discover(page: bytes) -> str:
    if len(page) > MAX_BYTES: raise SourceError('Oversized source page.')
    links = {html.unescape(value) for value in re.findall(r'https://[^\s"<>]+\.xlsx', page.decode('utf-8'))}
    matches = sorted(value for value in links if PATTERN.fullmatch(value))
    if len(matches) != 1: raise SourceError('Current source page must identify exactly one monthly workbook.')
    return matches[0]
def xml(data: bytes):
    if len(data) > 15_000_000 or re.search(br'<!\s*(DOCTYPE|ENTITY)', data, re.I): raise SourceError('Unsafe workbook XML.')
    return ET.fromstring(data)
def normalize(book: bytes, source_url: str, checked: dt.datetime) -> dict:
    if not PATTERN.fullmatch(source_url): raise SourceError('Unapproved workbook URL.')
    if len(book) > MAX_BYTES or len(book) < 100: raise SourceError('Invalid workbook size.')
    try:
        z = zipfile.ZipFile(io.BytesIO(book))
        infos = z.infolist()
        if len(infos) > 100 or sum(item.file_size for item in infos) > 30_000_000 or len({item.filename for item in infos}) != len(infos): raise SourceError('Unsafe workbook archive.')
        if any(item.flag_bits & 1 or '..' in pathlib.PurePosixPath(item.filename).parts or item.filename.startswith('/') for item in infos): raise SourceError('Unsafe workbook archive path.')
        shared = xml(z.read('xl/sharedStrings.xml'))
        strings = [''.join(node.itertext()) for node in shared.findall('s:si', NS)]
        workbook = xml(z.read('xl/workbook.xml'))
        sheets = workbook.find('s:sheets', NS)
        if sheets is None: raise SourceError('Workbook sheet catalogue is missing.')
        candidates = [sheet for sheet in sheets if sheet.attrib.get('name') == 'Monthly Prices']
        if len(candidates) != 1: raise SourceError('Monthly Prices sheet is missing or ambiguous.')
        rid = candidates[0].attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        relationships = xml(z.read('xl/_rels/workbook.xml.rels'))
        targets = [row.attrib['Target'] for row in relationships if row.attrib.get('Id') == rid and row.attrib.get('TargetMode') != 'External']
        if len(targets) != 1 or not re.fullmatch(r'worksheets/sheet\d+\.xml', targets[0]): raise SourceError('Invalid sheet relationship.')
        data = xml(z.read('xl/' + targets[0]))
        rows = []
        for row in data.findall('s:sheetData/s:row', NS):
            values = {}
            for cell in row:
                if cell.tag.rsplit('}',1)[-1] != 'c': continue
                address = cell.attrib.get('r','')
                column = re.sub(r'\d', '', address)
                value = cell.find('s:v', NS)
                if cell.find('s:f',NS) is not None: raise SourceError('Formula cells are not accepted as primary observations.')
                if value is None: continue
                text = value.text or ''
                if cell.attrib.get('t') == 's':
                    if not text.isdigit() or int(text) >= len(strings): raise SourceError('Invalid shared-string reference.')
                    text = strings[int(text)]
                if column in values: raise SourceError('Duplicate sheet cell.')
                values[column] = text.strip()
            rows.append(values)
    except (KeyError, zipfile.BadZipFile, ET.ParseError, UnicodeError) as error:
        raise SourceError('Malformed workbook.') from error
    title = ' '.join(' '.join(row.values()) for row in rows[:4])
    if 'World Bank Commodity Price Data' not in title or 'nominal us dollars' not in title.lower(): raise SourceError('Wrong commodity workbook.')
    headers = [row for row in rows if sum(spec['header'] in row.values() for spec in SPECS) == len(SPECS)]
    if len(headers) != 1: raise SourceError('Commodity headers are missing or ambiguous.')
    header = headers[0]; header_index = rows.index(header)
    if header_index + 1 >= len(rows): raise SourceError('Commodity units missing.')
    units = rows[header_index + 1]; columns = {}
    for spec in SPECS:
        matches = [column for column,value in header.items() if value == spec['header']]
        if len(matches) != 1 or units.get(matches[0]) != spec['sheetUnit']: raise SourceError('Wrong or ambiguous commodity units: ' + spec['id'])
        columns[spec['id']] = matches[0]
    observations = {spec['id']: [] for spec in SPECS}; prior = ''
    for row in rows[header_index + 2:]:
        period = row.get('A','')
        if not period: continue
        if not re.fullmatch(r'\d{4}M(?:0[1-9]|1[0-2])', period): raise SourceError('Unexpected commodity reference period.')
        date = period[:4] + '-' + period[5:7] + '-01'
        if date < '1900-01-01': raise SourceError('Reference period predates the supported history.')
        if date <= prior: raise SourceError('Commodity periods must be unique and ascending.')
        prior = date
        if date >= checked.strftime('%Y-%m-01'): raise SourceError('Incomplete or future monthly average.')
        for spec in SPECS:
            raw = row.get(columns[spec['id']], '')
            if raw in ('', '…', '..', '...', 'NA', 'N/A'): continue
            try: value = float(raw)
            except ValueError as error: raise SourceError('Invalid commodity number.') from error
            energy = spec['id'] in ['WB_WTI','WB_BRENT','WB_GAS_US']
            if not math.isfinite(value) or value > (1e6 if energy else 1e8) or value < -1000 or not energy and value <= 0: raise SourceError('Commodity value outside bounds.')
            observations[spec['id']].append({'date': date, 'value': value})
    cutoff_index = checked.year * 12 + checked.month - 1 - 3
    cutoff = f'{cutoff_index // 12:04}-{cutoff_index % 12 + 1:02}-01'
    series = []
    for spec in SPECS:
        points = observations[spec['id']][-720:]
        if len(points) < 40 or points[-1]['date'] < cutoff: raise SourceError('Missing or stale monthly commodity observations: '+spec['id'])
        series.append({'id':spec['id'], 'observations': points})
    return {'schemaVersion':1,'sourceUrl':source_url,'sourceCheckedAt':checked.astimezone(dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),'series':series}
def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--workbook');parser.add_argument('--source-url');parser.add_argument('--output',default=str(ROOT/'data/world-bank-cycles.json'));args=parser.parse_args()
    if args.workbook:
        file=pathlib.Path(args.workbook);checked=dt.datetime.fromtimestamp(file.stat().st_mtime,dt.timezone.utc);url=args.source_url
        if not url: raise SourceError('Local workbook verification requires its actual source URL.')
        result=normalize(file.read_bytes(),url,checked)
    else:
        url=discover(download(PAGE));book=download(url);checked=dt.datetime.now(dt.timezone.utc);result=normalize(book,url,checked)
    target=pathlib.Path(args.output)
    if target.exists():
        previous=json.loads(target.read_text())
        old={row['id']:row['observations'][-1]['date'] for row in previous['series']}
        if any(row['observations'][-1]['date'] < old.get(row['id'],'') for row in result['series']): raise SourceError('A source cannot replace newer cached commodity history with an older publication.')
    target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(result,separators=(',',':'))+'\n');os.replace(temporary,target)
    print(f'Validated {len(result["series"])} World Bank monthly series; latest {result["series"][0]["observations"][-1]["date"]}; source checked {result["sourceCheckedAt"]}.')
if __name__ == '__main__': main()
