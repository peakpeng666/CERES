"""Research resource libraries: CVRPLIB benchmark instances, Fields2Benchmark real fields, references."""
from __future__ import annotations

from .. import paths

CACHE = paths.RUNTIME / 'library'


def overview() -> dict:
    from . import cvrplib, fields, references
    return {'cvrplib': cvrplib.status(), 'fields': fields.status(), 'references': references.status()}
