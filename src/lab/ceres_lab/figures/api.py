"""Research figures: catalogue, single-figure rendering (SVG / PDF / EPS / PNG / TIFF) and the paper bundle (zip)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..services import ServiceError
from . import registry
from .style import PRESETS, Style

router = APIRouter()
CONTEXT_KEYS = ('scenario_id', 'run_id', 'run_ids', 'session_id', 'study_id', 'asset_id', 'asset_v')


def _query(request: Request) -> dict:
    return {k: request.query_params.get(k) for k in CONTEXT_KEYS if request.query_params.get(k)}


def _style(request: Request, size: str | None = None) -> Style:
    q = request.query_params
    preset = q.get('preset') or 'elsevier'
    if preset not in PRESETS:
        raise ServiceError(400, 'invalid_preset', f"版式应为 {' / '.join(PRESETS)}")
    lang = q.get('lang') or 'zh'
    if lang not in ('zh', 'en'):
        raise ServiceError(400, 'invalid_lang', '语言应为 zh 或 en')
    sz = q.get('size') or size or 'single'
    if sz not in ('single', 'onehalf', 'double'):
        raise ServiceError(400, 'invalid_size', '宽度应为 single / onehalf / double')
    try:
        dpi = int(q.get('dpi') or 600)
    except ValueError:
        raise ServiceError(400, 'invalid_dpi', 'dpi 应为整数') from None
    if dpi not in (300, 600, 1200):
        raise ServiceError(400, 'invalid_dpi', 'dpi 应为 300 / 600 / 1200')
    return Style(preset=preset, size=sz, lang=lang, dpi=dpi)


@router.get('/figures/presets')
def presets():
    return {'presets': {k: {'label': v['label'], 'widths_mm': v['widths'], 'font_pt': v['font']} for k, v in PRESETS.items()},
            'groups': [{'key': k, 'zh': zh, 'en': en} for k, zh, en in registry.GROUPS], 'formats': list(registry.FORMATS)}


@router.get('/figures/catalog')
def figure_catalog(request: Request):
    ctx = registry.build_context(request.app.state.lab, _query(request))
    return {'figures': registry.catalog(ctx), 'sources': ctx.sources(), 'notes': ctx.notes, 'digest': ctx.digest()}


@router.get('/figures/render/{key}')
def figure_render(key: str, request: Request):
    spec = registry.BY_KEY.get(key)
    if not spec:
        raise ServiceError(404, 'unknown_figure', f'没有图 {key}')
    fmt = request.query_params.get('format') or 'svg'
    if fmt not in registry.FORMATS:
        raise ServiceError(400, 'invalid_format', f"格式应为 {' / '.join(registry.FORMATS)}")
    st = _style(request, spec.size)
    ctx = registry.build_context(request.app.state.lab, _query(request))
    try:
        blob, meta = registry.render(key, ctx, st, fmt)
    except LookupError as exc:
        raise ServiceError(409, 'figure_unavailable', str(exc)) from None
    name = f"{key}_{st.preset}_{st.size}_{st.lang}.{fmt}"
    inline = request.query_params.get('download') != '1' and fmt in ('svg', 'png')
    headers = {'Content-Disposition': f"{'inline' if inline else 'attachment'}; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
               'Cache-Control': 'no-store', 'X-Figure-Size-Mm': f"{meta['size_mm'][0]}x{meta['size_mm'][1]}"}
    return Response(blob, media_type=registry.FORMATS[fmt], headers=headers)


@router.get('/figures/meta/{key}')
def figure_meta(key: str, request: Request):
    """Caption, provenance and the plotted data of one figure (renders an SVG once, then served from cache)."""
    spec = registry.BY_KEY.get(key)
    if not spec:
        raise ServiceError(404, 'unknown_figure', f'没有图 {key}')
    st = _style(request, spec.size)
    ctx = registry.build_context(request.app.state.lab, _query(request))
    try:
        _, meta = registry.render(key, ctx, st, 'svg')
    except LookupError as exc:
        raise ServiceError(409, 'figure_unavailable', str(exc)) from None
    tables = {k: len(v) for k, v in (meta['table'] or {}).items() if isinstance(v, list)}
    return {k: v for k, v in meta.items() if k != 'table'} | {'tables': tables}


@router.get('/figures/bundle')
def figure_bundle(request: Request):
    st = _style(request)
    fmts = [f for f in (request.query_params.get('formats') or 'pdf,svg,png').split(',') if f]
    if not fmts or any(f not in registry.FORMATS for f in fmts):
        raise ServiceError(400, 'invalid_format', f"格式应为 {' / '.join(registry.FORMATS)}")
    ctx = registry.build_context(request.app.state.lab, _query(request))
    blob, manifest = registry.bundle(ctx, st.preset, st.lang, fmts, st.dpi)
    if not manifest['figures']:
        raise ServiceError(409, 'nothing_to_draw', '当前选择下没有可绘制的图：' + '；'.join(f"{s['title']}：{s['reason']}" for s in manifest['skipped'][:6]))
    name = f"figures_{st.preset}_{st.lang}.zip"
    return Response(blob, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})
