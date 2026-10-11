import unittest,importlib.util,pathlib,datetime as dt,io,zipfile,xml.sax.saxutils as esc
spec=importlib.util.spec_from_file_location('publisher',pathlib.Path(__file__).with_name('refresh-world-bank.py'));p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
URL='https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx'
NOW=dt.datetime(2026,10,12,tzinfo=dt.timezone.utc)
def book(change=None):
 strings=['World Bank Commodity Price Data (The Pink Sheet)','monthly prices in nominal US dollars']+[s['header']for s in p.SPECS]+[s['sheetUnit']for s in p.SPECS]
 def sharedcell(address,index):return f'<c r="{address}" t="s"><v>{index}</v></c>'
 def col(i):return chr(66+i)
 rows=['<row>'+sharedcell('A1',0)+'</row>','<row>'+sharedcell('A2',1)+'</row>','<row>'+''.join(sharedcell(col(i)+'5',2+i)for i in range(15))+'</row>','<row>'+''.join(sharedcell(col(i)+'6',17+i)for i in range(15))+'</row>']
 for i in range(45):
  month=2023*12+i;period=f'{month//12}M{month%12+1:02}';strings.append(period);row=7+i;rows.append('<row>'+sharedcell(f'A{row}',len(strings)-1)+''.join(f'<c r="{col(j)}{row}"><v>{100+i+j}</v></c>'for j in range(15))+'</row>')
 files={'xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'+''.join('<si><t>'+esc.escape(s)+'</t></si>'for s in strings)+'</sst>','xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Monthly Prices" r:id="rId1"/></sheets></workbook>','xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="rId1" Target="worksheets/sheet2.xml"/></Relationships>','xl/worksheets/sheet2.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(rows)+'</sheetData></worksheet>'}
 if change:change(files)
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED)as z:
  for key,value in files.items():z.writestr(key,value)
 return out.getvalue()
class PublisherTests(unittest.TestCase):
 def test_actual_schema_and_original_months(self):
  x=p.normalize(book(),URL,NOW);self.assertEqual(len(x['series']),15);self.assertEqual(x['series'][0]['observations'][-1]['date'],'2026-09-01');self.assertEqual(x['sourceCheckedAt'],'2026-10-12T00:00:00.000Z');self.assertEqual([r['id']for r in x['series']],[s['id']for s in p.SPECS])
 def test_discovery_rejects_archives_and_multiple_monthly_workbooks(self):
  self.assertEqual(p.discover(f'<a href="{URL}">Monthly</a>'.encode()),URL)
  with self.assertRaises(p.SourceError):p.discover(b'<a href="https://other.example/file.xlsx">Monthly</a>')
  with self.assertRaises(p.SourceError):p.discover((URL+' '+URL.replace('0050012026','0050012025')).encode())
 def test_wrong_source_url_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(),'https://evil.example/file.xlsx',NOW)
 def test_wrong_units_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/sharedStrings.xml':f['xl/sharedStrings.xml'].replace('($/bbl)','wrong-unit')})),URL,NOW)
 def test_duplicate_dates_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/sharedStrings.xml':f['xl/sharedStrings.xml'].replace('2026M09','2026M08')})),URL,NOW)
 def test_future_or_incomplete_month_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/sharedStrings.xml':f['xl/sharedStrings.xml'].replace('2026M09','2026M10')})),URL,NOW)
 def test_stale_tail_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(),URL,dt.datetime(2027,2,1,tzinfo=dt.timezone.utc))
 def test_nan_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/worksheets/sheet2.xml':f['xl/worksheets/sheet2.xml'].replace('<v>100</v>','<v>NaN</v>')})),URL,NOW)
 def test_nonenergy_negative_price_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/worksheets/sheet2.xml':f['xl/worksheets/sheet2.xml'].replace('<c r="E7"><v>103</v>','<c r="E7"><v>-3</v>')})),URL,NOW)
 def test_explicit_missing_observation_not_filled(self):
  x=p.normalize(book(lambda f:f.update({'xl/worksheets/sheet2.xml':f['xl/worksheets/sheet2.xml'].replace('<c r="B7"><v>100</v>','<c r="B7"><v>…</v>')})),URL,NOW);self.assertEqual(len(x['series'][0]['observations']),44)
 def test_formula_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/worksheets/sheet2.xml':f['xl/worksheets/sheet2.xml'].replace('<v>100</v>','<f>1+1</f><v>100</v>')})),URL,NOW)
 def test_dtd_and_external_entities_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/sharedStrings.xml':'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+f['xl/sharedStrings.xml']})),URL,NOW)
 def test_external_sheet_relationship_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/_rels/workbook.xml.rels':f['xl/_rels/workbook.xml.rels'].replace('Id="rId1"','Id="rId1" TargetMode="External"')})),URL,NOW)
 def test_wrong_sheet_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(book(lambda f:f.update({'xl/workbook.xml':f['xl/workbook.xml'].replace('Monthly Prices','Annual Prices')})),URL,NOW)
 def test_bad_zip_rejected(self):
  with self.assertRaises(p.SourceError):p.normalize(b'x'*101,URL,NOW)
if __name__=='__main__':unittest.main()
