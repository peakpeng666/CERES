"""Small closed-vocabulary preparation diagnostics, no raw-log parser."""
import json
import os
import stat

CODES=frozenset(['APPLICATION_BLOB', 'APPLICATION_CHANGED', 'APPLICATION_DIRTY', 'APPLICATION_EMPTY', 'APPLICATION_ENTRY', 'APPLICATION_FILE_SIZE', 'APPLICATION_HEAD', 'APPLICATION_SOURCE_BOUND', 'APPLICATION_SOURCE_SCOPE', 'APPLICATION_TREE_ID', 'BASE_IDENTITY', 'BOOTSTRAP_ALIAS', 'BOOTSTRAP_BYTES', 'BOOTSTRAP_CHANGED', 'BOOTSTRAP_LOCK_ANCHOR', 'BOOTSTRAP_MODE', 'BOOTSTRAP_SCHEMA', 'BOOTSTRAP_SIZE', 'BOOTSTRAP_SOURCE', 'BOOTSTRAP_TOOL', 'BOOTSTRAP_TOOL_SCHEMA', 'BOOTSTRAP_TOOL_SET', 'BOOTSTRAP_TRUST', 'BOOTSTRAP_VERSION', 'CA_ARCHIVE_BYTES', 'CA_TRANSPORT', 'DUPLICATE_KEY', 'EXECUTION_NOT_AUTHORIZED', 'EXPORTED_ALIAS_ESCAPE', 'EXPORTED_SPECIAL', 'EXPORT_CONTAINER_ID', 'FRESH_OUTPUT', 'FRESH_PYTHON_REQUIRED', 'IMAGE_ANCESTRY', 'IMAGE_DISTRIBUTION', 'IMAGE_ID', 'IMAGE_IDENTITY', 'IMAGE_ROOT_ONLY', 'IMAGE_USER', 'NONAPPLICATION_LINK', 'NONROOT_CONTROLLER', 'OUTPUT_ALIAS', 'PREPARED_ANCHOR', 'PYTHON_DISTRIBUTION', 'PYTHON_INSTALL_LAYOUT', 'PYTHON_VERSION', 'SEED_BYTES', 'SEED_PREMATURE_ANCHOR', 'SEED_TRANSPORT', 'SOURCE_ALIAS', 'SOURCE_ANCHOR', 'SOURCE_ANCHOR_FORMAT', 'SOURCE_BYTES', 'SOURCE_CHANGED', 'SOURCE_PATH', 'SOURCE_SCHEMA', 'SOURCE_TYPE_SIZE', 'UNCLASSIFIED_PREPARATION_FAILURE', 'UV_VERSION'])
STAGES=frozenset(('entry','source_verification','bootstrap_bytes','seed_retrieval','input_staging','image_build','image_inspection','input_export','artifact_verification'))
EXCEPTIONS=frozenset(('RuntimeError','CalledProcessError','TimeoutExpired','TimeoutError','HTTPError','URLError','FileNotFoundError','PermissionError','FileExistsError','OSError','ValueError','JSONDecodeError','KeyError','TypeError','OTHER'))

def project_exception(error,stage):
    code='UNCLASSIFIED_PREPARATION_FAILURE'
    if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES:
        code=error.args[0]
    kind=type(error).__name__
    return {'schema':1,'stage':stage if stage in STAGES else 'entry','code':code,'exception_type':kind if kind in EXCEPTIONS else 'OTHER'}

def read_projected(path):
    # No substring extraction from native logs; only this exact tiny JSON format.
    try:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size>4096:return None
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as stream:
            opened=os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size>4096:return None
            raw=stream.read(4097)
        if len(raw)>4096:return None
        def unique(pairs):
            out={}
            for key,value in pairs:
                if key in out:raise ValueError('duplicate')
                out[key]=value
            return out
        row=json.loads(raw,object_pairs_hook=unique)
        if type(row) is not dict or set(row)!={'schema','stage','code','exception_type'}:return None
        if type(row['schema']) is not int or row['schema']!=1:return None
        if not all(type(row[k]) is str for k in ('stage','code','exception_type')):return None
        if row['stage'] not in STAGES or row['code'] not in CODES or row['exception_type'] not in EXCEPTIONS:return None
        return row
    except (OSError,ValueError,UnicodeError):return None
