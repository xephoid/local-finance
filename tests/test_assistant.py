"""Synthetic-only assistant fixtures; no live bank/model requests."""
import json, pathlib, sqlite3, tempfile, unittest
import finance
LOCAL_METADATA={'details':{'family':'qwen'},'capabilities':['completion'],'model_info':{'general.architecture':'qwen'}}

class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=finance.Store(self.tmp.name)
    def fixture(self):
        self.payload={'accounts':[{'id':'synthetic','name':'Synthetic account','currency':'USD','balance':'100','transactions':[{'id':'one','posted':1700000000,'amount':'-12','description':'Synthetic shop'},{'id':'two','posted':1700000000,'amount':'-3','description':'Synthetic shop'}]}]}
        self.store.ingest(self.payload)
        return self.store.rows('select * from transactions order by id')[0]
    def test_registry_migration_preserves_legacy_rows_and_stable_ids(self):
        row=self.fixture(); self.store.correct(row['account'],row['id'],'Shopping',False,True)
        self.store.approve_budget('Shopping','USD','20',True)
        # Simulate pre-feature database using a separate truly legacy schema.
        legacy=pathlib.Path(self.tmp.name)/'legacy'; legacy.mkdir()
        db=sqlite3.connect(legacy/'ledger.sqlite3')
        db.executescript("CREATE TABLE transactions(account TEXT,id TEXT,day TEXT,amount INTEGER,description TEXT,pending INTEGER,category TEXT,review TEXT,excluded INTEGER,reviewed INTEGER,PRIMARY KEY(account,id)); INSERT INTO transactions VALUES('fixture','t','2023-11-14',-1200,'Synthetic',0,'Legacy custom','',0,1);")
        db.commit(); db.close()
        migrated=finance.Store(legacy)
        self.assertTrue(hasattr(migrated,'categories'),'category registry missing')
        preserved=migrated.rows('select * from transactions')[0]
        self.assertEqual(preserved['amount'],-1200)
        self.assertEqual(preserved['category'],'Legacy custom')
        ident=preserved['category_id']; self.assertTrue(ident)
        migrated.rename_category(ident,'Renamed custom')
        self.assertEqual(migrated.rows('select category_id,category from transactions')[0],{'category_id':ident,'category':'Renamed custom'})
        finance.Store(legacy)
        self.assertEqual(len(migrated.categories()),len(set(c['id'] for c in migrated.categories())))
        cid=self.store.add_category('Pets')
        self.store.correct(row['account'],row['id'],cid,False,True)
        self.store.approve_budget(cid,'USD','25',True)
        self.store.rename_category(cid,'Pet care')
        self.assertEqual(self.store.rows('select category from rules')[0]['category'],'Pet care')
        self.assertEqual(self.store.rows('select category from budgets where category_id=?',(cid,))[0]['category'],'Pet care')
        with self.assertRaises(ValueError): self.store.merge_category(cid,cid)
        with self.assertRaises(ValueError): self.store.merge_category(cid,self.store.category_id('Shopping'))
        self.assertIn('Pet care',finance.dashboard(self.store,'synthetic'))

    def test_review_defer_confirm_undo_conflict_examples_and_source_reset(self):
        self.fixture(); session={}
        self.assertTrue(hasattr(self.store,'review_next'),'one-item review missing')
        first=self.store.review_next(session)
        self.store.review_action(session,first['account'],first['id'],first['version'],'defer')
        second=self.store.review_next(session)
        self.assertNotEqual(first['id'],second['id'])
        cid=self.store.add_category('Pets')
        self.store.review_action(session,second['account'],second['id'],second['version'],'save',cid,False,True)
        self.assertIsNone(self.store.review_next(session))
        self.assertEqual(self.store.examples('Synthetic shop')[0]['category_id'],cid)
        self.assertEqual(self.store.rows('select provenance from transactions where id=?',(second['id'],))[0]['provenance'],'human')
        self.store.undo_review(session)
        self.assertEqual(self.store.examples('Synthetic shop'),[])
        self.assertEqual(self.store.rows('select * from rules'),[])
        item=self.store.review_next(session)
        self.store.review_action(session,item['account'],item['id'],item['version'],'save','Transfer',False,False)
        self.assertEqual(self.store.report('2023-01-01','2024-01-01')['USD']['net_outflow'],1500)
        self.store.correct(item['account'],item['id'],'Shopping',False)
        with self.assertRaises(ValueError): self.store.undo_review(session)
        self.payload['accounts'][0]['transactions'][0]['amount']='-99'
        self.store.ingest(self.payload)
        changed=self.store.rows("select * from transactions where id='one'")[0]
        self.assertFalse(changed['reviewed']); self.assertFalse(changed['excluded'])
        with self.assertRaises(ValueError): self.store.review_action({},first['account'],first['id'],first['version'],'save','Other')

    def test_model_uses_registry_and_confirmed_examples_never_guesses(self):
        from unittest.mock import patch
        row=self.fixture(); cid=self.store.add_category('Pets')
        self.store.correct(row['account'],row['id'],cid,False)
        seen=[]
        def fetch(req,timeout,max_bytes):
            if req.full_url.endswith('/api/show'):return LOCAL_METADATA
            self.assertEqual(max_bytes,65536)
            seen.append(json.loads(req.data))
            return {'message':{'content':'{"category":"Pets"}'}}
        with patch('finance.fetch_json',side_effect=fetch):
            self.assertEqual(finance.categorize(self.store,seconds=1),1)
        self.assertIn('Pets',seen[0]['format']['properties']['category']['enum'])
        data=json.loads(seen[0]['messages'][-1]['content'])
        self.assertEqual(data['confirmed_examples'][0]['category_id'],cid)
        tx=self.store.rows("select * from transactions where id='two'")[0]
        self.assertEqual(tx['provenance'],'model'); self.assertFalse(tx['reviewed'])
        self.assertEqual(len(self.store.examples('Synthetic shop')),1)
        with self.assertRaises(ValueError):finance.validate_category('{"category":"Pets","sql":"DELETE"}',self.store)
        with self.assertRaises(ValueError):finance.validate_category('{"category":"Pets","category":"Pets"}',self.store)
        self.store.correct(tx['account'],tx['id'],'Uncategorized',False)
        with patch('finance.model_request',side_effect=AssertionError('Human decisions must not be overwritten')):
            self.assertEqual(finance.categorize(self.store,seconds=1),0)

    def test_chat_validated_read_only_queries_compute_facts_for_custom_categories(self):
        import assistant_features as af
        row=self.fixture(); cid=self.store.add_category('Pets'); self.store.correct(row['account'],row['id'],cid,False)
        self.store.approve_budget(cid,'USD','20',True)
        self.assertTrue(hasattr(af,'validate_plan'),'constrained query planner missing')
        plan={'op':'spending','start':'2023-11-01','end':'2023-12-01','category_id':cid,'currency':'USD'}
        before=self.store.rows('select * from transactions')
        answer=af.query_facts(self.store,af.validate_plan(json.dumps(plan),self.store))
        self.assertIn('USD 12.00',json.dumps(answer))
        self.assertIn('2023-11-01',json.dumps(answer)); self.assertIn('freshness',answer)
        self.assertIn('Synthetic shop',json.dumps(answer))
        for op in ('balances','freshness','spending','search','budget','compare','review'):
            p={'op':op} if op in ('balances','freshness') else dict(plan,op=op)
            if op=='compare':p.update(compare_start='2023-10-01',compare_end='2023-11-01')
            self.assertIn('cards',af.query_facts(self.store,af.validate_plan(json.dumps(p),self.store)))
        self.assertEqual(self.store.rows('select * from transactions'),before)
        for bad in [dict(plan,sql='DROP TABLE transactions'),dict(plan,op='shell'),dict(plan,category_id='not-real'),dict(plan,start='1900-01-01'),dict(plan,limit=1000),dict(plan,min_amount='NaN'),dict(plan,currency='ZZZ')]:
            with self.assertRaises(ValueError):af.validate_plan(json.dumps(bad),self.store)
        with self.assertRaises(ValueError):af.validate_plan('{"op":"balances","op":"search"}',self.store)
        # Merchant injection is inert parameterized text, not SQL or a command.
        p=af.validate_plan(json.dumps(dict(plan,op='search',merchant="' OR 1=1 --")),self.store)
        self.assertEqual(af.query_facts(self.store,p)['cards'][0]['rows'],[])

    def web(self):
        import http.client, ssl, threading, re, urllib.parse
        finance.initialize(self.store)
        server=finance.make_server(self.store,'127.0.0.1',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,fields=None,cookie=None):
            conn=http.client.HTTPSConnection('127.0.0.1',server.server_port,context=ssl._create_unverified_context(),timeout=4)
            headers={'Content-Type':'application/x-www-form-urlencoded','Origin':'https://127.0.0.1:'+str(server.server_port)}
            if cookie:headers['Cookie']=cookie
            try:
                conn.request(method,path,urllib.parse.urlencode(fields) if fields else None,headers)
                r=conn.getresponse();return r.status,dict(r.getheaders()),r.read().decode()
            finally:conn.close()
        _,h,b=request('GET','/'); pre=h['Set-Cookie'].split(';')[0];csrf=re.search('name="csrf" value="([^"]+)"',b)[1]
        _,h,_=request('POST','/login',{'csrf':csrf,'password':(self.store.root/'login_password').read_text().strip()},pre)
        cookie=h['Set-Cookie'].split(';')[0]
        _,_,b=request('GET','/',cookie=cookie);csrf=re.search('name="csrf" value="([^"]+)"',b)[1]
        return request,cookie,csrf

    def test_authenticated_review_and_category_routes(self):
        import re
        self.fixture();request,cookie,csrf=self.web()
        self.assertEqual(request('GET','/review')[0],401)
        status,_,page=request('GET','/review',cookie=cookie)
        self.assertEqual(status,200,'authenticated one-item page missing')
        self.assertIn('Categorize together',page);self.assertIn('Current',page);self.assertIn('Suggested',page)
        self.assertIn('Exact merchant',page);self.assertIn('progress',page.lower())
        self.assertNotIn('<script',page)
        fields={k:v for k,v in re.findall('name="([^"]+)" value="([^"]*)"',page) if k in ('csrf','account','id','version')}
        import html
        fields={k:html.unescape(v) for k,v in fields.items()};fields.update(action='save',category='Shopping')
        self.assertEqual(request('POST','/review',dict(fields,csrf='bad'),cookie)[0],403)
        self.assertEqual(request('POST','/review',fields,cookie)[0],303)
        self.assertEqual(request('POST','/undo',{'csrf':csrf},cookie)[0],303)
        self.assertEqual(request('POST','/category',{'csrf':csrf,'action':'add','label':'Pets'},cookie)[0],303)
        cid=self.store.category_id('Pets')
        self.assertEqual(request('POST','/category',{'csrf':csrf,'action':'rename','category_id':cid,'label':'Pet care'},cookie)[0],303)
        self.assertIn('Pet care',request('GET','/review',cookie=cookie)[2])
        self.assertEqual(request('POST','/category',{'csrf':'bad','action':'add','label':'Injected'},cookie)[0],403)

    def test_chat_session_only_clear_logout_expiry_and_does_not_freeze_dashboard(self):
        import threading,time
        from unittest.mock import patch
        self.fixture();request,cookie,csrf=self.web()
        self.assertEqual(request('GET','/ask')[0],401)
        self.assertEqual(request('GET','/ask',cookie=cookie)[0],200,'chat page missing')
        self.assertEqual(request('POST','/ask',{'csrf':'bad','question':'Synthetic?'},cookie)[0],403)
        entered=threading.Event();release=threading.Event();responses=[];payloads=[]
        def planner(payload):
            payloads.append(payload);entered.set();release.wait(3);return '{"op":"balances"}'
        with patch('assistant_features.plan_request',side_effect=planner):
            worker=threading.Thread(target=lambda:responses.append(request('POST','/ask',{'csrf':csrf,'question':'Synthetic <question> balances?'},cookie)))
            worker.start();self.assertTrue(entered.wait(2))
            self.assertEqual(request('GET','/',cookie=cookie)[0],200,'inference held global HTTP lock')
            release.set();worker.join(4);self.assertEqual(responses[0][0],303)
            page=request('GET','/ask',cookie=cookie)[2]
            self.assertIn('USD 100.00',page);self.assertIn('&lt;question&gt;',page);self.assertNotIn('<question>',page)
            self.assertEqual(request('POST','/ask',{'csrf':csrf,'question':'And freshness?'},cookie)[0],303)
            self.assertTrue(payloads[-1]['recent_context'])
        self.assertEqual(request('POST','/ask/clear',{'csrf':csrf},cookie)[0],303)
        self.assertNotIn('Synthetic &lt;question&gt;',request('GET','/ask',cookie=cookie)[2])
        with patch('assistant_features.plan_request',return_value='{"op":"balances"}'):
            request('POST','/ask',{'csrf':csrf,'question':'Synthetic private question'},cookie)
        monotonic=time.monotonic
        with patch('finance.time.monotonic',side_effect=lambda:monotonic()+30000):
            self.assertEqual(request('GET','/ask',cookie=cookie)[0],401)
        self.assertEqual(request('POST','/logout',{'csrf':csrf},cookie)[0],403)

    def test_undo_refuses_reconfirmation_and_rule_ABA(self):
        row=self.fixture();session={}
        self.store.review_action(session,row['account'],row['id'],row['version'],'save','Shopping',False,True)
        self.store.correct(row['account'],row['id'],'Shopping',False)
        with self.assertRaises(ValueError):self.store.undo_review(session)

    def test_query_validation_rejects_ignored_or_mistyped_fields(self):
        import assistant_features as af
        self.fixture()
        for p in [{'op':'balances','category_id':self.store.category_id('Shopping')},{'op':'freshness','merchant':'Synthetic'},{'op':'balances','start':'invalid'},{'op':'search','start':'2023-11-01','end':'2023-12-01','compare_start':'invalid'},{'op':[]},{'op':'balances','currency':[]}]:
            with self.assertRaises(ValueError):af.validate_plan(json.dumps(p),self.store)

    def test_transport_is_pinned_bounded_no_proxy_or_redirect(self):
        import assistant_features as af
        import urllib.request
        from unittest.mock import patch
        payload=af.chat_payload(self.store,'Synthetic balances?',[]);seen=[]
        def fetch(req,timeout,max_bytes):
            if req.full_url.endswith('/api/show'):return LOCAL_METADATA
            seen.append((req,json.loads(req.data),timeout,max_bytes))
            return {'message':{'content':'{"op":"balances"}'}}
        with patch('finance.fetch_json',side_effect=fetch):
            self.assertEqual(af.plan_request(payload),'{"op":"balances"}')
            with self.assertRaises(ValueError):af.plan_request(payload,endpoint='http://cloud.invalid')
        req,body,timeout,limit=seen[0]
        self.assertEqual(req.full_url,'http://localhost:11434/api/chat');self.assertEqual(body['model'],'qwen3:8b')
        self.assertEqual(timeout,15);self.assertEqual(limit,65536)
        with patch('finance.urllib.request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value=b'{}'
            self.assertEqual(finance.fetch_json(req,max_bytes=10),{})
            self.assertEqual(build.call_args.args[0].proxies,{})
            self.assertIsInstance(build.call_args.args[1],finance.NoRedirect)
        with self.assertRaises(ValueError):finance.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_on_demand_suggestion_is_unconfirmed_and_accept_is_explicit(self):
        from unittest.mock import patch
        import html,re
        self.fixture();request,cookie,csrf=self.web()
        page=request('GET','/review',cookie=cookie)[2]
        self.assertIn('Get local suggestion',page)
        row=self.store.review_next({})
        f={'csrf':csrf,'account':row['account'],'id':row['id'],'version':row['version']}
        with patch('finance.model_request',return_value='{"category":"Shopping"}'):
            self.assertEqual(request('POST','/review/suggest',dict(f,csrf='bad'),cookie)[0],403)
            self.assertEqual(request('POST','/review/suggest',f,cookie)[0],303)
        row=self.store.review_next({})
        self.assertEqual(row['category'],'Shopping');self.assertEqual(row['provenance'],'model');self.assertFalse(row['reviewed'])
        self.assertEqual(self.store.examples('Synthetic shop'),[])
        f.update(version=row['version'],action='accept')
        self.assertEqual(request('POST','/review',f,cookie)[0],303)
        self.assertEqual(self.store.rows('select provenance from transactions where id=?',(row['id'],))[0]['provenance'],'human')

    def test_clear_discards_inflight_answer_and_logout_clears_history(self):
        import threading
        from unittest.mock import patch
        self.fixture();request,cookie,csrf=self.web()
        entered=threading.Event();release=threading.Event();responses=[]
        def planner(payload):entered.set();release.wait(3);return '{"op":"balances"}'
        with patch('assistant_features.plan_request',side_effect=planner):
            t=threading.Thread(target=lambda:responses.append(request('POST','/ask',{'csrf':csrf,'question':'Synthetic late question'},cookie)))
            t.start();self.assertTrue(entered.wait(2))
            self.assertEqual(request('POST','/ask/clear',{'csrf':csrf},cookie)[0],303)
            release.set();t.join(4)
            self.assertEqual(responses[0][0],409)
            self.assertNotIn('Synthetic late question',request('GET','/ask',cookie=cookie)[2])
        with patch('assistant_features.plan_request',return_value='{"op":"balances"}'):
            request('POST','/ask',{'csrf':csrf,'question':'Synthetic private question'},cookie)
        self.assertEqual(request('POST','/logout',{'csrf':csrf},cookie)[0],303)
        self.assertEqual(request('GET','/ask',cookie=cookie)[0],401)

    def test_deferred_item_does_not_recur_after_background_suggestion(self):
        row=self.fixture();session={}
        self.store.review_action(session,row['account'],row['id'],row['version'],'defer')
        with self.store.connect() as db:db.execute("UPDATE transactions SET category='Shopping',provenance='model' WHERE account=? AND id=?",(row['account'],row['id']))
        next_row=self.store.review_next(session)
        self.assertNotEqual(next_row['id'],row['id'])

    def test_examples_bound_merchant_text_and_model_response_size(self):
        from unittest.mock import patch
        row=self.fixture();self.store.correct(row['account'],row['id'],'Shopping',False)
        long='Synthetic '+('x'*2000)
        self.payload['accounts'][0]['transactions'][0]['description']=long;self.store.ingest(self.payload)
        self.store.correct(row['account'],row['id'],'Shopping',False)
        self.assertLessEqual(len(self.store.examples('Synthetic')[0]['description']),512)
        with self.assertRaises(ValueError):finance.validate_category(' '*9000+'{"category":"Shopping"}',self.store)

    def test_query_money_refunds_exclusions_date_bounds_freshness_and_comparison(self):
        import assistant_features as af
        row=self.fixture();self.store.correct(row['account'],row['id'],'Shopping',False)
        self.payload['accounts'][0]['transactions'] += [
            {'id':'refund','posted':1700000000,'amount':'2','description':'Synthetic refund'},
            {'id':'pending','posted':1700000000,'amount':'-100','description':'Synthetic pending','pending':True},
            {'id':'excluded','posted':1700000000,'amount':'-80','description':'Synthetic excluded'}]
        self.store.ingest(self.payload)
        for r in self.store.rows("select * from transactions where id!='one'"):
            self.store.correct(r['account'],r['id'],'Shopping',r['id']=='excluded')
        p=af.validate_plan(json.dumps({'op':'spending','start':'2023-11-01','end':'2023-12-01','currency':'USD'}),self.store)
        result=af.query_facts(self.store,p);data=result['cards'][0]['rows'][0]
        self.assertEqual(data['debits'],'USD 15.00');self.assertEqual(data['credits'],'USD 2.00');self.assertEqual(data['net_outflow'],'USD 13.00')
        self.assertIn('source_dates',result['freshness'])
        p.update(op='compare',compare_start='2023-10-01',compare_end='2023-11-01')
        self.assertEqual(af.query_facts(self.store,p)['cards'][0]['rows'][0]['change'],'USD 13.00')
        p=af.validate_plan(json.dumps({'op':'search','start':'2023-11-14','end':'2023-11-15','currency':'USD','min_amount':'-12','max_amount':'-12','limit':1}),self.store)
        self.assertEqual(len(af.query_facts(self.store,p)['cards'][0]['rows']),1)

    def test_worker_timeout_never_mutates_and_capacity_remains_bounded(self):
        import assistant_features as af
        import threading,time
        from unittest.mock import patch
        release=threading.Event(); calls=[]
        def blocked(payload):calls.append(True);release.wait(2);return '{"op":"balances"}'
        before=self.store.rows('select * from meta')
        with patch('assistant_features.plan_request',side_effect=blocked):
            try:
                for _ in range(2):
                    with self.assertRaises(ValueError):af.plan_with_deadline({'synthetic':True},seconds=.01)
                with self.assertRaises(ValueError):af.plan_with_deadline({'synthetic':True},seconds=.01)
                self.assertEqual(len(calls),2)
            finally:release.set()
            time.sleep(.03)
        self.assertEqual(self.store.rows('select * from meta'),before)

    def test_registry_merge_preserves_ids_rules_and_examples_and_rejects_builtin(self):
        row=self.fixture();a=self.store.add_category('Pets');b=self.store.add_category('Animals')
        self.store.correct(row['account'],row['id'],a,False,True)
        self.store.merge_category(a,b)
        self.assertEqual(self.store.rows('select category_id from rules')[0]['category_id'],b)
        self.assertEqual(self.store.examples('Synthetic shop')[0]['category_id'],b)
        self.store.rename_category(b,'Pet care')
        self.assertEqual(self.store.examples('Synthetic shop')[0]['label'],'Pet care')
        for value in ['Uncategorized','Transfer','Card payment']:
            cid=self.store.category_id(value)
            with self.assertRaises(ValueError):self.store.rename_category(cid,'Changed')
            with self.assertRaises(ValueError):self.store.merge_category(cid,b)
        with self.assertRaises(ValueError):self.store.add_category('pet CARE')

    def test_category_transport_bounds_and_escapes_new_category_labels(self):
        from unittest.mock import patch
        row=self.fixture();cid=self.store.add_category('<Pets>')
        self.store.correct(row['account'],row['id'],cid,False)
        self.assertNotIn('<Pets>',finance.dashboard(self.store,'synthetic'))
        self.assertIn('&lt;Pets&gt;',finance.dashboard(self.store,'synthetic'))
        with patch('finance.fetch_json',side_effect=lambda req,**kw: LOCAL_METADATA if req.full_url.endswith('/api/show') else {'message':{'content':'{"category":"Shopping"}'}}) as fetch:
            finance.model_request('Synthetic merchant')
            self.assertEqual(fetch.call_args.kwargs.get('max_bytes'),65536)
        with self.assertRaises(ValueError):finance.model_request('Synthetic',categories=['Synthetic '+str(i) for i in range(201)])

    def test_legacy_approved_rules_budgets_and_examples_survive_repeat_migration(self):
        legacy=pathlib.Path(self.tmp.name)/'old';legacy.mkdir()
        db=sqlite3.connect(legacy/'ledger.sqlite3')
        db.executescript("""CREATE TABLE transactions(account TEXT,id TEXT,day TEXT,amount INTEGER,description TEXT,pending INTEGER,category TEXT,review TEXT,excluded INTEGER,reviewed INTEGER,PRIMARY KEY(account,id));
          CREATE TABLE rules(description TEXT PRIMARY KEY,category TEXT);
          CREATE TABLE budgets(category TEXT,currency TEXT,amount INTEGER,approved TEXT,PRIMARY KEY(category,currency));
          INSERT INTO transactions VALUES('synthetic','one','2023-11-14',-1200,'Synthetic shop',0,'Shopping','',0,1);
          INSERT INTO rules VALUES('synthetic shop','Shopping');
          INSERT INTO budgets VALUES('Shopping','USD',2000,'synthetic-approval');""")
        db.commit();db.close()
        migrated=finance.Store(legacy)
        self.assertEqual(migrated.rows('select amount,approved from budgets')[0],{'amount':2000,'approved':'synthetic-approval'})
        self.assertEqual(migrated.rows('select description from rules')[0]['description'],'synthetic shop')
        self.assertEqual(len(migrated.examples('Synthetic shop')),1)
        before=migrated.rows('select * from transactions');finance.Store(legacy)
        self.assertEqual(migrated.rows('select * from transactions'),before)
        with migrated.connect() as db:db.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):finance.Store(legacy)

    def test_balance_limit_is_honored_and_readonly_relative_store_paths_work(self):
        import assistant_features as af
        self.fixture()
        self.store.ingest({'accounts':[{'id':'synthetic-second','name':'Synthetic second','currency':'JPY','balance':'200','transactions':[]}]})
        p=af.validate_plan('{"op":"balances","limit":1}',self.store)
        self.assertEqual(len(af.query_facts(self.store,p)['cards'][0]['rows']),1)
        import os
        self.store.dbpath=pathlib.Path(os.path.relpath(self.store.dbpath))
        self.assertEqual(len(af.query_facts(self.store,p)['cards'][0]['rows']),1)

    def test_budget_query_refuses_silent_truncation(self):
        import assistant_features as af,itertools
        self.store.add_category('Synthetic extra A');self.store.add_category('Synthetic extra B')
        pairs=list(itertools.islice(itertools.product([c['label'] for c in self.store.categories()],finance.EXPONENTS),501))
        self.assertEqual(len(pairs),501)
        with self.store.connect() as db:
            db.executemany('INSERT INTO budgets(category,currency,amount,approved) VALUES(?,?,100,?)',[(c,u,'synthetic approval') for c,u in pairs])
        p=af.validate_plan('{"op":"budget","start":"2023-11-01","end":"2023-12-01"}',self.store)
        with self.assertRaises(ValueError):af.query_facts(self.store,p)

    def test_comparison_refuses_truncated_previous_period(self):
        import assistant_features as af,itertools
        self.store.add_category('Synthetic extra A');self.store.add_category('Synthetic extra B')
        pairs=list(itertools.islice(itertools.product([c['label'] for c in self.store.categories()],finance.EXPONENTS),501))
        with self.store.connect() as db:
            for currency in finance.EXPONENTS:db.execute('INSERT INTO accounts VALUES(?,?,?,0,?,NULL)',(currency,'Synthetic '+currency,currency,finance.now()))
            db.executemany("INSERT INTO transactions(account,id,day,amount,description,pending,category) VALUES(?,?,'2023-10-15',-1,'Synthetic evidence',0,?)",[(currency,str(i),category) for i,(category,currency) in enumerate(pairs)])
        p=af.validate_plan('{"op":"compare","start":"2023-11-01","end":"2023-12-01","compare_start":"2023-10-01","compare_end":"2023-11-01"}',self.store)
        with self.assertRaises(ValueError):af.query_facts(self.store,p)

    def test_expired_session_history_is_purged_even_without_browser_request(self):
        import assistant_features as af,time
        from unittest.mock import patch
        self.fixture();request,cookie,csrf=self.web();captured=[]
        render=af.chat_page
        def capture(session):captured.append(session);return render(session)
        with patch('assistant_features.plan_request',return_value='{"op":"balances"}'):
            request('POST','/ask',{'csrf':csrf,'question':'Synthetic expiring question'},cookie)
        with patch('assistant_features.chat_page',side_effect=capture):request('GET','/ask',cookie=cookie)
        self.assertIn('chat',captured[0]);monotonic=time.monotonic
        with patch('finance.time.monotonic',side_effect=lambda:monotonic()+30000):time.sleep(.8)
        self.assertEqual(captured[0],{})

    def test_category_labels_cannot_impersonate_stable_ids(self):
        for label in ('builtin:Shopping','custom:synthetic','legacy:synthetic'):
            with self.assertRaises(ValueError):self.store.add_category(label)
        cid=self.store.add_category('Pets')
        with self.assertRaises(ValueError):self.store.rename_category(cid,'builtin:Shopping')
        self.assertIn('value="'+cid+'"',finance.choices('Pets',self.store))

    def test_merchant_names_aliases_are_independent_exact_and_source_safe(self):
        row=self.fixture()
        self.assertTrue(hasattr(self.store,'name_transaction'),'merchant identity workflow missing')
        self.store.name_transaction(row['account'],row['id'],row['version'],'Friendly shop')
        edited=self.store.rows("select * from transactions where id='one'")[0]
        self.assertEqual(edited['display_name'],'Friendly shop')
        self.assertEqual(edited['original_description'],'Synthetic shop')
        self.assertEqual(edited['description'],'Synthetic shop')
        self.assertFalse(edited['reviewed']);self.assertFalse(edited['excluded']);self.assertEqual(edited['category'],'Uncategorized')
        self.assertEqual(self.store.rows('select * from rules'),[])
        self.assertEqual(self.store.rows('select * from merchant_aliases'),[])
        with self.assertRaises(ValueError):self.store.name_transaction(row['account'],row['id'],edited['version'],'Friendly shop',reuse=True)
        self.store.name_transaction(row['account'],row['id'],edited['version'],'Friendly shop',confirm_identity=True,reuse=True)
        approved=self.store.rows("select * from transactions where id='one'")[0];identity=approved['merchant_id']
        self.assertTrue(identity)
        self.payload['accounts'][0]['transactions'] += [{'id':'new','posted':1700000000,'amount':'-1','description':' SYNTHETIC SHOP '},{'id':'ambiguous','posted':1700000000,'amount':'-1','description':'Synthetic shop variant'}]
        self.store.ingest(self.payload);self.store.ingest(self.payload)
        future=self.store.rows("select * from transactions where id='new'")[0]
        self.assertEqual(future['display_name'],'Friendly shop');self.assertEqual(future['merchant_id'],identity)
        self.assertEqual(future['category'],'Uncategorized');self.assertEqual(future['name_origin'],'alias')
        ambiguous=self.store.rows("select * from transactions where id='ambiguous'")[0]
        self.assertIsNone(ambiguous['merchant_id']);self.assertEqual(ambiguous['display_name'],'Synthetic shop variant')
        self.payload['accounts'][0]['transactions'][0]['description']='Synthetic changed source'
        self.payload['accounts'][0]['transactions'][2]['description']='Synthetic new source'
        self.store.ingest(self.payload)
        manual=self.store.rows("select * from transactions where id='one'")[0]
        automatic=self.store.rows("select * from transactions where id='new'")[0]
        self.assertEqual(manual['display_name'],'Friendly shop');self.assertIsNone(manual['merchant_id'])
        self.assertEqual(manual['original_description'],'Synthetic shop');self.assertEqual(manual['description'],'Synthetic changed source')
        self.assertEqual(automatic['display_name'],'Synthetic new source');self.assertIsNone(automatic['merchant_id'])
        self.assertEqual(len(self.store.rows('select * from merchant_aliases')),1)
        reopened=finance.Store(self.store.root)
        self.assertEqual(reopened.rows("select original_description from transactions where id='one'")[0]['original_description'],'Synthetic shop')

    def test_merchant_name_routes_and_readable_query_evidence_keep_raw_inspectable(self):
        import assistant_features as af
        row=self.fixture();request,cookie,csrf=self.web()
        page=request('GET','/review',cookie=cookie)[2]
        self.assertIn('Readable merchant name',page)
        f={'csrf':csrf,'account':row['account'],'id':row['id'],'version':row['version'],'display_name':'Friendly <shop>','reuse_alias':'yes','confirm_identity':'yes'}
        self.assertEqual(request('POST','/merchant',dict(f,csrf='bad'),cookie)[0],403)
        self.assertEqual(request('POST','/merchant',f,cookie)[0],303)
        page=request('GET','/review',cookie=cookie)[2]
        self.assertIn('Friendly &lt;shop&gt;',page);self.assertIn('Original bank description',page);self.assertIn('Synthetic shop',page)
        dashboard=finance.dashboard(self.store,'synthetic')
        self.assertIn('Friendly &lt;shop&gt;',dashboard);self.assertIn('Original bank description',dashboard)
        p=af.validate_plan('{"op":"search","start":"2023-11-01","end":"2023-12-01","merchant":"Friendly"}',self.store)
        evidence=af.query_facts(self.store,p)['cards'][0]['rows']
        self.assertEqual(len(evidence),1)
        self.assertEqual(evidence[0]['merchant'],'Friendly <shop>');self.assertEqual(evidence[0]['original_description'],'Synthetic shop')
        self.assertEqual(self.store.rows('select * from rules'),[])
        self.assertTrue(all(not t['reviewed'] and not t['excluded'] and t['category']=='Uncategorized' for t in self.store.rows('select * from transactions')))

    def test_confirmed_identity_improves_examples_without_similarity_merge(self):
        row=self.fixture();self.store.name_transaction(row['account'],row['id'],row['version'],'Friendly',confirm_identity=True,reuse=True)
        current=self.store.rows("select * from transactions where id='one'")[0]
        self.store.correct(current['account'],current['id'],'Shopping',False)
        second=self.store.rows("select * from transactions where id='two'")[0]
        self.payload['accounts'][0]['transactions'][1]['description']='UNRELATED descriptor'
        self.store.ingest(self.payload)
        second=self.store.rows("select * from transactions where id='two'")[0]
        self.assertEqual(self.store.examples('UNRELATED descriptor'),[])
        self.store.name_transaction(second['account'],second['id'],second['version'],'Friendly',merchant_id=current['merchant_id'],confirm_identity=True,reuse=True)
        examples=self.store.examples('UNRELATED descriptor')
        self.assertEqual(len(examples),1);self.assertEqual(examples[0]['display_name'],'Friendly')

    def test_readable_names_appear_in_reports_and_local_model_context(self):
        from unittest.mock import patch
        row=self.fixture();self.store.name_transaction(row['account'],row['id'],row['version'],'Friendly <shop>',confirm_identity=True,reuse=True)
        report=self.store.report('2023-11-01','2023-12-01')
        self.assertIn('Friendly &lt;shop&gt;',finance.report_html(report))
        self.assertEqual(report['USD']['net_outflow'],1500)
        seen=[]
        def fetch(req,timeout,max_bytes):
            if req.full_url.endswith('/api/show'):return LOCAL_METADATA
            seen.append(json.loads(req.data));return {'message':{'content':'{"category":"Shopping"}'}}
        with patch('finance.fetch_json',side_effect=fetch):finance.categorize(self.store,seconds=1)
        first=json.loads(seen[0]['messages'][-1]['content'])
        self.assertEqual(first['readable_name'],'Friendly <shop>')
        self.assertEqual(first['untrusted_merchant'],'Synthetic shop')
        self.assertEqual(self.store.rows("select original_description from transactions where id='one'")[0]['original_description'],'Synthetic shop')

    def test_v2_to_v3_name_migration_preserves_confirmation_and_raw_immutability(self):
        legacy=pathlib.Path(self.tmp.name)/'v2';legacy.mkdir()
        db=sqlite3.connect(legacy/'ledger.sqlite3')
        db.executescript("""CREATE TABLE transactions(account TEXT,id TEXT,day TEXT,amount INTEGER,description TEXT,pending INTEGER,category TEXT,review TEXT,excluded INTEGER,reviewed INTEGER,category_id TEXT,version INTEGER NOT NULL DEFAULT 0,provenance TEXT,PRIMARY KEY(account,id));
          INSERT INTO transactions VALUES('synthetic','t','2023-11-14',-1200,'Synthetic raw',0,'Shopping','',0,1,'builtin:Shopping',7,'human');
          CREATE TABLE confirmed_examples(account TEXT,id TEXT,description TEXT,category_id TEXT,PRIMARY KEY(account,id));
          INSERT INTO confirmed_examples VALUES('synthetic','t','Synthetic raw','builtin:Shopping');
          PRAGMA user_version=2;""")
        db.commit();db.close();migrated=finance.Store(legacy)
        row=migrated.rows('select * from transactions')[0]
        self.assertEqual((row['reviewed'],row['version'],row['original_description'],row['display_name']),(1,7,'Synthetic raw','Synthetic raw'))
        self.assertEqual(len(migrated.examples('Synthetic raw')),1)
        with self.assertRaises(sqlite3.IntegrityError):
            with migrated.connect() as db:db.execute("UPDATE transactions SET original_description='Altered'")
        with self.assertRaises(ValueError):migrated.name_transaction('synthetic','t',6,'Stale')
        finance.Store(legacy)
        self.assertEqual(migrated.rows('select * from transactions')[0],row)
