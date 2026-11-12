"""Experience-driven, evolvable path decisions (docs/持续进化决策与调度.md).

The package sits beside the existing task → recommend → solve → check → simulate → record chain and never
changes solver internals: it reads results through ``run_algorithm``, run records and simulation result files.

- ``features``  agricultural structural features (E1), versioned and invariance-tested
- ``instances`` field manifest (development vs sealed test fields) and instance builders
- ``sweep``     offline portfolio runner with process isolation, per-job timeouts and CPU pinning
- ``audit``     complementarity audit: VBS/SBS headroom, feasibility complementarity, Shapley contributions
- ``policy``    try-order selection policies, grouped cross-validation, prequential evaluation, risk gate
- ``experience`` experience base built from run records, sweeps and field-simulation results
"""
