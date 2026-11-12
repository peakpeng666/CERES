"""Extract only CIFAR training members; never extract/parse official test samples."""
from pathlib import Path
import hashlib,json,tarfile,time
from core import ROOT,TRAIN_FILES,digest,save,stamp

t,c=time.perf_counter(),time.process_time()
archive=ROOT/"data/cifar-10-binary.tar.gz"
expected=json.loads((ROOT/"evidence/original_p4/artifacts/data_manifest.json").read_text())
assert digest(archive)==expected["archive_sha256"]
assert hashlib.md5(archive.read_bytes()).hexdigest()==expected["archive_md5"]
with tarfile.open(archive,"r:gz") as tar:
    for name in TRAIN_FILES:
        m=tar.getmember("cifar-10-batches-bin/"+name)
        assert m.isfile() and m.size==30730000
        with tar.extractfile(m) as f: (ROOT/"data"/name).write_bytes(f.read())
        assert digest(ROOT/"data"/name)==expected["extracted_files"][name]["sha256"]
save(ROOT/"artifacts/data_recovery.json",{
    "at_utc":stamp(),"source_url":expected["source_url"],"source":"official HTTPS archive; original recovery package documents dataset exclusion; relevant Drive folders were also inspected",
    "archive_sha256":digest(archive),"archive_md5":expected["archive_md5"],"archive_bytes":archive.stat().st_size,
    "train_file_hashes":{n:digest(ROOT/"data"/n) for n in TRAIN_FILES},"split_sha256":expected["split_sha256"],
    "official_test_extracted":False,"official_test_sample_reads":0,"official_test_evaluations":0,
    "preparation_wall_seconds":time.perf_counter()-t,"preparation_process_cpu_seconds":time.process_time()-c,
    "timing_scope":"hash validation and explicit training-only extraction; network download attempts and timing separately in artifacts/data_download*.log"})
print("Verified original archive and all five original training hashes; test never extracted")
