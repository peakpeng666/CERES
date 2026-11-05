# Real simulation acceptance harness

Execution source is fixed to 3acf840d1a0c16ab1cfcca15744e4ea6609ddb35. The harness package is checked out at a separate fixed commit supplied by the wrapper; both identities appear independently in the allowlisted summary. A fresh clean source checkout is mandatory, not the dirty local audit checkout.

Interface:

    python acceptance.py --source SOURCE --private-dir NEW_PRIVATE_DIRECTORY --summary SUMMARY --harness-sha FORTY_HEX_COMMIT --stage maze
    python public_summary.py --input SUMMARY --runner-status RUNNER_STATUS

The wrapper owns installation and Xvfb. The harness owns a fresh runtime and gateway, and invokes the unmodified source full_loop.py via its real HTTP API: maze-inspection, recommended algorithm only, ground_truth, follow_path, no GUI or RViz. Full-loop overall wall budget is 1800 seconds. The physical controller's separate budget is reported only if present in its real record, otherwise null. No historical timing is claimed for this new maze run.

PASS requires full-loop PASS, terminal simulation succeeded, at least two finite ground-truth samples with real movement, all positive-count tasks reached, planned and measured actual collision-free map checks, verified solver source and worker interpreter identities, verified source-fixed bridge plus TF/GUI compatibility, complete assets, normal recorded session cleanup, gateway exit 0, full-loop exit 0 and no remaining owned processes. Timeout, forced harness cleanup, missing evidence, wrapper error or wrapper process residue fail closed. The harness never disables original source/ABI/map gates. A random inherited ownership marker and PID start-time check limit cleanup to its own process tree; the outer wrapper independently adopts/checks descendants.

The only public path is public_summary.py, which rebuilds fixed keys and enums, finite bounded numbers, booleans and hex digests. Arbitrary strings, original errors, URLs, absolute paths, API responses and unknown keys are discarded. Absent measurements remain null with not_measured availability. TF/clock live diagnostics are not presently sampled and are not claimed as zero. Raw API outputs, runtime, logs and reports stay beneath the runner temporary directory, never uploaded or cached. The wrapper's final cleanup result can overturn an earlier PASS.

Warehouse uses the independently rechecked frozen five-task witness, exact recovered spawn and unchanged resource/map/route checks. No replacement solver is run. The planner-selected649.6-second budget is preserved and explicitly uses simulation time. Historical missing timing is not invented.

Local tests exercise projection, every success gate, missing measurements, corrupt/nonfinite inputs, source mismatch, wrapper cleanup override and a real tagged child cleanup. They are harness tests, not ROS/Gazebo acceptance. No simulator or installer was run during development.

## Startup timing policy

The pinned application uses an Undock-only 35-second simulation budget, a 700-second monotonic wall watchdog, and 30-second clock-progress and required-stream guards. Readiness polling is capped at 1200 wall seconds and remains bounded by the existing overall polling deadline. Cancellation and cleanup have their own bounded wall waits. Route geometry, collision checks and physical success criteria are unchanged. The opt-in execution-clock protocol below changes execution time basis explicitly, not the planner-selected budget. The observer records an observed interval, not exact action acceptance/result times.


## Explicit execution-clock protocol

Only follow_path is submitted. CLI opt-in for maze is --execution-time-basis simulation --execution-wall-watchdog 900; --execution-budget is not supplied. The reviewed application's planner budget remains selected-clock seconds, not measured runtime: approximately103 for maze and649.6 for the fixed warehouse route. Maze layers are watchdog900, full_loop1800, runner-stage3600 seconds and job125 minutes. Warehouse layers are watchdog5400, lifecycle7200, runner-stage9000 seconds and job220 minutes. Installation remains separately capped at3600 seconds. Preload, readiness and cleanup retain their existing checks and finite deadlines. These scopes are not interchangeable.

execution_protocol.py verifies the original requested policy and both API submission/terminal metadata against the immutable planner budget, simulation basis, shared approach/all-legs scope and unchanged stream guards. Missing/mismatched policy blocks PASS in addition to, never instead of, every existing physical predicate. Public timing projection excludes absolute clocks, coordinates and arbitrary text. No real-time feasibility claim follows from this simulation-time experiment. Source pins now target the reviewed execution-policy source commit; no native run is claimed by this local validation.

## Simulation freshness protocol v2

The next cohort requires `simulation_execution_v2` / `execution_budget_v2`: source freshness is 3 simulation seconds, receipt and source-progress liveness are each 30 wall seconds, future stamp tolerance is 3 simulation seconds, and only ground truth permits a zero-stamp clock-at-receive fallback. Requested and terminal frozen policies must both exactly match. The source pin is the reviewed v2 commit `3acf840d1a0c16ab1cfcca15744e4ea6609ddb35`. Route geometry, tasks, selected-clock budgets, wall watchdogs, physical predicates and cleanup gates are unchanged. Earlier v1 observations retain their v1 label and are not accepted as the new cohort.

Execution diagnostics expose separate atomic trigger and latest observations, plus at most 8 recent observations, all in a fixed schema. Per-stream receipt/source/freshness/progress ages are relative, finite and range-limited; negative ages are retained for fault diagnosis. Zero-stamped ground truth keeps source age unavailable rather than inventing measurement age. Absolute clocks, source stamps, paths, identifiers and raw messages are never projected. Missing or malformed evidence remains null. Diagnostic observations do not create a PASS or establish real-time feasibility.
Legacy or unavailable observations deliberately retain `diagnostics: null`; they are not claimed to validate the new schema's `execution_observation_v1` constant. A fallback ground-truth row always projects `source_age_sim_s: null`, including inconsistent private input that supplies a numeric age.
