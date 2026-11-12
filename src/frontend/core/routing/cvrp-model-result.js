/* Offline model-result bridge. Geometry is checked here; producer/transport/timing claims are unsigned. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./cvrp-result.js'), require('./cvrp-checker.js'));
  } else root.CVRPModelResult = factory(root.CVRPResult, root.CVRPChecker);
}(typeof globalThis !== 'undefined' ? globalThis : this, function (Reference, Checker) {
  'use strict';
  const SCHEMA = 'ceres-cvrp-model-result/1', MAX_BYTES = 256 * 1024, MAX_CUSTOMERS = 20;
  const MODEL = Object.freeze({
    repository: 'Qwen/Qwen3-4B-GGUF',
    revision: 'bc640142c66e1fdd12af0bd68f40445458f3869b',
    filename: 'Qwen3-4B-Q4_K_M.gguf',
    sha256: '7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5'
  });
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const finite = value => typeof value === 'number' && Number.isFinite(value);

  // Count UTF-8 bytes without depending on Node or TextEncoder in the offline page.
  function byteLength(text) {
    let bytes = 0;
    for (const char of text) {
      const point = char.codePointAt(0);
      bytes += point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4;
    }
    return bytes;
  }

  // JSON.parse loses duplicate keys and the distinction between 1, 1.0 and 1e0.
  // Parse JSON grammar directly, using JSON.parse only to decode one quoted string.
  // No trimming, code evaluation, fenced-block extraction or candidate repair occurs.
  function strictJSON(text, integerNumbers) {
    if (typeof text !== 'string') throw Error('需要原始 JSON 文本');
    if (byteLength(text) > MAX_BYTES) throw Error('结果文件超过 256 KiB');
    let offset = 0;
    const fail = message => { throw Error(message + '（位置 ' + offset + '）'); };
    const whitespace = () => { while (/[\x20\t\n\r]/.test(text[offset] || '\u0000')) offset++; };
    function string() {
      const start = offset++;
      while (offset < text.length) {
        const char = text[offset++];
        if (char === '"') {
          try { return JSON.parse(text.slice(start, offset)); }
          catch (_) { fail('JSON 字符串无效'); }
        }
        if (char === '\\') offset++;
      }
      fail('JSON 字符串未结束');
    }
    function value(depth) {
      if (depth > 64) fail('JSON 嵌套过深');
      whitespace();
      const char = text[offset];
      if (char === '"') return string();
      if (char === '{') {
        offset++;
        whitespace();
        const result = {}, keys = new Set();
        if (text[offset] === '}') { offset++; return result; }
        for (;;) {
          whitespace();
          if (text[offset] !== '"') fail('JSON 对象键无效');
          const key = string();
          if (keys.has(key)) fail('JSON 包含重复键 ' + key);
          keys.add(key);
          whitespace();
          if (text[offset++] !== ':') fail('JSON 对象缺少冒号');
          Object.defineProperty(result, key, {value: value(depth + 1), enumerable: true, writable: true, configurable: true});
          whitespace();
          const delimiter = text[offset++];
          if (delimiter === '}') return result;
          if (delimiter !== ',') fail('JSON 对象分隔符无效');
        }
      }
      if (char === '[') {
        offset++;
        whitespace();
        const result = [];
        if (text[offset] === ']') { offset++; return result; }
        for (;;) {
          result.push(value(depth + 1));
          whitespace();
          const delimiter = text[offset++];
          if (delimiter === ']') return result;
          if (delimiter !== ',') fail('JSON 数组分隔符无效');
        }
      }
      for (const [literal, decoded] of [['true', true], ['false', false], ['null', null]]) {
        if (text.startsWith(literal, offset)) { offset += literal.length; return decoded; }
      }
      const token = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(offset));
      if (!token) fail('JSON 值无效');
      offset += token[0].length;
      const decoded = Number(token[0]);
      if (!Number.isFinite(decoded)) fail('JSON 数字必须有限');
      if (integerNumbers && (!/^-?\d+$/.test(token[0]) || !Number.isSafeInteger(decoded))) {
        fail('节点编号必须使用安全整数 JSON 语法，不能使用小数或指数');
      }
      return decoded;
    }
    const result = value(0);
    whitespace();
    if (offset !== text.length) fail('JSON 后有额外内容');
    return result;
  }

  function parseCandidate(contentText) {
    const parsed = strictJSON(contentText, true);
    if (!object(parsed) || Object.keys(parsed).length !== 1 || !Object.hasOwn(parsed, 'routes')) {
      throw Error('候选 JSON 必须且只能包含 routes 字段');
    }
    if (!Array.isArray(parsed.routes) || parsed.routes.some(route =>
      !Array.isArray(route) || route.some(node => !Number.isSafeInteger(node)))) {
      throw Error('候选 routes 必须为整数节点编号组成的路线数组');
    }
    return parsed;
  }

  // This is checker-derived input, never a distance claimed by the model.
  // Checker.check separately recomputes geometry and checks every CVRP constraint.
  function routeCost(instance, routes) {
    if (!Array.isArray(routes)) throw Error('路线集合无效');
    let total = 0;
    for (const route of routes) {
      if (!Array.isArray(route) || route.some(node =>
        !Number.isSafeInteger(node) || node < 0 || node >= instance.coordinates.length)) {
        throw Error('路线含未知节点或形状错误');
      }
      let distance = 0;
      for (let index = 1; index < route.length; index++) {
        const a = instance.coordinates[route[index - 1]], b = instance.coordinates[route[index]];
        const dx = a[0] - b[0], dy = a[1] - b[1];
        distance += Math.sqrt(dx * dx + dy * dy);
      }
      total += distance;
    }
    if (!Number.isFinite(total)) throw Error('复算距离必须有限');
    return total;
  }

  function readRecord(raw) {
    if (typeof raw === 'string') return strictJSON(raw, false);
    // Parsed-object callers cannot retain outer lexical details, but candidate text
    // still does. Reject values that JSON.stringify would silently coerce or omit.
    const seen = new Set();
    function validate(value, depth) {
      if (depth > 64) throw Error('JSON 嵌套过深');
      if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
      if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw Error('JSON 数字必须有限');
        return;
      }
      if (!object(value) && !Array.isArray(value)) throw Error('结果必须为 JSON 数据');
      if (seen.has(value)) throw Error('结果不能包含循环引用');
      seen.add(value);
      for (const child of Object.values(value)) validate(child, depth + 1);
      seen.delete(value);
    }
    validate(raw, 0);
    if (byteLength(JSON.stringify(raw)) > MAX_BYTES) throw Error('结果文件超过 256 KiB');
    return raw;
  }

  function inspect(raw, instance, units) {
    const record = readRecord(raw);
    if (!object(record) || record.schema_version !== SCHEMA) throw Error('需要 ' + SCHEMA + ' 结果文件');
    if (record.input_profile !== 'cvrp-workbench-input-v1') throw Error('输入合同不匹配');
    const wanted = Reference.binding(instance, units), received = Reference.binding(record.input, record.input?.units);
    if (JSON.stringify(wanted) !== JSON.stringify(received)) {
      throw Error('任务坐标、需求、容量或单位与当前页面不一致；请先导入对应方案参数');
    }
    if (instance.coordinates.length - 1 > MAX_CUSTOMERS) throw Error('模型文件桥只支持 1–20 个任务点');
    const reference = Reference.inspect(record.reference, instance, units);
    if (!reference.eligible) throw Error('PyVRP 参考记录未通过状态或预算合同检查');
    if (!object(record.model) || Object.entries(MODEL).some(([key, value]) => record.model[key] !== value)) {
      throw Error('模型仓库、修订、文件名或 SHA-256 声明与固定配置不匹配');
    }
    if (!object(record.runtime)) throw Error('运行记录缺少 runtime 对象');
    if (!object(record.scope)) throw Error('运行记录缺少 scope 对象');
    const candidate = record.candidate;
    if (!object(candidate) || (candidate.content_text !== null && typeof candidate.content_text !== 'string') ||
        typeof candidate.transport_valid !== 'boolean' || typeof candidate.route_feasible !== 'boolean' ||
        !Array.isArray(candidate.errors) || candidate.errors.some(error => typeof error !== 'string')) {
      throw Error('候选记录结构无效');
    }
    let candidateRoutes = null, geometryCheck;
    try {
      const parsed = parseCandidate(candidate.content_text);
      const reportedCost = routeCost(instance, parsed.routes);
      geometryCheck = Checker.check(instance, {routes: parsed.routes, reported_cost: reportedCost});
      if (geometryCheck.accepted) candidateRoutes = parsed.routes;
    } catch (error) {
      geometryCheck = {accepted: false, errors: [error.message], recomputed_cost: null, loads: [], distances: []};
    }
    const errors = [...geometryCheck.errors];
    if (!candidate.transport_valid) errors.unshift('外来记录声明传输失败；候选不参与选择');
    const candidateCheck = {...geometryCheck,
      accepted: candidate.transport_valid && geometryCheck.accepted,
      geometry_accepted: geometryCheck.accepted, transport_authenticated: false,
      cost_source: 'independent_geometry_checker', errors,
      recomputed_cost: candidate.transport_valid && geometryCheck.accepted ? geometryCheck.recomputed_cost : null};
    const candidateCost = candidateCheck.recomputed_cost, referenceCost = reference.check.recomputed_cost;
    const tolerance = 1e-9 * Math.max(1, referenceCost, candidateCost === null ? 0 : candidateCost);
    const improves = candidateCheck.accepted && candidateCost < referenceCost - tolerance;
    const selected = improves ? 'model' : 'pyvrp_reference';
    const reason = !candidateCheck.accepted ? 'candidate_invalid' : improves ? 'candidate_strictly_better' : 'candidate_not_better';
    const output = {routes: improves ? candidateRoutes : reference.output.routes,
      reported_cost: improves ? candidateCost : referenceCost};
    const sameCost = (declared, derived) => finite(declared) && declared >= 0 &&
      Math.abs(declared - derived) <= 1e-9 * Math.max(1, derived);
    if (candidate.route_feasible !== candidateCheck.accepted ||
        (candidateCost === null ? candidate.recomputed_cost !== null : !sameCost(candidate.recomputed_cost, candidateCost))) {
      throw Error('候选可行性或距离声明与浏览器独立检查不一致');
    }
    const selection = record.selection;
    if (!object(selection) || selection.selected !== selected || selection.reason !== reason ||
        !finite(selection.distance_tolerance) ||
        // Python fsum/hypot and browser addition/sqrt can differ by a few ulps.
        // This only checks the imported claim; the selection above always uses
        // the independently derived tolerance, never the imported value.
        Math.abs(selection.distance_tolerance - tolerance) > 1e-12 * tolerance ||
        JSON.stringify(selection.delivered_routes) !== JSON.stringify(output.routes) ||
        !sameCost(selection.delivered_cost, output.reported_cost)) {
      throw Error('交付选择、路线、距离或容差声明与独立复算不一致');
    }
    const check = Checker.check(instance, output);
    if (!check.accepted) throw Error('交付路线独立检查失败：' + check.errors.join('；'));
    return {record, reference, candidate_check: candidateCheck, candidate_routes: candidateRoutes,
      selected, reason, tolerance, output, check,
      provenance_authenticated: false, timing_authenticated: false, transport_authenticated: false};
  }
  return {SCHEMA, MAX_BYTES, MAX_CUSTOMERS, MODEL, inspect, parseCandidate, routeCost};
}));
