# The arvutus sandbox (docs/ARVUTUS-PLAN.md): Python cells from notes run here, never in web or
# worker. Compose runs it with no network, a read-only root, no capabilities and tight limits.
FROM python:3.13-slim
RUN pip install --no-cache-dir numpy==2.3.4 pandas==2.3.3 \
    && useradd --uid 1001 --no-create-home --shell /usr/sbin/nologin arvutus \
    && mkdir -p /run/arvutus /sandbox && chown arvutus:arvutus /run/arvutus
COPY server.py kernel.py /sandbox/
USER arvutus
CMD ["python", "-I", "/sandbox/server.py"]
