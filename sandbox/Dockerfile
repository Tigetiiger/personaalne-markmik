# The arvutus sandbox (docs/ARVUTUS-PLAN.md): Python cells from notes run here, never in web or
# worker. Compose runs it with no network, a read-only root, no capabilities and tight limits.
FROM python:3.13-slim
RUN pip install --no-cache-dir numpy==2.3.4 pandas==2.3.3 matplotlib==3.11.2 openpyxl==3.1.5 pyarrow==25.0.1 \
    && useradd --uid 1001 --no-create-home --shell /usr/sbin/nologin arvutus \
    && mkdir -p /run/arvutus /sandbox && chown arvutus:arvutus /run/arvutus \
    # matplotlib's font cache, built once: each run copies it (its /tmp is emptied after every run)
    && MPLCONFIGDIR=/opt/mplcache MPLBACKEND=Agg python -c "import matplotlib.pyplot as plt; plt.figure()" \
    && chmod -R a+rX /opt/mplcache
COPY server.py kernel.py /sandbox/
USER arvutus
CMD ["python", "-I", "/sandbox/server.py"]
