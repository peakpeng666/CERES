"""Loopback stand-in for an OpenAI-compatible chat/completions endpoint, used only by browser_check.sh.

It is not a language model: it plays a fixed research script from the conversation state, so the check
exercises the real HTTP client, agent runtime, workbench tools, solvers, checkers and panel. It enforces
the protocol rules a strict provider applies (Bearer header, tools offered, system prompt first, every
assistant tool call answered by a tool message with the same tool_call_id) and answers violations with
HTTP 400. GET /log returns one entry per request.

    python stub_llm.py PORT
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []


def tool_call(cid, name, args):
    return {'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}


def protocol_errors(body, headers):
    errors = []
    if not (headers.get('Authorization') or '').startswith('Bearer '):
        errors.append('missing bearer key')
    if not body.get('tools') or body.get('tool_choice') != 'auto':
        errors.append('tools not offered')
    msgs = body.get('messages') or []
    if not msgs or msgs[0].get('role') != 'system':
        errors.append('first message is not the system prompt')
    open_ids = []
    for m in msgs:
        if m.get('role') == 'tool':
            if m.get('tool_call_id') in open_ids:
                open_ids.remove(m['tool_call_id'])
            else:
                errors.append(f"tool message answers unknown call {m.get('tool_call_id')}")
            continue
        if open_ids:
            errors.append(f"unanswered tool calls before {m.get('role')}: {open_ids}")
        open_ids = [c['id'] for c in m.get('tool_calls') or []] if m.get('role') == 'assistant' else []
    if open_ids:
        errors.append(f'unanswered tool calls at end: {open_ids}')
    return errors


def tool_results(msgs, since=0):
    names = {c['id']: c['function']['name'] for m in msgs if m['role'] == 'assistant' for c in m.get('tool_calls') or []}
    return [(names.get(m['tool_call_id']), json.loads(m['content'])) for m in msgs[since:] if m['role'] == 'tool']


def checked(run):
    return run.get('state') == 'done' and (run.get('check') or {}).get('valid') is True


def research_turn(text, done, turn):
    got = dict(done)
    if not done:
        return [tool_call(f'c{turn}a', 'compose_scenario', {'text': text.split('。')[0], 'seed': 7})], '先按描述生成任务地图。'
    sid = got['compose_scenario']['scenario_id']
    if 'recommend' not in got:
        return [tool_call(f'c{turn}b', 'recommend', {'scenario_id': sid}),
                tool_call(f'c{turn}c', 'list_algorithms', {'problem_class': 'fleet_routing'})], '同时读取规则推荐和算法目录。'
    if 'solve' not in got:
        available = {a['id'] for a in got['list_algorithms']['algorithms'] if a.get('available')}
        algos = [a for a in ('pyvrp.fleet', 'ortools.fleet') if a in available] or ['panel.savings_fleet']
        return [tool_call(f'c{turn}d', 'solve', {'scenario_id': sid,
                                                 'algorithms': [{'id': a, 'params': {'time_limit_s': 3}} for a in algos]})], None
    if 'wait_for_runs' not in got:
        return [tool_call(f'c{turn}e', 'wait_for_runs', {'run_ids': got['solve']['run_ids'], 'timeout_s': 90})], None
    if 'compare_runs' not in got:
        return [tool_call(f'c{turn}f', 'compare_runs', {'run_ids': got['solve']['run_ids']})], None
    runs = got['wait_for_runs']['runs']
    lines = []
    for r in runs:
        value, unit = r['objective'].get('value'), r['objective'].get('unit') or ''
        shown = f'{value:.1f} {unit}' if isinstance(value, (int, float)) else '无'
        lines.append(f"- {r['algorithm']['id']}（{r['run_id']}）：目标值 {shown}，{'通过独立检查' if checked(r) else '未通过（' + str(r.get('state')) + '）'}")
    valid = [r for r in runs if checked(r)]
    if valid:
        best = min(r['objective']['value'] for r in valid)
        leaders = [r['algorithm']['id'] for r in valid if abs(r['objective']['value'] - best) <= 1e-9]
        unit = valid[0]['objective'].get('unit') or ''
        head = (f"结论：场景 {sid} 上 {'、'.join(leaders)} {'并列' if len(leaders) > 1 else ''}总里程最短（{best:.1f} {unit}）。")
    else:
        head = f'结论：场景 {sid} 上没有运行通过独立检查，不能给出最优方案。'
    return '\n'.join([head, '', '依据：', *lines, '', '局限：每个算法只运行一次、各 3 秒，没有重复种子，不能据此推断统计显著性；'
                      '这是规划层面的米制路径，没有在 Gazebo 中执行。']), None


def simulation_turn(msgs, done, turn):
    if not done:
        runs = [r for name, res in tool_results(msgs) if name == 'wait_for_runs' for r in res.get('runs', []) if checked(r)]
        if not runs:
            return '结论：还没有通过独立检查的方案，不能启动仿真。', None
        best = min(runs, key=lambda r: r['objective']['value'])
        return [tool_call(f'c{turn}s', 'start_field_simulation', {'run_id': best['run_id']})], '启动仿真会驱动 Gazebo，需要用户批准。'
    result = done[-1][1]
    if result.get('denied'):
        return '结论：你没有批准，所以没有启动田间仿真。\n\n依据：工具调用被拒绝，规划结果保持不变。\n\n局限：方案目前只经过规划层面的独立检查，执行层面尚未验证。', None
    return f"结论：已提交仿真会话 {result.get('session_id')}。", None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/log':
            return self.reply(200, LOG)
        self.reply(404, {'error': {'message': 'not found'}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        errors = protocol_errors(body, self.headers)
        if self.path != '/v1/chat/completions':
            errors.append(f'bad path {self.path}')
        msgs = body.get('messages') or []
        LOG.append({'messages': len(msgs), 'model': body.get('model'), 'tools': len(body.get('tools') or []), 'errors': errors})
        if errors:
            return self.reply(400, {'error': {'message': '; '.join(errors)}})
        last_user = max(i for i, m in enumerate(msgs) if m['role'] == 'user')
        text, done, turn = msgs[last_user]['content'], tool_results(msgs, last_user + 1), sum(m['role'] == 'user' for m in msgs)
        step, reasoning = simulation_turn(msgs, done, turn) if '仿真' in text else research_turn(text, done, turn)
        message = {'role': 'assistant', 'content': step if isinstance(step, str) else None}
        if not isinstance(step, str):
            message['tool_calls'] = step
        if reasoning:
            message['reasoning_content'] = reasoning
        self.reply(200, {'id': f'stub-{len(LOG)}', 'object': 'chat.completion', 'model': body.get('model'),
                         'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop' if isinstance(step, str) else 'tool_calls'}],
                         'usage': {'prompt_tokens': 900 + 40 * len(msgs), 'completion_tokens': 60, 'total_tokens': 960 + 40 * len(msgs)}})

    def reply(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
