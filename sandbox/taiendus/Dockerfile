# Completion for Python cells (docs/TAIENDUS-PLAN.md): Jedi and pyflakes read a note's code and
# never run it. Same numpy, pandas and matplotlib as the arvutus sandbox, so suggestions match.
# Compose runs it with no network, a read-only root and no capabilities.
FROM python:3.13-slim
RUN pip install --no-cache-dir jedi==0.20.0 pyflakes==4.0.2 numpy==2.3.4 pandas==2.3.3 matplotlib==3.11.2 \
    && useradd --uid 1002 --no-create-home --shell /usr/sbin/nologin taiendus \
    && mkdir -p /run/taiendus /taiendus && chown taiendus:taiendus /run/taiendus
COPY server.py engine.py /taiendus/
USER taiendus
WORKDIR /taiendus
ENV JEDI_CACHE=/tmp/jedi PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-I", "/taiendus/server.py"]
