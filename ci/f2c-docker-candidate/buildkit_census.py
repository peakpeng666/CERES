"""Bounded diagnostic-only census, independent of successful vertex binding.

Candidate presence is NOT authentication, acceptance, or a failure attribution.
Only closed enums/count buckets escape; raw markers and error text never do.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

_spec = importlib.util.spec_from_file_location('urp_census_buildkit', Path(__file__).resolve().with_name('buildkit_diagnostic.py'))
_b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_b)
BUCKETS = ('none', 'one', 'multiple')
STATES = ('read', 'empty', 'missing', 'unreadable', 'unsupported_file', 'oversized', 'invalid', 'changed')
STAGES = tuple(_b.RUN_COMMANDS)
_RELATIONS = ('none', 'single', 'duplicate', 'conflict')
_BINDINGS = ('none', 'bound', 'unbound', 'conflict')
_HEADERS = ('none', 'unique', 'repeated_identical', 'conflict')
_ERRORS = ('none', 'canonical', 'noncanonical', 'mixed')
_SUMMARY = re.compile(rb'([0-9]{1,6}\.[0-9]{1,3}) (.*)')
_ERROR_OTHER = re.compile(rb'(?:#[1-9][0-9]{0,5} ERROR(?:\b|:)|ERROR:)')
_PREFIXES = (_b.BOOTSTRAP_PREFIX.encode('ascii'), _b.PACKAGE_PREFIX.encode('ascii'), _b._stage.PREFIX.encode('ascii'))
_COUNTS = ('vertex_candidate_count', 'summary_candidate_count', 'unframed_candidate_count', 'invalid_candidate_count', 'top_level_error_count', 'other_error_count')

def _bucket(number):
    return BUCKETS[min(number, 2)]

def _receipt(state='read', **updates):
    result = {'schema': 1, 'diagnostic_only': True, 'state': state,
              **{key: 'none' for key in _COUNTS}, 'candidate_stages': [],
              'binding_input': 'not_used', 'vertex_marker_relation': 'none', 'vertex_marker_binding': 'none',
              'header_relation': 'none', 'error_shape': 'none', 'error_binding': 'none'}
    result.update(updates)
    return result

def validate_census(value):
    template = _receipt()
    if type(value) is not dict or set(value) != set(template):
        return None
    if type(value['schema']) is not int or value['schema'] != 1 or value['diagnostic_only'] is not True:
        return None
    choices = {'binding_input': ('not_used', 'legacy_literal', 'verified_values', 'missing_or_invalid'), 'state': STATES, 'vertex_marker_relation': _RELATIONS,
               'vertex_marker_binding': _BINDINGS, 'header_relation': _HEADERS,
               'error_shape': _ERRORS, 'error_binding': _BINDINGS,
               **{key: BUCKETS for key in _COUNTS}}
    if any(type(value[k]) is not str or value[k] not in options for k, options in choices.items()):
        return None
    stages = value['candidate_stages']
    if type(stages) is not list or any(type(x) is not str or x not in STAGES for x in stages):
        return None
    if stages != [x for x in STAGES if x in stages]:
        return None
    if value['state'] != 'read':
        return _receipt(value['state']) if value == _receipt(value['state']) else None
    if value['binding_input'] == 'not_used': return None
    if value['vertex_candidate_count'] == 'none':
        if value['vertex_marker_relation'] != 'none' or value['vertex_marker_binding'] != 'none':
            return None
    elif value['vertex_marker_relation'] == 'none' or value['vertex_marker_binding'] == 'none':
        return None
    if value['vertex_candidate_count'] == 'one' and value['vertex_marker_relation'] != 'single':
        return None
    if value['vertex_candidate_count'] == 'multiple' and value['vertex_marker_relation'] not in ('duplicate', 'conflict'):
        return None
    candidates = [value[k] for k in _COUNTS[:3]]
    if all(x == 'none' for x in candidates) != (not stages):
        return None
    top, other = value['top_level_error_count'], value['other_error_count']
    shape = ('none' if top == other == 'none' else 'noncanonical' if top == 'none' else 'canonical' if other == 'none' else 'mixed')
    if value['error_shape'] != shape or ((top == 'none') != (value['error_binding'] == 'none')):
        return None
    result = dict(value)
    result['candidate_stages'] = list(stages)
    return result

def _candidate(payload):
    """Return (recognized, valid closed envelope); success is not failure evidence."""
    if not payload.startswith(_PREFIXES):
        return False, None
    if len(payload) > _b.MAX_MARKER_BYTES or any(x < 32 or x > 126 for x in payload):
        return True, None
    if payload.startswith(_PREFIXES[0]):
        code = payload[len(_PREFIXES[0]):].decode('ascii')
        if code == _b.BOOTSTRAP_SUCCESS:
            return False, None
        source, detail = 'bootstrap', {'code': code}
    else:
        prefix = _PREFIXES[1] if payload.startswith(_PREFIXES[1]) else _PREFIXES[2]
        try:
            detail = json.loads(payload[len(prefix):], object_pairs_hook=_b._unique_object)
        except (ValueError, UnicodeError, RecursionError):
            return True, None
        source = 'package' if prefix == _PREFIXES[1] else (detail.get('stage') if type(detail) is dict else None)
    return True, _b.validate_projection({'schema': 1, 'source': source, 'diagnostic_only': True, 'detail': detail})

def _scan(stream, build_args):
    counts = {key: 0 for key in _COUNTS}
    stages, headers, header_stages, source_vertices = set(), {}, {}, {}
    header_relation = 'none'
    vertex_first = None
    relation, binding = 'none', 'none'
    errors = []
    total = 0
    def merge_binding(old, new):
        if old == 'none': return new
        if old == new: return old
        return 'conflict'
    while True:
        raw = stream.readline(_b.MAX_LINE_BYTES + 1)
        if not raw: break
        total += len(raw)
        if total > _b.MAX_LOG_BYTES or len(raw) > _b.MAX_LINE_BYTES:
            return _receipt('invalid')
        if not raw.endswith(b'\n'):
            return _receipt('invalid')
        line = raw[:-1]
        header = _b._HEADER.fullmatch(line)
        if header:
            vertex, step, command = header.groups()
            shape = (step, command)
            if vertex in headers:
                if headers[vertex] != shape:
                    header_relation = 'conflict'
                    header_stages[vertex] = None
                elif header_relation != 'conflict':
                    header_relation = 'repeated_identical'
            else:
                headers[vertex] = shape
                header_stages[vertex] = _b._static_header(step, command, build_args)
                stage = header_stages[vertex]
                if stage is not None:
                    if stage in source_vertices and source_vertices[stage] != vertex: header_relation = 'conflict'
                    source_vertices[stage] = vertex
                if header_relation == 'none': header_relation = 'unique'
            if len(headers) > _b.MAX_VERTICES: return _receipt('invalid')
            continue
        error = _b._ERROR.match(line)
        if error:
            counts['top_level_error_count'] += 1
            if len(errors) < 2: errors.append(header_stages.get(error[1]))
            continue
        if _ERROR_OTHER.match(line):
            counts['other_error_count'] += 1
            continue
        output = _b._OUTPUT.fullmatch(line)
        summary = None if output else _SUMMARY.fullmatch(line)
        if output:
            vertex, _, payload = output.groups(); kind = 'vertex_candidate_count'
        elif summary:
            payload = summary[2]; vertex = None; kind = 'summary_candidate_count'
        else:
            payload = line; vertex = None; kind = 'unframed_candidate_count'
        recognized, candidate = _candidate(payload)
        if not recognized: continue
        if candidate is None:
            counts['invalid_candidate_count'] += 1
            continue
        counts[kind] += 1
        stage = candidate['source']; stages.add(stage)
        if vertex is not None:
            canonical = (vertex, json.dumps(candidate, sort_keys=True, separators=(',', ':')))
            if vertex_first is None:
                vertex_first = canonical; relation = 'single'
            elif vertex_first == canonical and relation != 'conflict': relation = 'duplicate'
            else: relation = 'conflict'
            bound_stage = header_stages.get(vertex)
            state = 'unbound' if bound_stage is None else 'bound' if bound_stage == stage else 'conflict'
            binding = merge_binding(binding, state)
    error_binding = 'none'
    if errors:
        error_binding = ('conflict' if len(errors) > 1 or header_relation == 'conflict'
                         else 'bound' if errors[0] is not None else 'unbound')
    if header_relation == 'conflict' and counts['vertex_candidate_count']:
        binding = 'conflict'
    top, other = counts['top_level_error_count'], counts['other_error_count']
    if total == 0: return _receipt('empty')
    return _receipt('read',
        binding_input='legacy_literal' if build_args is None else 'verified_values' if _b.validate_build_args(build_args) is not None else 'missing_or_invalid',
        **{key: _bucket(value) for key, value in counts.items()},
        candidate_stages=[x for x in STAGES if x in stages],
        vertex_marker_relation=relation, vertex_marker_binding=binding,
        header_relation=header_relation,
        error_shape='mixed' if top and other else 'canonical' if top else 'noncanonical' if other else 'none',
        error_binding=error_binding)

def inspect_census(path, build_args=None):
    """Regular stable bounded file only; never return raw bytes or a trusted error."""
    try:
        path = Path(path); before = path.lstat()
        if not stat.S_ISREG(before.st_mode): return _receipt('unsupported_file')
        if before.st_size > _b.MAX_LOG_BYTES: return _receipt('oversized')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _b.MAX_LOG_BYTES: return _receipt('invalid')
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino): return _receipt('changed')
            result = _scan(stream, build_args)
            after = os.fstat(stream.fileno()); current = path.lstat()
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns): return _receipt('changed')
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino): return _receipt('changed')
        return validate_census(result) or _receipt('invalid')
    except FileNotFoundError:
        return _receipt('missing')
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return _receipt('unreadable')
