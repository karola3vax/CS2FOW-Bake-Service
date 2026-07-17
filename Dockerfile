FROM python:3.11-slim-bookworm

ARG CS2FOW_RELEASE_URL=https://github.com/karola3vax/CS2FOW/releases/download/v0.2.3-preview/cs2fow-0.2.3-preview-linux-x86_64.zip
ARG CS2FOW_RELEASE_SHA256=0c8c5a8413eb62559670a3fca575cf229a3fa10846274b7d48db46ed5f8a6143
RUN if [ -z "$CS2FOW_RELEASE_URL" ]; then echo >&2 "CS2FOW_RELEASE_URL build argument is required"; exit 1; fi \
	&& if [ -z "$CS2FOW_RELEASE_SHA256" ]; then echo >&2 "CS2FOW_RELEASE_SHA256 build argument is required"; exit 1; fi

ENV DEBIAN_FRONTEND=noninteractive \
	PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	STEAMCMD=/opt/steamcmd/steamcmd.sh \
	CS2FOW_ROOT=/opt/cs2fow \
	RESULTS_DIR=/var/lib/cs2fow-results

RUN dpkg --add-architecture i386 \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates \
		curl \
		lib32gcc-s1 \
		lib32stdc++6 \
		libicu72 \
		libstdc++6 \
		unzip \
	&& rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/steamcmd /opt/cs2fow \
	&& curl -fsSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
		| tar -xz -C /opt/steamcmd

RUN curl -fsSL -o /tmp/cs2fow.zip "$CS2FOW_RELEASE_URL" \
	&& echo "$CS2FOW_RELEASE_SHA256  /tmp/cs2fow.zip" | sha256sum -c - \
	&& unzip -q /tmp/cs2fow.zip -d /opt/cs2fow \
	&& rm /tmp/cs2fow.zip \
	&& chmod +x /opt/cs2fow/tools/cs2fow_baker /opt/cs2fow/tools/vrf/linux64/Source2Viewer-CLI

WORKDIR /app
COPY app.py bake.py test_service.py README.md Dockerfile ./
COPY static ./static
RUN python -m unittest -q test_service.py \
	&& useradd --create-home --uid 10001 --shell /usr/sbin/nologin cs2fow \
	&& mkdir -p "$RESULTS_DIR" \
	&& chown -R cs2fow:cs2fow /opt/steamcmd "$RESULTS_DIR"

USER cs2fow
EXPOSE 7860
CMD ["python", "app.py"]
