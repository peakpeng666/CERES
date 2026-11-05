"""Exercise exact Dockerfile shell text with mocked chown; never change ownership."""
import json
import os
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]
PREFIX='SAFE_PREPARATION_DIAGNOSTIC:'
OLD='test "$WORKLOAD_UID" -gt 0 && test "$WORKLOAD_GID" -gt 0 && chown -R --no-dereference "$WORKLOAD_UID:$WORKLOAD_GID" /inputs'
def command():
 text=(ROOT/'preparation/Dockerfile').read_text().replace('\\\n',' ')
 return next(line[4:] for line in text.splitlines() if line.startswith('RUN { test '))
def run(cmd,uid='10001',gid='10001',rc='0',prefix=''):
 stub='chown() { echo CHOWN_CALLED; return "$FAKE_RC"; }; '
 return subprocess.run(['/bin/sh','-c',prefix+stub+cmd],env={'WORKLOAD_UID':uid,'WORKLOAD_GID':gid,'FAKE_RC':rc},capture_output=True,text=True)
def markers(result):
 return [json.loads(line[len(PREFIX):]) for line in result.stderr.splitlines() if line.startswith(PREFIX)]
class OwnershipDiagnosticTests(unittest.TestCase):
 def test_normal_success_silent_and_same_calls(self):
  before=run(OLD);after=run(command())
  self.assertEqual((before.returncode,before.stdout,before.stderr),(after.returncode,after.stdout,after.stderr))
 def test_uid_failure_preserves_exit_and_short_circuit(self):
  for uid in ('0','-1','PRIVATE_NONNUMERIC',''):
   with self.subTest(uid=uid):
    a=run(OLD,uid=uid);b=run(command(),uid=uid)
    self.assertEqual((a.returncode,a.stdout),(b.returncode,b.stdout));self.assertNotEqual(b.returncode,0)
    self.assertEqual(markers(b),[dict(schema=1,stage='input_ownership',substage='uid_check',code='UID_CHECK_FAILED',exception_class='ShellExit')])
 def test_gid_failure_preserves_exit_and_short_circuit(self):
  for gid in ('0','-1','PRIVATE_NONNUMERIC',''):
   with self.subTest(gid=gid):
    a=run(OLD,gid=gid);b=run(command(),gid=gid)
    self.assertEqual((a.returncode,a.stdout),(b.returncode,b.stdout))
    self.assertEqual(markers(b)[0]['substage'],'gid_check')
 def test_chown_exit_categories_unchanged(self):
  for rc in ('1','2','125','127','128','130','143','255'):
   with self.subTest(rc=rc):
    a=run(OLD,rc=rc);b=run(command(),rc=rc)
    self.assertEqual((a.returncode,a.stdout),(b.returncode,b.stdout))
    self.assertEqual(markers(b),[dict(schema=1,stage='input_ownership',substage='ownership_change',code='OWNERSHIP_COMMAND_FAILED',exception_class='ShellExit')])
 def test_marker_write_failure_preserves_original_exit(self):
  for kwargs in ({'uid':'0'},{'gid':'0'},{'rc':'127'}):
   a=run(OLD,**kwargs);b=run(command(),prefix='printf() { return 7; }; ',**kwargs)
   self.assertEqual(a.returncode,b.returncode)
 def test_cancellation_not_reclassified(self):
  prefix='chown() { kill -TERM $$; }; '
  for cmd in (OLD,command()):
   r=subprocess.run(['/bin/sh','-c',prefix+cmd],env={'WORKLOAD_UID':'1','WORKLOAD_GID':'1'},capture_output=True,text=True)
   self.assertEqual(r.returncode,-15);self.assertEqual(markers(r),[])
 def test_untrusted_values_never_enter_marker(self):
  secret='PRIVATE_NAME https://private.invalid /PRIVATE_PATH $(echo PRIVATE_COMMAND)'
  r=run(command(),uid=secret)
  value=json.dumps(markers(r));self.assertNotIn('PRIVATE',value);self.assertNotIn('https',value)
 def test_exact_original_command_arguments(self):
  cmd=command()
  for part in ('test "$WORKLOAD_UID" -gt 0','test "$WORKLOAD_GID" -gt 0','chown -R --no-dereference "$WORKLOAD_UID:$WORKLOAD_GID" /inputs'):
   self.assertEqual(cmd.count(part),1)
if __name__=='__main__':unittest.main()
