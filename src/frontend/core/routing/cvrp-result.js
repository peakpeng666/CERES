/* File bridge: validate geometry/feasibility, never authenticate imported claims. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./cvrp-problem.js'), require('./cvrp-checker.js'));
  } else root.CVRPResult = factory(root.CVRPProblem, root.CVRPChecker);
}(typeof globalThis !== 'undefined' ? globalThis : this, function (Problem, Checker) {
  'use strict';
  const SCHEMA = 'ceres-cvrp-solver-result/1', MAX_BYTES = 256 * 1024;
  function binding(instance, units) {
    Problem.validate(instance);
    if (instance.objective !== 'sum_unrounded_euclidean_distance') throw Error('不支持的目标函数');
    if (!['abstract', 'm'].includes(units)) throw Error('未知坐标单位');
    return {coordinates: instance.coordinates, demands: instance.demands,
      capacity: instance.capacity, depot: 0, fleet: instance.fleet,
      objective: instance.objective, units};
  }
  function inspect(raw, instance, units) {
    if (!raw || raw.schema_version !== SCHEMA) throw Error('需要 ' + SCHEMA + ' 结果文件');
    if (raw.input_profile !== 'cvrp-workbench-input-v1') throw Error('输入合同不匹配');
    const wanted = binding(instance, units), received = binding(raw.input, raw.input?.units);
    if (JSON.stringify(wanted) !== JSON.stringify(received)) throw Error('任务坐标、需求、容量或单位与当前页面不一致；请先导入对应方案参数');
    if (raw.algorithm !== 'pyvrp_ils_savings_warm' || raw.solver_version !== '0.14.0' || raw.seed !== 11) {
      throw Error('结果声明不是固定 PyVRP 0.14.0 Savings warm-start / seed 11');
    }
    const cfg = raw.config;
    if (!cfg || cfg.cpu_budget_seconds !== 1 || cfg.wall_budget_seconds !== 3 ||
        cfg.pyvrp_iterations !== 1000000 || cfg.pyvrp_cpu_return_margin_seconds !== 0.02 ||
        cfg.common_cpu_return_margin_seconds !== 0.02) throw Error('结果声明的预算合同不匹配');
    const check = Checker.check(instance, raw.output);
    if (!check.accepted) throw Error('浏览器独立路线检查失败：' + check.errors.join('；'));
    const runtime = raw.runtime;
    if (!runtime || typeof runtime !== 'object' || Array.isArray(runtime)) throw Error('运行记录缺少 runtime 对象');
    const finite = key => typeof runtime?.[key] === 'number' && Number.isFinite(runtime[key]) && runtime[key] >= 0;
    if (!['solver_cpu_seconds', 'child_total_cpu_seconds', 'process_wall_seconds'].every(finite)) {
      throw Error('运行记录的计时字段缺失或无效');
    }
    const conformant = raw.status === 'accepted' && raw.budget_conformant === true && raw.source_unchanged === true &&
      runtime?.status === 'returned' && runtime.child_exit_code === 0 &&
      finite('solver_cpu_seconds') && runtime.solver_cpu_seconds <= 1 &&
      finite('process_wall_seconds') && runtime.process_wall_seconds <= 3;
    return {algorithm: raw.algorithm, output: raw.output, check, record: raw,
      eligible: conformant, timing_authenticated: false, provenance_authenticated: false,
      note: conformant ? '路线在本页复算通过；预算、运行时间、求解器身份和来源是外来文件声明，未认证' :
        '路线在本页复算通过，但外来运行状态或预算声明不合格；不参与推荐'};
  }
  return {SCHEMA, MAX_BYTES, binding, inspect};
}));
