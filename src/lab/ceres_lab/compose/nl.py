"""Parse a one-sentence Chinese (or simple English) task description into a structured task spec."""
from __future__ import annotations

import math
import re

CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
NUM = r'(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)'

KINDS = {
    'sampling': ('采样', r'采样|取样|土壤|测土|样本'),
    'inspection': ('巡检', r'巡检|巡查|巡视|检查|监测|查看|拍照'),
    'spraying': ('植保', r'喷洒|喷药|打药|植保|施肥|灌溉'),
    'harvest': ('采收', r'采摘|收获|采收|摘果|收割'),
    'transport': ('运输', r'运输|搬运|转运|配送|投放|运送|送达'),
    'seeding': ('播种', r'播种|补苗|种植'),
}
OBSTACLES = {
    'pond': ('池塘', r'池塘|水塘|鱼塘|水池|湖|水坑'),
    'building': ('建筑', r'建筑|房屋|房子|厂房|库房|机房|泵房|仓房|平房|楼'),
    'greenhouse': ('温室', r'温室|大棚'),
    'tree': ('树木', r'大树|树木|树|电线杆|电杆|杆塔|立柱|柱子'),
    'ditch': ('沟渠', r'水渠|沟渠|水沟|沟|渠'),
    'rock': ('石块', r'石头|岩石|乱石|石块'),
}
DEPOT = {'西南': 'sw', '左下': 'sw', '东南': 'se', '右下': 'se', '西北': 'nw', '左上': 'nw', '东北': 'ne', '右上': 'ne',
         '中间': 'center', '中心': 'center', '正中': 'center', '中央': 'center', '北': 'n', '南': 's', '东': 'e', '西': 'w',
         '上': 'n', '下': 's', '左': 'w', '右': 'e'}

# Time phrases. A clock reading counts only with a word that gives it a role, so "12 点采样" stays a task count.
CN = '[零〇一二两三四五六七八九十]'
PERIOD = '凌晨|清晨|清早|一大早|一早|早上|早晨|上午|中午|正午|午后|下午|傍晚|晚上|夜里|夜间'
AFTERNOON = ('午后', '下午', '傍晚', '晚上', '夜里', '夜间')
CLOCK = re.compile(rf'(?:(?P<period>{PERIOD})\s*)?(?:(?P<hour>\d{{1,2}}|{CN}{{1,3}})\s*(?:点(?![位数])|时)\s*(?:钟|整)?\s*'
                   rf'(?:(?P<half>半)|(?P<quarter>[一三])刻|(?P<minute>\d{{1,2}}|{CN}{{1,3}})\s*分?(?![\d零〇一二两三四五六七八九十个台辆块处棵株颗]))?'
                   rf'|(?P<colon_h>\d{{1,2}})\s*[:：]\s*(?P<colon_m>\d{{2}})(?!\d)'
                   rf'|(?P<en_h>\d{{1,2}})(?::(?P<en_m>\d{{2}}))?\s*(?P<ampm>am|pm)\b)', re.I)
AMOUNT = rf'\d+(?:\.\d+)?|{CN}+'
DURATION = re.compile(rf'(?P<hours>{AMOUNT})\s*个?(?P<and_half>半)?\s*(?:小时|钟头)(?:\s*(?P<and_minutes>{AMOUNT})\s*分钟?)?'
                      rf'|(?P<half_hour>半)\s*个?\s*(?:小时|钟头)|(?P<quarters>[一二两三])\s*刻钟'
                      rf'|(?P<minutes>{AMOUNT})\s*分钟|(?P<seconds>{AMOUNT})\s*秒钟?'
                      r'|(?P<en>\d+(?:\.\d+)?)\s*(?P<en_unit>hours?|hrs?|minutes?|mins?|seconds?|secs?)\b', re.I)
BY_LEFT = re.compile(r'(?:最迟|最晚|不晚于|不迟于|截止(?:时间|时刻)?|截至|deadline|\bby|\bbefore)\s*(?:到|至|是|为|在|于)?\s*$', re.I)
BY_RIGHT = re.compile(r'\s*(?:之前|以前|前)')
FROM_LEFT = re.compile(r'(?:最早|不早于|\bafter)\s*(?:从|是|为|在|于)?\s*$', re.I)
FROM_RIGHT = re.compile(rf'\s*(?:之后|以后|后)(?!\s*(?:\d|{CN}|半))|\s*起(?!点)')
START_LEFT = re.compile(r'(?:出发|开始|发车|开工|启动)(?:时间|时刻)?\s*(?:定|设)?\s*(?:为|是|在|于)?\s*$|(?:start(?:s|ing)?|depart(?:s|ing)?|leav(?:e|es|ing))\s+at\s*$', re.I)
START_RIGHT = re.compile(r'\s*(?:准时|就|正式)?\s*(?:从[^，,。；;]{0,12}?)?(?:出发|开始|动身|发车|开工|启动|开干|开跑)')
DEPART_WORD = re.compile(r'\s*(?:出发|开始|动身|发车|开工|启动)')
RANGE_GAP = re.compile(r'\s*(?:到|至|-|—|–|~|～)\s*$')
WITHIN = re.compile(r'\s*(?:内|以内|之内)')
AFTER_DEPARTURE = re.compile(r'(?:出发|开始|发车|启动|放行)后?\s*$')
SERVED = re.compile(r'[^，,。；;]{0,8}?(?:送达|送到|到达|赶到|运到|抵达|完成|跑完|做完|干完|服务完|巡检完|采样完|采完|结束|搞定|投放)')
SOLVER_TIME = re.compile(r'算|求解|规划出|运行时间|预算|time limit', re.I)
# What a single shared window cannot say: tasks singled out, or a day other than the mission's own.
SCOPED = re.compile(rf'[A-Za-z甲乙丙丁]\s*(?:区|地块|块)|第\s*(?:\d+|{CN}+)\s*(?:个|号|块|区)|(?:\d+|{CN}+)\s*号(?:点|任务|地块)?|部分|其中|其余|其他|这些|那些|某些|有些|有的|一半|'
                    rf'前\s*(?:\d+|{CN}+)\s*个|后\s*(?:\d+|{CN}+)\s*个|重点|优先|紧急')
DATED = re.compile(rf'明天|明早|明晚|后天|下周|下星期|星期|礼拜|周[一二三四五六日天]|\d+\s*月\s*\d+\s*[日号]|每天|每日')
WINDOW_WORD = re.compile(r'时间窗|时段|准时|按时|截止|最迟|最晚(?!完成)|不晚于|不迟于|不早于|最早(?!完成)|deadline|time windows?', re.I)
CLAUSE = re.compile(r'[^，,。；;！!？?\n]+')


def cn_number(text: str) -> float | None:
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return float(text)
    total, section, digit = 0, 0, None
    for ch in text:
        if ch in CN_DIGITS:
            digit = CN_DIGITS[ch]
        elif ch in '十百千':
            unit = {'十': 10, '百': 100, '千': 1000}[ch]
            section += (digit if digit is not None else 1) * unit
            digit = None
        else:
            return None
    total = section + (digit or 0)
    return float(total) if text else None


def _find(pattern: str, text: str):
    m = re.search(pattern, text, re.I)
    if not m:
        return None, None
    nums = [cn_number(g) for g in m.groups() if g is not None and cn_number(g) is not None]
    return nums, m.group(0)


def _clock_of(m: re.Match) -> str | None:
    """24-hour ``HH:MM`` of one CLOCK match, or None for a reading that is not a time of day."""
    groups = m.groupdict()
    if groups['colon_h'] is not None:
        hour, minute = int(groups['colon_h']), int(groups['colon_m'])
    elif groups['en_h'] is not None:
        hour, minute = int(groups['en_h']) % 12 + 12 * (groups['ampm'].lower() == 'pm'), int(groups['en_m'] or 0)
    else:
        hour = int(cn_number(groups['hour']))
        minute = 30 if groups['half'] else {'一': 15, '三': 45}.get(groups['quarter'], 0)
        if groups['minute'] is not None:
            minute = int(cn_number(groups['minute']))
    period = groups['period']
    if period in AFTERNOON and hour < 12 or period in ('中午', '正午') and hour < 6:
        hour += 12
    if period == '凌晨' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}' if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def _seconds_of(m: re.Match) -> float | None:
    groups = m.groupdict()
    if groups['half_hour']:
        return 1800.0
    if groups['quarters']:
        return 900.0 * cn_number(groups['quarters'])
    if groups['en']:
        return float(groups['en']) * {'h': 3600, 'm': 60, 's': 1}[groups['en_unit'][0].lower()]
    if groups['hours'] is not None:
        return 3600.0 * cn_number(groups['hours']) + 1800.0 * bool(groups['and_half']) + 60.0 * (cn_number(groups['and_minutes'] or '0') or 0)
    if groups['minutes'] is not None:
        return 60.0 * cn_number(groups['minutes'])
    return cn_number(groups['seconds'])


def clock_value(text: str) -> str | None:
    """``09:00`` from a written time of day such as 上午九点、9:30、8点一刻, or None."""
    value = str(text or '').strip()
    if re.fullmatch(r'\d{1,2}', value) and int(value) <= 23:
        return f'{int(value):02d}:00'
    m = CLOCK.search(value)
    return _clock_of(m) if m else None


def duration_value(text: str) -> float | None:
    """Seconds from a written duration such as 半小时、一个半小时、45 分钟, or None."""
    m = DURATION.search(str(text or ''))
    return _seconds_of(m) if m else None


def _clause(text: str, at: int) -> str:
    return next((m.group(0).strip() for m in CLAUSE.finditer(text) if m.start() <= at < m.end()), '')


def _clock_roles(t: str) -> list[dict]:
    """Every clock reading that has a role here: when the fleet leaves, or a bound on when a task is reached."""
    matches = list(CLOCK.finditer(t))
    out, taken = [], set()
    for i, m in enumerate(matches):
        if i in taken:
            continue
        left, right = t[:m.start()], t[m.end():]
        value = _clock_of(m)
        nxt = matches[i + 1] if i + 1 < len(matches) else None
        role, begin, end = None, m.start(), m.end()
        if nxt and RANGE_GAP.fullmatch(t[m.end():nxt.start()]):
            # Two ends of one window, the second borrowing the first's morning or afternoon: 下午两点到五点.
            late = _clock_of(nxt)
            if late and value and not nxt.group('period') and m.group('period') in AFTERNOON and int(late[:2]) < 12:
                late = f'{int(late[:2]) + 12:02d}:{late[3:]}'
            taken.add(i + 1)
            tail = re.match(r'\s*(?:之间|之内|以内|内)?', t[nxt.end():])
            out.append({'role': 'earliest', 'value': value, 'at': m.start(), 'span': (m.start(), m.end()), 'period': m.group('period')})
            out.append({'role': 'deadline', 'value': late, 'at': nxt.start(), 'span': (nxt.start(), nxt.end() + tail.end()), 'period': nxt.group('period') or m.group('period')})
            continue
        by, by_left = BY_RIGHT.match(right), BY_LEFT.search(left)
        since, since_left = FROM_RIGHT.match(right), FROM_LEFT.search(left)
        if by and DEPART_WORD.match(right[by.end():]):
            role = 'departs_by'                 # 七点前出发 bounds the departure, which the model has no field for
            end += by.end()
        elif by or by_left:
            role = 'deadline'
            begin, end = by_left.start() if by_left else begin, end + (by.end() if by else 0)
        elif since or since_left:
            role = 'earliest'
            begin, end = since_left.start() if since_left else begin, end + (since.end() if since else 0)
        elif START_RIGHT.match(right) or START_LEFT.search(left):
            role = 'start'
        elif m.group('period'):
            role = 'unknown'                    # 上午九点送到 names a time and leaves open what it binds
        if role:
            out.append({'role': role, 'value': value, 'at': m.start(), 'span': (begin, end), 'period': m.group('period')})
    return out


def _duration_roles(t: str) -> list[dict]:
    """Durations bound to the departure: 出发后 30 分钟内, 半小时内全部送达, 发车 10 分钟后."""
    out = []
    for m in DURATION.finditer(t):
        left, right = t[:m.start()], t[m.end():]
        within = WITHIN.match(right)
        since = re.match(r'\s*(?:之后|以后|后)', right)
        clause = _clause(t, m.start())
        value = _seconds_of(m)
        if within and not SOLVER_TIME.search(clause) and (AFTER_DEPARTURE.search(left) or SERVED.match(right[within.end():])):
            out.append({'role': 'deadline', 'value': f'{value:g}s', 'at': m.start(), 'span': (m.start(), m.end() + within.end())})
        elif since and AFTER_DEPARTURE.search(left) and not SOLVER_TIME.search(clause):
            out.append({'role': 'earliest', 'value': f'{value:g}s', 'at': m.start(), 'span': (m.start(), m.end() + since.end())})
    return out


def _same_half_day(understood: list[tuple], usable: list[dict]) -> list[tuple]:
    """A bare hour takes the half of the day the rest of the sentence is in: 下午两点出发，五点前送到; 一点出发，下午两点到五点送到."""
    bare = {p['role'] for p in usable if not p.get('period') and ':' in p['value']}
    names = {'start_clock': 'start', 'deadline': 'deadline', 'earliest': 'earliest'}
    later = lambda v: f'{int(v[:2]) + 12:02d}:{v[3:]}' if int(v[:2]) < 12 else v  # noqa: E731
    start = next((u[2] for u in understood if u[0] == 'start_clock'), None)
    bounds = [u[2] for u in understood if u[0] != 'start_clock' and ':' in u[2]]
    if not start or not bounds:
        return understood
    out = []
    for key, label, value, snippet in understood:
        if names[key] in bare and ':' in value:
            if key == 'start_clock' and min(bounds) >= '12:00' and later(value) <= min(bounds):
                value = later(value)
            elif key != 'start_clock' and value <= start < later(value):
                value = later(value)
        out.append((key, label, value, snippet))
    return out


def _time_phrases(t: str) -> tuple[str, list[tuple], list[str]]:
    """Reads the time phrases out of a request. Returns the text with them blanked, so that 九点 is not counted as nine task
    points; ``(key, label, value, snippet)`` for each one understood; and the clauses that cannot be held by one shared window."""
    phrases = sorted(_clock_roles(t) + _duration_roles(t), key=lambda p: p['at'])
    masked, understood, unsupported = t, [], []

    def refuse(clause):
        if clause and clause not in unsupported:
            unsupported.append(clause)

    for p in phrases:
        a, b = p['span']
        masked = masked[:a] + ' ' * (b - a) + masked[b:]
    usable = []
    for p in phrases:
        clause = _clause(t, p['at'])
        if p['role'] in ('unknown', 'departs_by') or not p['value'] or SCOPED.search(clause) or DATED.search(clause):
            refuse(clause)
        else:
            usable.append(p)
    labels = {'start': ('start_clock', '出发时刻'), 'deadline': ('deadline', '截止时间'), 'earliest': ('earliest', '最早时间')}
    for role, (key, label) in labels.items():
        mine = [p for p in usable if p['role'] == role]
        if len({p['value'] for p in mine}) > 1:     # several values for one shared field: which task each binds is not stated
            for p in mine:
                refuse(_clause(t, p['at']))
        elif mine:
            understood.append((key, label, mine[0]['value'], t[mine[0]['span'][0]:mine[0]['span'][1]].strip()))
    understood = _same_half_day(understood, usable)
    for m in CLAUSE.finditer(masked):
        if WINDOW_WORD.search(m.group(0)):
            refuse(_clause(t, m.start() + WINDOW_WORD.search(m.group(0)).start()))
    return masked, understood, unsupported


def parse(text: str) -> dict:
    original = (text or '').strip()
    t, times, unsupported = _time_phrases(original)
    spec, found = {}, []

    def note(key, label, value, snippet):
        spec[key] = value
        found.append({'key': key, 'label': label, 'value': value, 'text': snippet})

    for key, label, value, snippet in times:
        note(key, label, value, snippet)
    nums, s = _find(NUM + r'\s*(?:米|m)?\s*[×xX*＊乘]\s*' + NUM + r'\s*(?:米|m)', t)
    if nums and len(nums) == 2:
        note('size', '场地', [nums[0], nums[1]], s)
    else:
        nums, s = _find(r'长\s*' + NUM + r'\s*(?:米|m).{0,6}?宽\s*' + NUM + r'\s*(?:米|m)', t)
        if nums and len(nums) == 2:
            note('size', '场地', [nums[0], nums[1]], s)
    if 'size' not in spec:
        nums, s = _find(NUM + r'\s*(公顷|ha|hectares?)', t)
        if nums:
            note('area_m2', '面积', nums[0] * 10000, s)
        else:
            nums, s = _find(NUM + r'\s*亩', t)
            if nums:
                note('area_m2', '面积', nums[0] * 666.67, s)
    nums, s = _find(NUM + r'\s*(?:台|辆|部|架)', t)
    if not nums:
        nums, s = _find(NUM + r'\s*个?\s*(?:机器人|无人车|AGV|小车|robots?|vehicles?)', t)
    if nums:
        note('vehicles', '车辆', int(nums[0]), s)
    nums, s = _find(NUM + r'\s*(?:个|棵|株|颗|处)?\s*(?:采样点|取样点|任务点|点位|巡检点|采摘点|投放点|监测点|作业点|目标点|样点|植株|果树|站点|点|处|tasks?|points?)', t)
    if nums:
        note('tasks', '任务点', int(nums[0]), s)
    nums, s = _find(r'(?:载重|容量|载量|装载量)\s*(?:为|是|都是)?\s*' + NUM, t)
    if not nums:
        nums, s = _find(r'每(?:台|辆|个)?(?:车|机器人)?\s*(?:最多|最大)?\s*(?:能|可)?(?:装|载|带|采|运|服务)\s*' + NUM, t)
    if not nums:
        nums, s = _find(r'(?:capacity|load)\s*(?:of)?\s*' + NUM, t)
    if nums:
        note('capacity', '单车容量', int(nums[0]), s)
    nums, s = _find(r'每个?(?:点|任务点|采样点|样点)\s*(?:需求|重量|需要|装)?\s*' + NUM, t)
    if nums:
        note('demand', '单点需求', int(nums[0]), s)
    nums, s = _find(NUM + r'\s*(?:块|个|片)\s*(?:地块|田块|田|地|果园|菜地|区)', t)
    if not nums:
        nums, s = _find(r'分(?:成|为)\s*' + NUM + r'\s*(?:块|个|片|区)', t)
    if nums:
        note('plots', '地块', int(nums[0]), s)
    obstacles = {}
    for key, (label, words) in OBSTACLES.items():
        # "果树" is usually the work target, not an obstacle.
        scope = re.sub(r'果树', '', t) if key == 'tree' else t
        m = re.search(r'(' + NUM[1:-1] + r')?\s*(?:个|处|座|栋|片|棵|根|条|间|口)?\s*(?:' + words + r')', scope)
        if m and not (key == 'greenhouse' and not re.search(r'避开|绕开|绕过|躲开|障碍|不能进', t)):
            count = cn_number(m.group(1)) if m.group(1) else None
            obstacles[key] = int(count) if count else (6 if key == 'tree' else 1)
            found.append({'key': f'obstacle.{key}', 'label': label, 'value': obstacles[key], 'text': m.group(0)})
    if obstacles:
        spec['obstacles'] = obstacles
    elif re.search(r'没有障碍|无障碍|空旷', t):
        spec['obstacles'] = {}
        found.append({'key': 'obstacles', 'label': '障碍', 'value': 0, 'text': re.search(r'没有障碍|无障碍|空旷', t).group(0)})
    for key, (label, words) in KINDS.items():
        m = re.search(words, t)
        if m:
            note('kind', '作业类型', key, m.group(0))
            break
    m = re.search(r'聚集|成簇|扎堆|集中|几片|一簇|clustered', t)
    if m:
        note('distribution', '分布', 'clustered', m.group(0))
    else:
        m = re.search(r'成行|沿行|沿着?作物行|垄|行间|一排排|rows?', t)
        if m:
            note('distribution', '分布', 'rows', m.group(0))
        else:
            m = re.search(r'均匀|随机|分散|散布|uniform', t)
            if m:
                note('distribution', '分布', 'uniform', m.group(0))
    m = re.search(r'(西南|东南|西北|东北|左下|右下|左上|右上|中间|中心|正中|中央|北|南|东|西)(?:角|侧|边|部|面)?(?:的)?\s*(?:车库|仓库|充电站|基地|出发|机库|停车)', t) or \
        re.search(r'(?:车库|仓库|充电站|基地|机库)(?:在|位于|设在)\s*(西南|东南|西北|东北|左下|右下|左上|右上|中间|中心|正中|中央|北|南|东|西)', t)
    if m:
        note('depot', '车库', DEPOT[m.group(1)], m.group(0))
    m = re.search(r'均衡|平均分配|同时完成|尽快完成|最快完成|完成时间|时间最短|最早完成|最短时间|makespan|balanced', t)
    if m:
        note('objective', '优化目标', 'makespan', m.group(0))
    else:
        m = re.search(r'总路程|总里程|总距离|路程最短|里程最短|最省|最少里程|total distance', t)
        if m:
            note('objective', '优化目标', 'total_distance', m.group(0))
    nums, s = _find(r'(?:速度|车速)\s*(?:为|是)?\s*' + NUM + r'\s*(米/秒|m/s|米每秒|公里/小时|km/h|千米/小时)', t)
    if nums:
        unit = re.search(r'(米/秒|m/s|米每秒|公里/小时|km/h|千米/小时)', s).group(1)
        note('speed_mps', '车速', nums[0] / 3.6 if 'h' in unit or '小时' in unit else nums[0], s)
    m = re.search(r'不规则|多边形|L\s*形|异形|irregular', t)
    if m:
        note('shape', '边界形状', 'L' if 'L' in m.group(0).upper() else 'irregular', m.group(0))
    nums, s = _find(r'(?:种子|seed)\s*' + NUM, t)
    if nums:
        note('seed', '随机种子', int(nums[0]), s)
    nums, s = _find(r'(?:车宽|机器人宽|宽度)\s*' + NUM + r'\s*(?:米|m)', t)
    if nums:
        note('robot_width', '车宽', nums[0], s)
    return {'spec': spec, 'found': found, 'text': original, 'unsupported': unsupported}


def fill_defaults(spec: dict) -> tuple[dict, list[dict]]:
    """Complete a parsed spec with sensible agricultural defaults; returns (full spec, defaults used)."""
    out, used = dict(spec), []

    def default(key, label, value):
        if key not in out:
            out[key] = value
            used.append({'key': key, 'label': label, 'value': value})
    if 'size' not in out and 'area_m2' in out:
        side = math.sqrt(out['area_m2'])
        out['size'] = [round(side * 1.4, 1), round(side / 1.4, 1)]
    default('size', '场地', [160.0, 100.0])
    default('kind', '作业类型', 'sampling')
    default('vehicles', '车辆', 3)
    default('tasks', '任务点', 30)
    default('plots', '地块', 4)
    default('distribution', '分布', 'rows' if out['kind'] in ('spraying', 'harvest', 'seeding') else 'uniform')
    default('obstacles', '障碍', {'pond': 1, 'building': 1})
    default('depot', '车库', 'sw')
    default('objective', '优化目标', 'total_distance')
    default('speed_mps', '车速', 1.0)
    default('robot_width', '车宽', 1.0)
    default('shape', '边界形状', 'rect')
    return out, used
