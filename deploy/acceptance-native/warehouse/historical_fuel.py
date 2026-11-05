"""Restore only hash-pinned historical data into a new isolated Fuel cache."""
import hashlib
import json
import os
import stat
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ARCHIVE_SHA256='ee2d2ce72dea345b5aaac8c4fe0d3d501a8d51c3211ba1c038ca025cc0ae3b0d'
ARCHIVE_SIZE=144263245
ASSET_ID=653519369
ASSET_URL='https://github.com/peakpeng666/URP/releases/download/platform-snapshot-20261018-ddb5113e/fuel-cache.tar.zst'
PREFIX='project/setup/2026-10-15/cache/ignition-fuel/'
SELECTED_SHA256='45bc4d45a362289f3e486eb4b0c9897910c1c14de06eb6d3dbf29916969531a6'
COHORT='historical_release_exact_bytes_v1'

def selected_files():
    data=Path(__file__).with_name('historical-fuel-selected.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=SELECTED_SHA256:raise ValueError('selected_manifest_identity_mismatch')
    result=json.loads(data)
    if len(result)!=61:raise ValueError('selected_manifest_count_mismatch')
    return result

def file_sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):digest.update(chunk)
    return digest.hexdigest()

def verify_archive(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=ARCHIVE_SIZE:raise ValueError('fuel_archive_size_mismatch')
    if file_sha(path)!=ARCHIVE_SHA256:raise ValueError('fuel_archive_identity_mismatch')

def download_archive(private):
    req=urllib.request.Request(f'https://api.github.com/repos/peakpeng666/URP/releases/assets/{ASSET_ID}',headers={'Accept':'application/vnd.github+json','User-Agent':'URP-isolated-acceptance'})
    with urllib.request.urlopen(req,timeout=60) as response:data=response.read(1048577)
    if len(data)>1048576:raise ValueError('fuel_asset_metadata_oversized')
    meta=json.loads(data)
    if any(meta.get(k)!=v for k,v in {'id':ASSET_ID,'name':'fuel-cache.tar.zst','size':ARCHIVE_SIZE,'digest':'sha256:'+ARCHIVE_SHA256,'browser_download_url':ASSET_URL}.items()):raise ValueError('fuel_asset_metadata_mismatch')
    target=Path(private)/'historical-fuel-cache.tar.zst';count=0;deadline=time.monotonic()+600
    with urllib.request.urlopen(ASSET_URL,timeout=60) as response,target.open('xb') as out:
        while True:
            if time.monotonic()>deadline:raise ValueError('fuel_download_limit')
            chunk=getattr(response,'read1',response.read)(1048576)
            if time.monotonic()>deadline:raise ValueError('fuel_download_limit')
            if not chunk:break
            count+=len(chunk)
            if count>ARCHIVE_SIZE or time.monotonic()>deadline:raise ValueError('fuel_download_limit')
            out.write(chunk)
    verify_archive(target)
    return target

def _extract_tar(stream,destination,selected):
    destination=Path(destination)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):raise ValueError('fuel_destination_not_empty')
    seen=set();found=set();total=0;deadline=time.monotonic()+120
    with tarfile.open(fileobj=stream,mode='r|') as tar:
        for member in tar:
            name=member.name;path=PurePosixPath(name);total+=member.size
            if (name in seen or path.is_absolute() or '..' in path.parts or str(path)!=name.rstrip('/')
                or member.issym() or member.islnk() or member.issparse()
                or not (member.isfile() or member.isdir()) or member.size<0 or member.size>64*1024*1024
                or len(seen)>=1000 or total>250*1024*1024 or time.monotonic()>deadline):raise ValueError('fuel_archive_member_rejected')
            seen.add(name)
            key=name.removeprefix(PREFIX) if name.startswith(PREFIX) else None
            if key not in selected:continue
            expected=selected[key]
            if not member.isfile() or member.size!=expected['size']:raise ValueError('fuel_member_size_mismatch')
            data=tar.extractfile(member).read(expected['size']+1)
            if len(data)!=expected['size'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:raise ValueError('fuel_member_identity_mismatch')
            target=destination/key
            if not target.resolve().is_relative_to(destination.resolve()):raise ValueError('fuel_destination_escape')
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as out:out.write(data)
            found.add(key)
    if found!=set(selected):raise ValueError('fuel_archive_missing_selected')

def verify_cache(destination):
    destination=Path(destination)
    if destination.is_symlink() or not destination.is_dir():raise ValueError('historical_cache_missing')
    selected=selected_files()
    directories={str(parent) for key in selected for parent in PurePosixPath(key).parents if str(parent)!='.'}
    found_files=set();found_dirs=set();pending=[destination]
    while pending:
        current=pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                relative=str(Path(entry.path).relative_to(destination))
                mode=entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):raise ValueError('historical_cache_link_rejected')
                if stat.S_ISDIR(mode):
                    if relative not in directories:raise ValueError('historical_cache_unexpected_directory')
                    found_dirs.add(relative);pending.append(Path(entry.path))
                elif stat.S_ISREG(mode):
                    if relative not in selected:raise ValueError('historical_cache_unexpected_file')
                    if entry.stat(follow_symlinks=False).st_nlink!=1:raise ValueError('historical_cache_link_rejected')
                    found_files.add(relative)
                else:raise ValueError('historical_cache_special_entry_rejected')
    if found_files!=set(selected) or found_dirs!=directories:raise ValueError('historical_cache_entry_set_mismatch')
    for key,expected in selected.items():
        path=destination/key
        if any(part.is_symlink() for part in [path,*path.parents]):raise ValueError('historical_cache_link_rejected')
        if not path.is_file() or path.stat().st_size!=expected['size'] or file_sha(path)!=expected['sha256']:raise ValueError('historical_cache_file_identity_mismatch')
    return {'resource_cache_cohort':COHORT,'historical_fuel_archive_sha256':ARCHIVE_SHA256,
            'historical_selected_manifest_sha256':SELECTED_SHA256,'historical_materialized_file_count':61,
            'historical_cache_verified':True}

def materialize(archive,destination):
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise ValueError('historical_cache_destination_exists')
    if any(parent.is_symlink() for parent in destination.parents):raise ValueError('historical_cache_parent_link_rejected')
    verify_archive(archive)
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.historical-fuel-',dir=destination.parent));proc=None
    try:
        proc=subprocess.Popen(['zstd','-dc','--',str(archive)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        _extract_tar(proc.stdout,staging,selected_files())
        if proc.wait(timeout=30)!=0:raise ValueError('fuel_archive_decompression_failed')
        receipt=verify_cache(staging)
        if destination.exists() or destination.is_symlink():raise ValueError('historical_cache_destination_exists')
        staging.rename(destination)
        return receipt
    finally:
        if proc:
            if proc.stdout:proc.stdout.close()
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
        if staging.exists():shutil.rmtree(staging)

ALIAS_PROJECTION='exact_jersey_alias_bytes_v1'
JERSEY_PREFIX='fuel.gazebosim.org/openrobotics/models/jersey barrier/6/'
JERSEY_FILES=('model.sdf','meshes/jersey_barrier.dae','materials/textures/jersey_barrier_diffuse.jpg','materials/textures/jersey_barrier_spec.jpg')
def canonicalize_assets(report,cache):
    """Project only four proven historical aliases; preserve raw report separately."""
    import copy
    verify_cache(cache)
    if not isinstance(report,dict) or not isinstance(report.get('files'),dict):raise ValueError('asset_report_invalid')
    result=copy.deepcopy(report);files=result['files'];selected=selected_files()
    allowed={str(Path(cache)/(JERSEY_PREFIX+suffix)) for suffix in JERSEY_FILES}
    allowed|={key.replace('/jersey barrier/','/jersey%20barrier/') for key in allowed}
    if any(('/jersey barrier/' in key or '/jersey%20barrier/' in key) and key not in allowed for key in files):raise ValueError('unexpected_historical_alias_path')
    for suffix in JERSEY_FILES:
        literal=JERSEY_PREFIX+suffix;encoded=literal.replace('/jersey barrier/','/jersey%20barrier/')
        if selected[literal]!=selected[encoded]:raise ValueError('historical_alias_bytes_differ')
        old=str(Path(cache)/literal);new=str(Path(cache)/encoded)
        for key in (old,new):
            if key in files and files[key]!=selected[literal]['sha256']:raise ValueError('reported_alias_identity_mismatch')
        if old in files:
            if new in files:raise ValueError('historical_alias_collision')
            files[new]=files.pop(old)
    return result
