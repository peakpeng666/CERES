# Compiler v15: remaining RUN diagnostics only

## Evidence and scope

Compiler14 run39872414645/job120149776060 ended during image preparation with
`buildkit_failure_detail=null`, `failed_stage=unknown`. It did not produce an
input image/index or execute the isolated native build. The absence of a package
marker does not prove that package verification passed. No cause is inferred.

Read-only replay of the three hash-pinned InRelease headers, all six package
indexes (118009 identities), the exact font control and reconstructed status
found no demonstrated parser regression. The current change repairs only the
observability gap after the package RUN, without dependency, selection, parser,
acceptance, provider, ABI, or source-target changes.

## Closed protocol

All seven Dockerfile RUN command headers are matched exactly, after the existing
ASCII-space normalization. New finite structural identities are closure_verify,
python_provision, input_ownership, inputs_prepare, and inputs_freeze. ARG
references remain exact literal reviewed command text, not extracted arbitrary
log values. Unmatched commands remain unknown.

New failure-only SAFE_PREPARATION_DIAGNOSTIC records have exactly schema, stage,
substage, code and exception_class. Each value is selected from source-owned
closed vocabularies; stage must match the exact same BuildKit vertex. These are
not process authentication, proof of success, or permission to change a gate.
Unknown, duplicate, conflicting, malformed, mismatched, or oversized evidence is
rejected. Unrelated vertices may interleave. Original bootstrap/package markers
and signed-index target-reference protocols remain supported unchanged.

The source-local Python emitters preserve the original exceptions, exit behavior,
interpreter flags and accepted predicates. Owned static predicate text maps to a
fixed code; known dynamic-message prefixes map to fixed codes without emitting
suffixes. No exception message, path, URL, environment, command or traceback is
projected. Unrecognized exceptions remain finite UNKNOWN_FAILURE with a bounded
class/substage. Process startup failure, cancellation, signals and malformed or
missing evidence can still yield unknown; there is no promise of complete
observability after a process cannot emit.

The ownership RUN preserves the exact test/test/chown short circuit. Its three
failure branches print only static records, then exit with the original status.
No new command process or signal trap is introduced. Failed diagnostic writes
do not replace the original exit. Tests mock chown and never change host/input
ownership. Signal tests terminate only the fixture shell itself.

Raw preparation logs remain private and ephemeral, not durably archived. Only
closed diagnostic receipts are public. No actual Docker/build/bootstrap/package
installation/native execution or publication occurred during authoring.
