import io,json,pathlib,socket,ssl,tempfile,threading,unittest,urllib.error
from unittest.mock import Mock
from runner.verify_registry import fetch_metadata,verify,CheckError
from public_summary import registry_projection

class Response(io.BytesIO):
    def geturl(self):return 'https://registry.npmjs.org/public/1'

class RegistryTests(unittest.TestCase):
    def test_transient_retry_and_bounded_exhaustion(self):
        for first in (urllib.error.HTTPError('PRIVATE',429,'PRIVATE',{},None),urllib.error.HTTPError('PRIVATE',503,'PRIVATE',{},None),socket.timeout('PRIVATE')):
            opener=Mock(side_effect=[first,Response(b'{"dist":{"integrity":"sha512-ok"}}')]);sleep=Mock()
            metadata,attempts=fetch_metadata('https://registry.npmjs.org/public/1',opener,sleep)
            self.assertEqual(attempts,2);sleep.assert_called_once_with(2)
        opener=Mock(side_effect=socket.timeout('PRIVATE'));sleep=Mock()
        with self.assertRaises(CheckError) as caught:fetch_metadata('PRIVATE',opener,sleep)
        self.assertEqual(caught.exception.attempts,3);self.assertEqual(opener.call_count,3)
    def test_auth_missing_not_retried(self):
        for code in (401,403,404):
            opener=Mock(side_effect=urllib.error.HTTPError('PRIVATE',code,'PRIVATE',{},None));sleep=Mock()
            with self.assertRaises(CheckError) as caught:fetch_metadata('PRIVATE',opener,sleep)
            self.assertEqual(caught.exception.http_status,code);self.assertEqual(opener.call_count,1);sleep.assert_not_called()
    def test_non_timeout_network_and_tls_not_retried(self):
        for cause,expected in ((OSError('PRIVATE_DNS'),'network'),(ssl.SSLError('PRIVATE_TLS'),'tls')):
            opener=Mock(side_effect=urllib.error.URLError(cause));sleep=Mock()
            with self.assertRaises(CheckError) as caught:fetch_metadata('PRIVATE',opener,sleep)
            self.assertEqual(caught.exception.category,expected);self.assertEqual(opener.call_count,1);sleep.assert_not_called()

    def test_integrity_fail_closed_and_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'src/frontend';path.mkdir(parents=True)
            (path/'package-lock.json').write_text(json.dumps({'packages':{'node_modules/PRIVATE':{'resolved':'https://registry.npmmirror.com/PRIVATE','version':'1','integrity':'sha512-original'}}}))
            fetch=Mock(return_value=({'dist':{'integrity':'sha512-mismatch'}},1))
            result=verify(tmp,fetch)
            self.assertEqual(result['status'],'failed');self.assertEqual(result['phase'],'integrity_check')
            self.assertEqual(result['mirror_index'],1);fetch.assert_called_once()
            self.assertNotIn('PRIVATE',json.dumps(result))
            fetch=Mock(side_effect=CheckError('http',403,1));result=verify(tmp,fetch)
            self.assertEqual(result['phase'],'metadata_fetch');self.assertEqual(result['http_status'],403)
            result=verify(tmp,Mock(return_value=({'dist':{'integrity':'sha512-original','tarball':'https://registry.npmjs.org/PRIVATE'}},1)))
            self.assertEqual(result['status'],'passed');self.assertEqual(result['mirror_identities_verified'],1)
            result=verify(tmp,Mock(return_value=({'dist':{'integrity':'sha512-original','tarball':'https://registry.npmjs.org/OTHER'}},1)))
            self.assertEqual(result['error'],'tarball_path_mismatch')
    def test_reducer_drops_unknown_fields(self):
        result=registry_projection({'phase':'PRIVATE','status':'PRIVATE','error':'PRIVATE','http_status':'PRIVATE','mirror_index':True,'attempts':999,'raw':'PRIVATE'})
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertIsNone(result['mirror_index']);self.assertIsNone(result['attempts'])

class RegistryConcurrencyTests(unittest.TestCase):
    def fixture(self,root):
        directory=root/'src/frontend';directory.mkdir(parents=True)
        packages={f'node_modules/p{n}':{
            'resolved':f'https://registry.npmmirror.com/p{n}/-/p{n}-1.tgz',
            'version':'1','integrity':f'sha512-{n}'} for n in range(90)}
        (directory/'package-lock.json').write_text(json.dumps({'packages':packages}))

    def metadata(self,n):
        return {'dist':{'integrity':f'sha512-{n}',
                        'tarball':f'https://registry.npmjs.org/p{n}/-/p{n}-1.tgz'}},1

    def test_all_ninety_verified_with_four_worker_bound(self):
        from runner.verify_registry import MAX_WORKERS
        self.assertEqual(MAX_WORKERS,4)
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);self.fixture(root)
            guard=threading.Lock();first_batch=threading.Barrier(4,timeout=5)
            active=peak=0;seen=[]
            def fetch(url):
                nonlocal active,peak
                n=int(url.split('/')[-2][1:])
                with guard:
                    active+=1;peak=max(peak,active);seen.append(n)
                try:
                    if n<4:first_batch.wait()
                    return self.metadata(n)
                finally:
                    with guard:active-=1
            result=verify(root,fetch)
            self.assertEqual(result['status'],'passed')
            self.assertEqual(result['mirror_identities_verified'],90)
            self.assertEqual(peak,4);self.assertEqual(active,0)
            self.assertCountEqual(seen,range(90))

    def test_earliest_lock_failure_wins_and_only_four_are_outstanding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);self.fixture(root)
            guard=threading.Lock();first_batch=threading.Barrier(4,timeout=5)
            later_failed=threading.Event();seen=[]
            def fetch(url):
                n=int(url.split('/')[-2][1:])
                with guard:seen.append(n)
                first_batch.wait()
                if n==2:
                    later_failed.set()
                    raise CheckError('http',503,3)
                if n==0:
                    if not later_failed.wait(5):raise AssertionError('later fixture did not finish')
                    raise CheckError('timeout',attempts=3)
                return self.metadata(n)
            result=verify(root,fetch)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['phase'],'metadata_fetch')
            self.assertEqual(result['error'],'timeout')
            self.assertEqual(result['mirror_index'],1)
            self.assertEqual(result['mirror_identities_verified'],0)
            self.assertEqual(result['attempts'],3)
            self.assertIsNone(result['http_status'])
            self.assertCountEqual(seen,range(4))

    def test_each_package_still_applies_source_integrity_and_path_guards(self):
        from runner.verify_registry import verify_package
        good={'resolved':'https://registry.npmmirror.com/p1/-/p1-1.tgz',
              'version':'1','integrity':'sha512-1'}
        cases=[(dict(good,resolved='http://registry.npmmirror.com/p1'),'source_not_allowlisted'),
               (dict(good,resolved='https://elsewhere.invalid/p1'),'source_not_allowlisted'),
               (dict(good,integrity='sha512-wrong'),'integrity_mismatch'),
               (dict(good,resolved=good['resolved']+'?private=1'),'tarball_path_mismatch'),
               (dict(good,resolved='https://registry.npmmirror.com/wrong'),'tarball_path_mismatch'),
               (dict(good,version=None),'lock_invalid')]
        for package,expected in cases:
            row=verify_package('node_modules/p1',package,lambda _:self.metadata(1))
            self.assertEqual(row['error'],expected)
            self.assertFalse(row['verified'])

if __name__=='__main__':unittest.main()
