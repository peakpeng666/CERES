"""工作台核心：场景、求解适配、独立检查、推荐、导出与仿真计划。"""
import os
from .env_compat import normalize_environment
os.environ.update(normalize_environment(os.environ))

from . import compat  # noqa: F401  must precede every module that touches processes

__version__ = '0.1.0'
