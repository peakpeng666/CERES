"""Solve-request validation shared by POST /lab/api/solve and the research agent (CONTRACT §2.4, §9)."""
from __future__ import annotations

from .jobs import resolve_params
from .algorithm_ids import canonical_algorithm_id

MAX_ALGORITHMS = 24
MAX_LABEL = 200


class SolveRequestError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def check_label(label) -> str | None:
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
        raise SolveRequestError(400, 'invalid_request', f'label 须为不超过 {MAX_LABEL} 字的字符串')
    return label


def check_requested(requested, limit: int = MAX_ALGORITHMS) -> list:
    if not isinstance(requested, list) or not requested:
        raise SolveRequestError(400, 'invalid_request', 'algorithms 须为非空数组')
    if len(requested) > limit:
        raise SolveRequestError(400, 'invalid_request', f'一次最多提交 {limit} 个算法')
    return requested


def resolve_algorithms(catalog: list[dict], problem_class: str, requested, limit: int = MAX_ALGORITHMS) -> list[tuple[dict, dict]]:
    """[{"id": …, "params": {…}} | "id", …] → [(catalog entry, resolved params)], with the gateway's error codes."""
    check_requested(requested, limit)
    by_id = {a['id']: a for a in catalog}
    resolved = []
    for item in requested:
        item = {'id': item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise SolveRequestError(400, 'invalid_request', 'algorithms 的每项须为 {"id": …, "params": {…}}')
        algo = by_id.get(canonical_algorithm_id(item['id']))
        if algo is None:
            raise SolveRequestError(400, 'unknown_algorithm', f"没有算法 {item['id']}")
        if problem_class not in algo.get('problem_classes', []):
            raise SolveRequestError(400, 'algorithm_not_applicable', f"{algo['name']} 不适用于 {problem_class} 场景")
        if not algo.get('available'):
            raise SolveRequestError(409, 'algorithm_unavailable',
                                    f"{algo['name']} 当前不可用：{algo.get('unavailable_reason') or '未说明'}")
        if item.get('params') is not None and not isinstance(item['params'], dict):
            raise SolveRequestError(400, 'invalid_params', 'params 须为 JSON 对象')
        try:
            resolved.append((algo, resolve_params(algo, item.get('params'))))
        except ValueError as exc:
            raise SolveRequestError(400, 'invalid_params', str(exc)) from None
    return resolved
