FROM nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-dev python3-pip python3.11-venv \
    ffmpeg libsm6 libxext6 libgl1-mesa-glx libglib2.0-0 \
    libxrender1 libfontconfig1 libx264-dev libx265-dev \
    libavcodec-dev libavformat-dev libswscale-dev \
    curl wget git build-essential pkg-config \
    libpq-dev netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && python -m pip install --upgrade pip setuptools wheel

WORKDIR /app

COPY requirements.txt .

RUN pip install torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 \
    --index-url https://download.pytorch.org/whl/cu121

RUN pip install \
    numpy==1.26.4 \
    scipy==1.11.4 \
    kornia==0.7.1

RUN pip install -r requirements.txt

COPY . .

RUN pip install --upgrade setuptools && pip install -e . --no-deps

RUN addgroup --system rift \
    && adduser --system --ingroup rift --home /app riftuser \
    && mkdir -p /data/storage/{uploads,renders,temp,weights,previews} \
    && chown -R riftuser:rift /app /data

USER riftuser

EXPOSE 8000