# Build only after tools/build_platform_image.py and the audited docker import.
# The published image is pinned by ID and file hashes in setup/platform-image.json.
FROM ceres-platform-userspace:20261018-ddb5113e
# Resolve uv's 3.12 alias to the archived 3.12.13 interpreter. The corrected raw
# snapshot also carries these links; this repairs the initial imported layer.
USER 0:0
RUN for env in .venv src/lab/.venv; do \
      test -e "$env/bin/python" || ln -s /home/peakpeng/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12 "$env/bin/python"; \
    done
COPY container_entry.py /usr/local/libexec/ceres-container-entry.py
COPY --chown=1000:1000 field_backend.py /home/peakpeng/Desktop/CERES/src/lab/e2e/field_backend.py
ADD system-links.tar /
RUN /sbin/ldconfig
USER 0:0
ENTRYPOINT ["/usr/bin/python3", "/usr/local/libexec/ceres-container-entry.py"]
CMD ["bash", "setup/run.sh", "lab"]
