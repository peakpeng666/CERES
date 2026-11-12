# syntax=docker/dockerfile:1
# Workbench image: frontend, gateway and the archive backend it starts.
# Simulation (ROS 2 / Gazebo) is deploy/Dockerfile.simulation.

ARG NODE_VERSION=24.22.0
FROM node:${NODE_VERSION}-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY src/frontend/ ./
RUN npm ci --engine-strict --no-audit --no-fund && npm run build

FROM debian:bookworm-slim AS runtime
ARG NODE_VERSION=24.22.0
ARG NODE_SHA256=28ea0a438de9a13bd7891fd758eee6c7819ee8af5442dfd8d45bd65e56d4228c
ARG PYTHON_VERSION=3.12.13
ARG CERES_REVISION=unknown

RUN test "$(dpkg --print-architecture)" = amd64 \
    && apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl iproute2 tini util-linux openjdk-17-jdk-headless g++ \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" -o /tmp/node.tar.gz \
    && echo "${NODE_SHA256}  /tmp/node.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.gz \
    && groupadd --gid 1000 urp \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash urp

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    CERES_REVISION=${CERES_REVISION} \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.12.13 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CERES_BIND_HOST=0.0.0.0 \
    CERES_LAB_RUNTIME=/data/lab \
    CERES_API_DB=/data/archive/research.sqlite3 \
    CERES_FOXGLOVE_BIND=0.0.0.0 \
    CERES_BROWSER_HOST=127.0.0.1 \
    LIBGL_ALWAYS_SOFTWARE=1

WORKDIR /opt/urp
COPY pyproject.toml uv.lock ./
COPY src/lab/pyproject.toml src/lab/uv.lock src/lab/
RUN uv python install ${PYTHON_VERSION} \
    && uv sync --locked --extra solvers --no-dev --python ${PYTHON_VERSION} \
    && uv sync --locked --inexact --no-dev --python ${PYTHON_VERSION} --project src/lab

# Cohort sources, adapters and licenses only; no formal runs or research archives.
COPY cohort_b/ails/adapted ./cohort_b/ails/adapted
COPY cohort_b/ails/build.sh ./cohort_b/ails/build.sh
COPY cohort_b/sisr/adapted ./cohort_b/sisr/adapted
COPY cohort_b/sisr/build.sh ./cohort_b/sisr/build.sh
COPY cohort_b/sisr/cvrp/config.json ./cohort_b/sisr/cvrp/config.json
COPY cohort_b/run_vendor.py ./cohort_b/run_vendor.py
COPY deploy/build-cohort.sh deploy/check-cohort.py deploy/image-healthcheck.py ./deploy/
COPY deploy/check-runtime.py deploy/check-fields2cover.py ./deploy/
RUN bash deploy/build-cohort.sh \
    && printf '%s\n' "${CERES_REVISION}" > /opt/urp/REVISION

COPY src/backend ./src/backend
COPY src/research ./src/research
COPY src/lab ./src/lab
COPY src/frontend/core ./src/frontend/core
COPY src/frontend/package.json ./src/frontend/package.json
COPY src/start.sh ./src/start.sh
COPY tools/runtime_db.py ./tools/runtime_db.py
COPY setup/profile.sh setup/cyclonedds.xml setup/
COPY data/backend/research.sqlite3 ./data/backend/research.sqlite3
COPY data/packages ./data/packages
COPY data/research ./data/research
COPY --from=frontend /src/frontend/dist ./src/frontend/dist
COPY deploy/entrypoint.sh deploy/THIRD_PARTY.md ./deploy/

RUN chmod 755 deploy/entrypoint.sh src/start.sh setup/profile.sh \
    && mkdir -p /data/lab /data/archive \
    && chown -R urp:urp /opt/urp /opt/uv-python /data

LABEL org.opencontainers.image.revision="${CERES_REVISION}" \
      org.opencontainers.image.source="https://github.com/peakpeng666/URP" \
      org.opencontainers.image.title="urp-bench" \
      org.opencontainers.image.description="CERES workbench without ROS"
EXPOSE 4100
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=8 \
    CMD ["/opt/urp/src/lab/.venv/bin/python", "/opt/urp/deploy/image-healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/urp/deploy/entrypoint.sh"]
CMD ["/opt/urp/src/lab/.venv/bin/python", "-m", "ceres_lab", "serve"]

