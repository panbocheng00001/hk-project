# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1

WORKDIR /app

# opencv-headless 仍需 libglib / libgl 相关运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md requirements.txt ./
COPY solver ./solver
COPY defense ./defense

RUN pip install --no-cache-dir ".[server,http]"

EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/healthz')"

CMD ["python", "-m", "solver.serve", "--host", "0.0.0.0", "--port", "8100"]
