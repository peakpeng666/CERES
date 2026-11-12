"""Publication style shared by every research figure.

Widths follow the publishers' artwork guides (final printed size, not scaled later):
IEEE 88.9 / 181.9 mm (3.5 / 7.16 in), Elsevier 90 / 140 / 190 mm, Springer 84 / 174 mm, and a Chinese thesis
text block. Text is 7–8 pt at final size (6 pt minimum), lines ≥ 0.5 pt, fonts are embedded TrueType
(PDF/EPS Type 42; SVG keeps editable text), raster output is 600 dpi by default (1200 for line art on request).
Colours are the Okabe–Ito colour-blind-safe set (Okabe & Ito 2008; Wong 2011, Nat. Methods 8:441) with
Paul Tol's muted scheme as extension, always paired with a second channel (marker / dash) so figures survive
greyscale printing.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

MM = 1 / 25.4

PRESETS = {
    'elsevier': {'label': 'Elsevier（Computers and Electronics in Agriculture / Biosystems Engineering）',
                 'widths': {'single': 90.0, 'onehalf': 140.0, 'double': 190.0}, 'font': 7.5, 'serif': False},
    'ieee': {'label': 'IEEE（T-RO / RA-L / T-ASE）', 'widths': {'single': 88.9, 'onehalf': 120.0, 'double': 181.9}, 'font': 8.0, 'serif': False},
    'springer': {'label': 'Springer（Precision Agriculture / Autonomous Robots）', 'widths': {'single': 84.0, 'onehalf': 129.0, 'double': 174.0},
                 'font': 8.0, 'serif': False},
    'thesis': {'label': '学位论文（版心 150 mm，宋体 / Times New Roman）', 'widths': {'single': 120.0, 'onehalf': 135.0, 'double': 150.0},
               'font': 9.0, 'serif': True},
}

# Okabe–Ito without yellow first (yellow is kept for the 8th series: low contrast on white)
OKABE_ITO = ['#0072B2', '#E69F00', '#009E73', '#D55E00', '#CC79A7', '#56B4E9', '#000000', '#F0E442']
TOL_MUTED = ['#332288', '#88CCEE', '#44AA99', '#117733', '#999933', '#DDCC77', '#CC6677', '#882255', '#AA4499']
PALETTE = OKABE_ITO + TOL_MUTED
MARKERS = ['o', 's', '^', 'D', 'v', 'P', 'X', '*', 'h', '<', '>', 'p', 'H', 'd', '8', '1', '2']
DASHES = ['-', '--', ':', '-.', (0, (5, 1)), (0, (3, 1, 1, 1)), (0, (1, 1)), (0, (5, 2, 1, 2))]

OBSTACLE = {  # fill, edge — muted, distinguishable in greyscale by lightness
    'pond': ('#A6CEE3', '#1F78B4'), 'building': ('#BDBDBD', '#525252'), 'greenhouse': ('#DCEFF5', '#5A9BB0'),
    'tree': ('#B2DF8A', '#33A02C'), 'ditch': ('#E3CDA8', '#8C6D31'), 'rock': ('#D0CCC6', '#6E6A64'), 'pole': ('#9E9E9E', '#424242'),
    'other': ('#D9D9D9', '#636363'),
}
SIM_BODY = (0.8, 0.56)   # field-simulation robot footprint, length × width (fieldworld.py)
FIELD = {'boundary_fill': '#F7F4EA', 'boundary_edge': '#6B5B3E', 'plot_fill': '#E5F2D9', 'plot_edge': '#5B8C3E', 'lane': '#A88E52',
         'nogo_edge': '#C0392B', 'depot': '#000000', 'task': '#000000'}

LATIN_SANS = ['Liberation Sans', 'Arial', 'Helvetica', 'DejaVu Sans']
LATIN_SERIF = ['Liberation Serif', 'Times New Roman', 'DejaVu Serif']
CJK_SANS = ['Noto Sans CJK SC', 'Source Han Sans SC', 'Droid Sans Fallback']
CJK_SERIF = ['Noto Serif CJK SC', 'Source Han Serif SC', 'Noto Sans CJK SC']


@dataclass(frozen=True)
class Style:
    preset: str = 'elsevier'
    size: str = 'single'          # single / onehalf / double
    lang: str = 'zh'              # zh / en
    dpi: int = 600

    @property
    def spec(self) -> dict:
        return PRESETS.get(self.preset, PRESETS['elsevier'])

    @property
    def width_in(self) -> float:
        return self.spec['widths'].get(self.size, self.spec['widths']['single']) * MM

    @property
    def fs(self) -> float:
        return self.spec['font']

    def t(self, zh: str, en: str) -> str:
        return zh if self.lang == 'zh' else en


def _available(names: list[str]) -> list[str]:
    have = {f.name for f in font_manager.fontManager.ttflist}
    return [n for n in names if n in have]


def rc(style: Style) -> dict:
    latin = LATIN_SERIF if style.spec['serif'] else LATIN_SANS
    cjk = CJK_SERIF if style.spec['serif'] else CJK_SANS
    # one Latin face, one CJK face (zh only), DejaVu for symbols neither has; extra fallbacks only add bold-weight warnings
    family = (_available(latin)[:1] + (_available(cjk)[:1] if style.lang == 'zh' else []) + ['DejaVu Sans'])
    fs = style.fs
    lw = 0.6
    return {
        'font.family': family, 'font.size': fs, 'axes.titlesize': fs, 'axes.labelsize': fs, 'xtick.labelsize': fs - 0.5,
        'ytick.labelsize': fs - 0.5, 'legend.fontsize': fs - 0.5, 'legend.title_fontsize': fs - 0.5, 'figure.titlesize': fs + 1,
        'mathtext.fontset': 'custom', 'mathtext.rm': family[0], 'mathtext.it': f'{family[0]}:italic', 'mathtext.bf': f'{family[0]}:bold',
        'axes.unicode_minus': False, 'axes.linewidth': lw, 'axes.edgecolor': '#222222', 'axes.labelcolor': '#111111',
        'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': False, 'axes.axisbelow': True,
        'axes.prop_cycle': matplotlib.cycler(color=PALETTE),
        'grid.linewidth': 0.4, 'grid.color': '#D9D9D9', 'grid.linestyle': '-',
        'xtick.direction': 'out', 'ytick.direction': 'out', 'xtick.major.size': 2.5, 'ytick.major.size': 2.5,
        'xtick.minor.size': 1.5, 'ytick.minor.size': 1.5, 'xtick.major.width': lw, 'ytick.major.width': lw,
        'xtick.minor.width': 0.4, 'ytick.minor.width': 0.4, 'xtick.color': '#222222', 'ytick.color': '#222222',
        'lines.linewidth': 1.0, 'lines.markersize': 3.2, 'patch.linewidth': 0.5, 'hatch.linewidth': 0.5,
        'legend.frameon': False, 'legend.handlelength': 1.6, 'legend.borderaxespad': 0.3, 'legend.columnspacing': 1.0,
        'legend.handletextpad': 0.4, 'legend.labelspacing': 0.3,
        'figure.dpi': 100, 'savefig.dpi': style.dpi, 'savefig.facecolor': 'white', 'savefig.transparent': False,
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none', 'pdf.compression': 6,
        'figure.constrained_layout.use': True, 'figure.constrained_layout.h_pad': 1 / 72, 'figure.constrained_layout.w_pad': 1 / 72,
        'figure.constrained_layout.hspace': 0.04, 'figure.constrained_layout.wspace': 0.04,
        'image.cmap': 'viridis',
    }


@contextmanager
def apply(style: Style):
    with plt.rc_context(rc(style)):
        yield


def figure(style: Style, aspect: float = 0.68, *, nrows: int = 1, ncols: int = 1, height_in: float | None = None, **kw):
    """A figure exactly as wide as the chosen column; height = aspect × width (clamped to a printable page).

    Built without pyplot (no global figure manager), so rendering many figures in a server never leaks."""
    w = style.width_in
    h = height_in if height_in is not None else max(1.2, min(w * aspect, 9.0))
    fig = Figure(figsize=(w, h), layout='constrained')
    return fig, fig.subplots(nrows, ncols, squeeze=False, **kw)


def panel_label(ax, letter: str, style: Style):
    ax.text(-0.02, 1.02, f'({letter})', transform=ax.transAxes, ha='right', va='bottom', fontweight='bold', fontsize=style.fs + 0.5)


def nice_length(span: float) -> float:
    """1-2-5 series length close to a fifth of the span (scale bars)."""
    if span <= 0:
        return 1.0
    target = span / 5
    k = 10 ** math.floor(math.log10(target))
    return next(m * k for m in (5, 2, 1) if m * k <= target * 1.25)


def scale_bar(ax, style: Style, x0: float, y0: float, x1: float, y1: float):
    """Scale bar in the lower-left corner of the data area (map figures)."""
    L = nice_length(x1 - x0)
    pad_x, pad_y = (x1 - x0) * 0.04, (y1 - y0) * 0.05
    bx, by = x0 + pad_x, y0 + pad_y
    h = (y1 - y0) * 0.012
    ax.add_patch(matplotlib.patches.Rectangle((bx, by), L, h, facecolor='#111111', edgecolor='none', zorder=20))
    ax.add_patch(matplotlib.patches.Rectangle((bx + L / 2, by), L / 2, h, facecolor='white', edgecolor='#111111', linewidth=0.4, zorder=21))
    label = f'{L:g} m' if L >= 1 else f'{L * 100:g} cm'
    ax.text(bx + L / 2, by + h * 1.6, label, ha='center', va='bottom', fontsize=style.fs - 1, zorder=21,
            bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': 0.7, 'pad': 0.6})


def north_arrow(ax, style: Style):
    ax.annotate('N', xy=(0.965, 0.97), xytext=(0.965, 0.86), xycoords='axes fraction', ha='center', va='center',
                fontsize=style.fs, fontweight='bold', arrowprops={'arrowstyle': '-|>', 'lw': 0.8, 'color': '#111111'}, zorder=22)


def qlabel(st: Style, zh: str, en: str, unit: str | None = None) -> str:
    """Axis label: Chinese journals and theses write quantity/unit (GB/T 3102), English journals quantity (unit)."""
    if not unit:
        return st.t(zh, en)
    return f'{zh}/{unit}' if st.lang == 'zh' else f'{en} ({unit})'


def log_axis(ax, which: str = 'x'):
    """Log axis with plain-number major labels (0.01, 0.1, 1, 10) and unlabelled minor ticks."""
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
    axis = ax.xaxis if which == 'x' else ax.yaxis
    (ax.set_xscale if which == 'x' else ax.set_yscale)('log')
    axis.set_major_locator(LogLocator(base=10))
    axis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:g}'))
    axis.set_minor_formatter(NullFormatter())


def fit_height(fig, pad_in: float = 0.03):
    """Trim empty space above / below the content while keeping the exact column width (journals fix the width).

    The height is reduced only while no axes gets narrower, so equal-aspect maps keep their scale."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    if not isinstance(fig.canvas, FigureCanvasAgg):
        FigureCanvasAgg(fig)
    w, h = fig.get_size_inches()
    for _ in range(3):
        fig.canvas.draw()
        widths = [ax.get_position().width * w for ax in fig.axes]
        need = fig.get_tightbbox(fig.canvas.get_renderer()).height + 2 * pad_in
        if h - need < 0.03:
            return
        fig.set_size_inches(w, need)
        fig.canvas.draw()
        if any(ax.get_position().width * w < old - 0.01 for ax, old in zip(fig.axes, widths)):
            fig.set_size_inches(w, h)       # the height became binding: keep the previous one
            return
        h = need


def color(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def marker(i: int) -> str:
    return MARKERS[i % len(MARKERS)]


def dash(i: int):
    return DASHES[i % len(DASHES)]
