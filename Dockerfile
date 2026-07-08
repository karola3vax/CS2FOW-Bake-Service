FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
	PIP_NO_CACHE_DIR=1 \
	STEAMCMD=/opt/steamcmd/steamcmd.sh \
	CS2FOW_ROOT=/opt/cs2fow

RUN dpkg --add-architecture i386 \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates \
		curl \
		lib32gcc-s1 \
		lib32stdc++6 \
		libstdc++6 \
		unzip \
		zip \
	&& rm -rf /var/lib/apt/lists/*

RUN pip install gradio==4.44.1

RUN mkdir -p /opt/steamcmd /opt/cs2fow \
	&& curl -fsSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz -C /opt/steamcmd

ARG CS2FOW_VERSION=0.1.1-preview
RUN curl -fsSL -o /tmp/cs2fow.zip "https://github.com/karola3vax/CS2FOW/releases/download/v${CS2FOW_VERSION}/cs2fow-${CS2FOW_VERSION}-linux-x86_64.zip" \
	&& unzip -q /tmp/cs2fow.zip -d /opt/cs2fow \
	&& rm /tmp/cs2fow.zip \
	&& chmod +x /opt/cs2fow/tools/cs2fow_baker /opt/cs2fow/tools/vrf/linux64/Source2Viewer-CLI

WORKDIR /app
COPY app.py README.md ./
RUN python app.py --self-test

EXPOSE 7860
CMD ["python", "app.py"]
