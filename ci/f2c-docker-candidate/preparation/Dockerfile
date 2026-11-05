# Compiler-preparation image only. It does not compile project code or accept a wheel.
FROM --platform=linux/amd64 docker.io/library/ubuntu@sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04
ARG WORKLOAD_UID
ARG WORKLOAD_GID
ARG INPUT_SOURCE_SHA
ARG BOOTSTRAP_SHA
COPY control/ /opt/urp-control/
COPY inputs/ /inputs/
COPY control/preparation/bootstrap/ /usr/local/lib/urp-bootstrap/
COPY bootstrap/bootstrap-lock.json /opt/urp-bootstrap-seed/bootstrap-lock.json
COPY bootstrap/ca-certificates.deb bootstrap/jammy-security.InRelease bootstrap/jammy-security.main.amd64.Packages.xz /opt/urp-bootstrap-seed/
COPY bootstrap/uv /opt/urp-uv/uv
RUN /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent \
    /bin/sh /usr/local/lib/urp-bootstrap/bootstrap.sh --install-bootstrap
RUN /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent \
    /usr/bin/python3 -I -B /opt/urp-control/preparation/prepare_packages.py --root /inputs --install
RUN /usr/bin/python3 -I -B /opt/urp-control/preparation/verify_bootstrap_closure.py
RUN /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C LANG=C HOME=/root \
    /usr/bin/python3 -I -B /opt/urp-control/preparation/bootstrap_tools.py install \
    --lock /opt/urp-bootstrap-seed/bootstrap-lock.json --expected-sha256 "$BOOTSTRAP_SHA"
RUN { test "$WORKLOAD_UID" -gt 0 || { rc=$?; printf '%s\n' 'SAFE_PREPARATION_DIAGNOSTIC:{"schema":1,"stage":"input_ownership","substage":"uid_check","code":"UID_CHECK_FAILED","exception_class":"ShellExit"}' >&2 || :; exit "$rc"; }; } \
    && { test "$WORKLOAD_GID" -gt 0 || { rc=$?; printf '%s\n' 'SAFE_PREPARATION_DIAGNOSTIC:{"schema":1,"stage":"input_ownership","substage":"gid_check","code":"GID_CHECK_FAILED","exception_class":"ShellExit"}' >&2 || :; exit "$rc"; }; } \
    && { chown -R --no-dereference "$WORKLOAD_UID:$WORKLOAD_GID" /inputs || { rc=$?; printf '%s\n' 'SAFE_PREPARATION_DIAGNOSTIC:{"schema":1,"stage":"input_ownership","substage":"ownership_change","code":"OWNERSHIP_COMMAND_FAILED","exception_class":"ShellExit"}' >&2 || :; exit "$rc"; }; }
USER ${WORKLOAD_UID}:${WORKLOAD_GID}
RUN /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C.UTF-8 LANG=C.UTF-8 HOME=/inputs \
    /opt/urp-python/bin/python3.12 -I -B -S /inputs/recipe/prepare_inputs.py \
    --root /inputs --python /opt/urp-python/bin/python3.12 \
    --package-lock /inputs/package-preparation.json --expected-source-index-sha256 "$INPUT_SOURCE_SHA"
RUN /opt/urp-python/bin/python3.12 -I -B -S /inputs/freeze_input_index.py --root /inputs --phase build
WORKDIR /inputs
ENTRYPOINT ["/opt/urp-python/bin/python3.12", "-I", "-B", "-S", "/inputs/engine_gate.py"]
