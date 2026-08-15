FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

# Set timezone and configure non-interactive installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install Python 3.12 from deadsnakes PPA and build tools
RUN apt-get update \
    && apt-get install -y software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa -y \
    && apt-get update \
    && apt-get install -y git python3.12 python3.12-venv python3.12-dev ffmpeg curl tzdata \
       build-essential gcc g++ make \
    && apt-get clean  \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV UV_LINK_MODE=copy

# copy project metadata first to leverage container image build cache
COPY ./pyproject.toml ./uv.lock /app/UltraSinger/
# Need to copy some minimal source structure for editable install
RUN mkdir -p /app/UltraSinger/src
WORKDIR /app/UltraSinger

# Install dependencies from pyproject.toml directly without venv (container is already isolated).
# numba>=0.59 in pyproject prevents uv from resolving librosa → numba 0.53 / llvmlite 0.36
# (llvmlite 0.36 only supports Python <3.10 and fails on 3.12).
RUN uv pip install --system --python 3.12 -e ".[webui]"

# Install PyTorch with CUDA support (override the CPU version from pyproject.toml)
RUN uv pip install --system --python 3.12 torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 --reinstall

# copy sources late to allow for caching of layers which contain all the dependencies
COPY . /app/UltraSinger
COPY container/webui-entrypoint.sh /usr/local/bin/webui-entrypoint.sh

# Cache dir for Hugging Face / torch models (named volume in compose)
RUN mkdir -p /app/UltraSinger/.cache \
    && chmod 755 /usr/local/bin/webui-entrypoint.sh \
    && chown -R 1000:1000 /app/UltraSinger

# Entrypoint runs as root to chown bind mounts, then drops to uid 1000.
# Web UI: FastAPI + Uvicorn. CLI: `python3.12 src/UltraSinger.py ...`
ENV HOME=/app/UltraSinger
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/UltraSinger
ENV WEBUI_NO_BROWSER=1
ENV ULTRASINGER_WEBUI_HOST=0.0.0.0
EXPOSE 8756

WORKDIR /app/UltraSinger
ENTRYPOINT ["/usr/local/bin/webui-entrypoint.sh"]
CMD ["python3.12", "-m", "webui"]
