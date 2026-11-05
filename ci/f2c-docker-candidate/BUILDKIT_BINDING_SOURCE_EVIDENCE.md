# Compiler16: exact rendered header compatibility and finite census

## What is known

Compiler15 run39877209353/job120165592896 failed at image preparation. Public
receipt reached closure_verify but had null failure detail and unknown failed
stage. The original7174-byte private BuildKit log was ephemeral and is not
recoverable through the public Actions log or artifacts (artifact list empty).
No real-run parser replay, actual failing predicate, producer execution or stage
completion is claimed. No speculative package/CA/ABI fix is included.

## Official source evidence, not runner-version evidence

Read-only source inspected at official moby/buildkit commit
b1ab902b2c63ea148f16b42a162af0c7381fb4ae. This is not proof of the runner's exact
BuildKit version. The source files and SHA inventory are retained separately.

- frontend/dockerfile/dockerfile2llb/convert.go1470–1478 sets RawQuotes,
  RawEscapes and SkipUnsetEnv, then calls processCmdEnv for the RUN display name.
  Its processCmdEnv1947–1952 calls shell.ProcessWord with the actual environment.
  Thus known ARG values are substituted in the display name, although shell-form
  execution itself is a separate operation. v15 literal ARG matching misses those
  names. Only the four known controlled ARG tokens are rendered by this patch;
  it does not implement a general shell evaluator.
- frontend/dockerfile/dockerfile2llb/convert_test.go,
  TestRunCustomNameKeepsEscapes calls the actual Dockerfile2LLB producer and
  checks that a known FOO expands to bar while escaped variables/quotes remain.
  This is an upstream producer semantic oracle, not a locally executed Docker or
  Go test and not captured hosted output.
- util/progress/progressui/printer.go39–77 reprints a vertex header when switching
  back to it. An identical byte-for-byte header is therefore idempotent, while a
  conflicting same-vertex header or the same stage on a different vertex remains
  rejected.
- printer.go145–150 prefixes live log lines with the vertex;187 emits top-level
  '#N ERROR: ...'. display.go785–804 prints final error-summary logs without the
  '#N' prefix. Ordinary summary repetition therefore must not be misclassified
  as a second producer emission.

Source links use the immutable commit above at
https://github.com/moby/buildkit/tree/b1ab902b2c63ea148f16b42a162af0c7381fb4ae

## Narrow implementation

The controller records the four exact existing --build-arg values before Docker
execution, without altering argv, selection or gates. This best-effort diagnostic
file is private; failure to write does not replace the original operation.
The projector reads it with strict bounded stable-file/schema checks. It renders
only known values into fixed reviewed RUN templates and compares whole commands.
No contains matching, arbitrary variable expansion, command/path output or
unvalidated value fallback is permitted. Missing/invalid bindings leave variable
RUNs unbound; immutable commands can still be recognized. Legacy standalone
parser calls without an argument binding retain their existing fixture behavior.

An independent finite census counts candidate failure markers and top-level
errors without promoting unbound observations to accepted failure details.
Live vertex and unbound final-summary candidates are kept separate. Counts are
bounded buckets and stages/reasons are fixed enums; no raw payload is published.
Conflict, invalid/missing binding, malformed/oversized input and unknown forms
remain visible without changing acceptance.

All build/selection/verification predicates and native execution are unchanged.
Only local pure fixtures/static source inspection were performed. Actual cause
remains unresolved until a separately authorized run yields sufficient evidence.
