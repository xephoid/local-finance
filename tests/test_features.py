"""Synthetic usability/alert/retry fixtures; no live network or private data."""
import contextlib, io, pathlib, queue, tempfile, unittest
from unittest.mock import patch
import finance

class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=finance.Store(self.tmp.name)
    def fixture(self, amount='-12.34', pending=False):
        self.store.ingest({'accounts':[{'id':'synthetic','name':'Fixture <account>','currency':'USD','balance':'1234.56','balance-date':int(finance.time.time()),'transactions':[{'id':'synthetic-t','posted':int(finance.time.time()),'amount':amount,'pending':pending,'description':'Fixture <merchant>'}]}]})
    def test_alert_settings_require_explicit_threshold_approval_and_allow_disable(self):
        self.assertTrue(hasattr(self.store,'configure_alert'),'alert settings missing')
        self.assertEqual(self.store.rows('select * from alert_settings'),[])
        for kind,currency,value in [('low_balance','USD','100'),('large_transaction','USD','10'),('budget','USD','80'),('feed','','24')]:
            with self.assertRaises(ValueError):self.store.configure_alert(kind,currency,value,False)
            self.store.configure_alert(kind,currency,value,True)
        self.assertEqual(len(self.store.rows('select * from alert_settings')),4)
        for kind,currency,value in [('budget','USD','101'),('feed','','0'),('large_transaction','USD','0'),('low_balance','ZZZ','1')]:
            with self.assertRaises(ValueError):self.store.configure_alert(kind,currency,value,True)
        self.store.configure_alert('low_balance','USD','',False,disable=True)
        self.assertEqual(len(self.store.rows('select * from alert_settings')),3)
        page=finance.dashboard(self.store,'synthetic-csrf')
        self.assertIn('Alert settings',page);self.assertIn('I explicitly approve',page)
        self.assertIn('action="/alerts"',page)

    def test_alerts_private_details_generic_deduplicated_outbox(self):
        self.assertTrue(hasattr(finance,'evaluate_alerts'),'alert evaluation missing')
        self.fixture()
        self.assertEqual(finance.evaluate_alerts(self.store),[])
        self.assertEqual(self.store.rows('select * from outbox'),[])
        self.store.configure_alert('low_balance','USD','2000',True)
        self.store.configure_alert('large_transaction','USD','10',True)
        self.store.configure_alert('budget','USD','80',True)
        self.store.configure_alert('feed','','24',True)
        self.store.approve_budget('Uncategorized','USD','15',True)
        with self.store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES('sync_state','attention')")
        alerts=finance.evaluate_alerts(self.store)
        self.assertEqual({a['kind'] for a in alerts},{'low_balance','large_transaction','budget','feed'})
        self.assertIn('approaching',' '.join(a['detail'] for a in alerts))
        finance.evaluate_alerts(self.store)
        events=self.store.rows('select * from outbox')
        self.assertEqual(len(events),4)
        self.assertTrue(all(e['message']==finance.GENERIC_NOTICE and e['kind']=='alert' for e in events))
        self.assertNotIn('Fixture',str(events));self.assertNotIn('synthetic',str(events))
        self.store.approve_budget('Uncategorized','USD','10',True)
        self.assertIn('over',' '.join(a['detail'] for a in finance.evaluate_alerts(self.store)))
        self.assertEqual(len(self.store.rows('select * from outbox')),5)
        self.fixture(pending=True)
        self.assertNotIn('large_transaction',{a['kind'] for a in finance.evaluate_alerts(self.store)})
        page=finance.dashboard(self.store,'csrf')
        self.assertIn('Active alerts',page);self.assertIn('&lt;account&gt;',page)
        self.assertNotIn('<account>',page)

    def test_daily_retry_is_transient_only_spaced_persisted_and_max_three(self):
        stamp=finance.dt.datetime(2026,10,29,12,tzinfo=finance.dt.timezone.utc);calls=[]
        def transient(store,path):
            calls.append(True)
            with store.connect() as db:
                db.execute("INSERT OR REPLACE INTO meta VALUES('sync_retryable','1')")
                db.execute("INSERT OR REPLACE INTO meta VALUES('sync_state','attention')")
            return False
        finance.tick(self.store,'unused',stamp=stamp,sync_fn=transient)
        finance.tick(self.store,'unused',stamp=stamp,sync_fn=transient)
        self.assertEqual(len(calls),1)
        for hours in (1,2,3,4):finance.tick(self.store,'unused',stamp=stamp+finance.dt.timedelta(hours=hours),sync_fn=transient)
        self.assertEqual(len(calls),3,'transient failures need bounded retries')
        self.assertEqual(len(self.store.rows("select * from outbox where kind='sync_attention'")),1)
        with self.store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES('sync_retryable','0')")
        nextday=stamp+finance.dt.timedelta(days=1)
        def permanent(store,path):calls.append(True);return False
        finance.tick(self.store,'unused',stamp=nextday,sync_fn=permanent)
        finance.tick(self.store,'unused',stamp=nextday+finance.dt.timedelta(hours=2),sync_fn=permanent)
        self.assertEqual(len(calls),4)

    def test_sync_classifies_retryable_transport_only_without_raw_error(self):
        import urllib.error
        secret=self.store.root/'synthetic-secret'
        secret.write_text('https://fixture:fixture@bridge.simplefin.org/simplefin');secret.chmod(0o600)
        for error,retry in [(TimeoutError('SENSITIVE'), '1'),(urllib.error.HTTPError('https://fixture',503,'SENSITIVE',{},None),'1'),(urllib.error.HTTPError('https://fixture',403,'SENSITIVE',{},None),'0'),(ValueError('SENSITIVE'),'0')]:
            def fetch(*args,**kwargs):raise error
            self.assertFalse(finance.sync(self.store,secret,fetch=fetch))
            meta={r['key']:r['value'] for r in self.store.rows('select * from meta')}
            self.assertEqual(meta.get('sync_retryable'),retry,'transient classification missing')
            self.assertNotIn('SENSITIVE',str(meta))
        rate=urllib.error.HTTPError('https://fixture',429,'SENSITIVE',{'Retry-After':'7200'},None)
        def limited(*a,**k):raise rate
        self.assertFalse(finance.sync(self.store,secret,fetch=limited))
        meta={r['key']:r['value'] for r in self.store.rows('select * from meta')}
        self.assertGreater(finance.dt.datetime.fromisoformat(meta['sync_retry_after']),finance.dt.datetime.now(finance.dt.timezone.utc)+finance.dt.timedelta(minutes=119))

    def test_retry_after_survives_day_rollover(self):
        stamp=finance.dt.datetime(2026,10,29,23,tzinfo=finance.dt.timezone.utc)
        with self.store.connect() as db:
            for key,value in [('daily_attempt','2026-10-29'),('daily_count','1'),('daily_next',stamp.isoformat()),('sync_retryable','1'),('sync_retry_after',(stamp+finance.dt.timedelta(hours=4)).isoformat())]:db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,value))
        calls=[]
        finance.tick(self.store,'unused',stamp=stamp+finance.dt.timedelta(hours=2),sync_fn=lambda *a:calls.append(True))
        self.assertEqual(calls,[],'Retry-After must not be bypassed at midnight')

    def test_categorize_cli_drains_bounded_batches_without_bank_access(self):
        account={'id':'synthetic','currency':'USD','balance':'1','transactions':[{'id':str(i).zfill(3),'posted':1700000000,'amount':'-1','description':'Fixture merchant '+str(i)} for i in range(105)]}
        account['transactions'].append({'id':'pending','posted':1700000000,'amount':'-1','pending':True,'description':'Fixture pending'})
        self.store.ingest({'accounts':[account]})
        output=io.StringIO()
        args=['--data',str(self.store.root),'--credential','/NONEXISTENT-SYNTHETIC','categorize']
        with patch('finance.model_request',return_value='{"category":"Groceries"}') as model,patch('finance.sync',side_effect=AssertionError('Bank access forbidden')),contextlib.redirect_stdout(output):
            try:code=finance.main(args+['--batches','1'])
            except SystemExit:code=-1
            self.assertEqual(code,0,'standalone categorize CLI missing')
            self.assertEqual(model.call_count,50)
            self.assertEqual(finance.main(args),0)
            self.assertEqual(model.call_count,105)
        self.assertEqual(len(self.store.rows("select * from transactions where category='Uncategorized' and pending=0")),0)
        self.assertEqual(len(self.store.rows('select * from transactions where pending=1')),1)
        self.assertNotIn('merchant',output.getvalue());self.assertNotIn('105',output.getvalue())
        self.assertTrue(all(r['review'] and not r['excluded'] for r in self.store.rows('select * from transactions')))

    def test_categorize_does_not_apply_stale_suggestion_after_source_change(self):
        self.fixture()
        def changed(merchant):
            self.fixture(pending=True)
            return '{"category":"Groceries"}'
        finance.categorize(self.store,request=changed,seconds=1)
        self.assertEqual(self.store.rows('select category from transactions')[0]['category'],'Uncategorized','changed source must not get stale model suggestion')

    def test_categorize_does_not_apply_stale_merchant_suggestion(self):
        self.fixture()
        def changed(merchant):
            with self.store.connect() as db:db.execute("UPDATE transactions SET description='Fixture changed merchant'")
            return '{"category":"Groceries"}'
        finance.categorize(self.store,request=changed,seconds=1)
        self.assertEqual(self.store.rows('select category from transactions')[0]['category'],'Uncategorized')

    def test_backlog_continues_after_per_batch_deadline_within_total_budget(self):
        self.store.ingest({'accounts':[{'id':'synthetic','currency':'USD','balance':'1','transactions':[{'id':str(i).zfill(3),'posted':1700000000,'amount':'-1','description':'Fixture slow local merchant'} for i in range(105)]}]})
        clock=[0]
        def model(*a,**k):
            clock[0]+=3
            return '{"category":"Groceries"}'
        with patch('finance.time.monotonic',side_effect=lambda:clock[0]),patch('finance.model_request',side_effect=model):
            self.assertTrue(finance.categorize_backlog(self.store,batches=20,seconds=600))
        self.assertEqual(self.store.rows("select count(*) n from transactions where category='Uncategorized'")[0]['n'],0,'per-batch timeout must allow further bounded batches')
        self.assertEqual(self.store.status()['model_state'],'ok')

    def test_feed_staleness_and_disable_recompute_without_detailed_notifications(self):
        self.fixture();self.store.configure_alert('feed','','24',True)
        self.assertEqual(finance.evaluate_alerts(self.store),[])
        stamp=finance.dt.datetime.now(finance.dt.timezone.utc)+finance.dt.timedelta(hours=25)
        alerts=finance.evaluate_alerts(self.store,stamp=stamp)
        self.assertEqual(len(alerts),2)
        self.assertTrue(all(a['kind']=='feed' for a in alerts))
        self.store.configure_alert('feed','','',False,disable=True)
        self.assertEqual(finance.evaluate_alerts(self.store,stamp=stamp),[])
        self.assertTrue(all(e['message']==finance.GENERIC_NOTICE for e in self.store.rows('select * from outbox')))

    def test_backlog_unknown_model_category_does_not_spin_or_skip_later_candidates(self):
        self.store.ingest({'accounts':[{'id':'synthetic','currency':'USD','balance':'1','transactions':[{'id':str(i).zfill(3),'posted':1700000000,'amount':'-1','description':'Fixture merchant'} for i in range(55)]}]})
        with patch('finance.model_request',return_value='{"category":"Uncategorized"}') as request:
            self.assertTrue(finance.categorize_backlog(self.store))
        self.assertEqual(request.call_count,55)
        self.assertEqual(self.store.status()['model_state'],'deferred')
        with patch('finance.model_request',side_effect=RuntimeError('SENSITIVE')):
            self.assertFalse(finance.categorize_backlog(self.store))
        self.assertEqual(self.store.status()['model_state'],'unavailable')
        self.assertNotIn('SENSITIVE',str(self.store.status()))
        for batches,seconds in [(0,600),(101,600),(1,0),(1,3601)]:
            with self.assertRaises(ValueError):finance.categorize_backlog(self.store,batches,seconds)

    def test_exact_major_money_and_human_reports(self):
        self.assertTrue(hasattr(finance,'money'),'exact money formatter missing')
        self.assertEqual(finance.money(-123456,'USD'),'USD -1,234.56')
        self.assertEqual(finance.money(1234,'KWD'),'KWD 1.234')
        self.assertEqual(finance.money(1234,'JPY'),'JPY 1,234')
        self.assertEqual(finance.money(9000000000000000,'USD'),'USD 90,000,000,000,000.00')
        with self.assertRaises(ValueError):finance.money(1,'ZZZ')
        self.fixture();self.store.approve_budget('Groceries','USD','50',True)
        page=finance.dashboard(self.store,'synthetic-csrf')
        for text in ('USD 1,234.56','USD -12.34','USD 50.00','Net outflow','USD 12.34'):
            self.assertIn(text,page)
        self.assertNotIn('integer minor units',page)
        self.assertNotIn('&quot;net_outflow&quot;',page)
