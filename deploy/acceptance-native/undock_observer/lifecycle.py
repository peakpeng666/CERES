"""Launcher owns only its explicit Popen object; no pkill or foreign PID discovery."""
import subprocess
class ObserverProcess:
    def __init__(self,command,env,private_log):
        self.log=private_log.open('xb');self.proc=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
    def close(self,grace=5):
        forced=0
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:self.proc.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    self.proc.kill();forced=1;self.proc.wait(timeout=5)
            return {'exit_code':self.proc.returncode,'forced_kill_count':forced,'remaining_owned_count':int(self.proc.poll() is None)}
        finally:self.log.close()
    def wait_ready(self,receipt,seconds=15):
        import time,json
        deadline=time.monotonic()+seconds
        while self.proc.poll() is None and time.monotonic()<deadline:
            if receipt.is_file() and not receipt.is_symlink():
                try:
                    if receipt.stat().st_size<=64 and json.loads(receipt.read_text())=={'ready':True}:return True
                except (OSError,ValueError):pass
            time.sleep(.05)
        return False
