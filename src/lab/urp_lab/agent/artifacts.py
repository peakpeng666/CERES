# CERES 会话级大工具结果落盘与分页读取
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

LIMIT_BYTES = 4096
READ_SCHEMA = {
    'type': 'object', 'properties': {
        'artifact_id': {'type': 'string', 'maxLength': 100},
        'pointer': {'type': 'string', 'maxLength': 500, 'description': 'JSON Pointer，如 /result/runs/0/metrics；空串读取完整对象'},
        'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 800}},
    'required': ['artifact_id'], 'additionalProperties': False,
}
IMPORTANT = ('status', 'error', 'result', 'run_id', 'scenario_id', 'job_id', 'study_id', 'runs', 'metrics', 'check',
             'valid', 'agrees', 'state', 'algorithm', 'algorithm_id', 'objective', 'errors', 'summary', 'reason')


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def _preview(value, depth=0, width=4):
    if isinstance(value, dict):
        keys = [k for k in IMPORTANT if k in value] + [k for k in value if k not in IMPORTANT]
        if depth >= 5:
            return {'omitted_keys': keys}
        return {k: _preview(value[k], depth + 1, width) for k in keys[:24]}
    if isinstance(value, (list, tuple)):
        return [_preview(v, depth + 1, width) for v in value[:width]]
    if isinstance(value, str) and len(value) > 240:
        return {'text_preview': value[:240], 'total_chars': len(value)}
    return value


class ArtifactStore:
    def __init__(self, runtime, session_id: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id):
            raise ValueError('无效会话编号')
        self.root = Path(runtime) / 'agent-artifacts' / session_id

    def put(self, value) -> dict:
        data = encode(value).encode('utf-8')
        digest = hashlib.sha256(data).hexdigest()
        name = f'{digest}.json'
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        if not target.exists():
            fd, tmp = tempfile.mkstemp(prefix='.result-', dir=self.root)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return {'artifact_id': name, 'path': str(target.resolve()), 'sha256': digest, 'bytes': len(data)}

    def payload(self, value) -> str:
        text = encode(value)
        if len(text.encode('utf-8')) <= LIMIT_BYTES:
            return text
        ref = self.put(value)
        for width in (4, 1):
            preview = _preview(value, width=width)
            out = preview if isinstance(preview, dict) else {'preview': preview}
            out = {**out, '_artifact': {**ref, 'partial': True,
                   'note': '当前为结构化预览；完整 JSON 可用 read_artifact 按 pointer 与 offset 分页读取。'}}
            text = encode(out)
            if len(text.encode('utf-8')) <= LIMIT_BYTES:
                return text
        return encode({'_artifact': {**ref, 'partial': True}, 'value_type': type(value).__name__})

    def read(self, artifact_id: str, pointer: str = '', offset: int = 0, limit: int = 800) -> dict:
        if not re.fullmatch(r'[0-9a-f]{64}\.json', artifact_id):
            raise ValueError('无效结果编号')
        path = self.root / artifact_id
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError('结果路径超出当前会话目录')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact_id[:-5]:
            raise ValueError('结果完整性校验失败')
        value = json.loads(raw)
        if pointer:
            if not isinstance(pointer, str) or not pointer.startswith('/'):
                raise ValueError('pointer 须为空或以 / 开头')
            try:
                for part in pointer[1:].split('/'):
                    part = part.replace('~1', '/').replace('~0', '~')
                    if isinstance(value, list):
                        if not part.isdigit():
                            raise ValueError('数组索引须为非负整数')
                        value = value[int(part)]
                    else:
                        value = value[part]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError('结果中不存在该 JSON Pointer') from exc
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 800:
            raise ValueError('offset 须为非负整数，limit 须为 1–800')
        text = encode(value)
        end = min(len(text), offset + limit)
        return {'artifact_id': artifact_id, 'pointer': pointer, 'offset': offset,
                'content': text[offset:end], 'total_chars': len(text), 'next_offset': end if end < len(text) else None}
