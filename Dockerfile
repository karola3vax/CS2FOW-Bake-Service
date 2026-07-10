FROM python:3.11-slim-bookworm

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

ARG CS2FOW_VERSION=0.2.0-preview
ARG CS2FOW_ARCHIVE_URL
ARG CS2FOW_SHA256=A812B1A970F50A986B5B9A549407C8793B1AB6B6B1B9E065E84C971FB2D0A127
RUN test -n "$CS2FOW_SHA256" \
	&& url="${CS2FOW_ARCHIVE_URL:-https://github.com/karola3vax/CS2FOW/releases/download/v${CS2FOW_VERSION}/cs2fow-${CS2FOW_VERSION}-linux-x86_64.zip}" \
	&& curl -fsSL -o /tmp/cs2fow.zip "$url" \
	&& echo "$CS2FOW_SHA256  /tmp/cs2fow.zip" | sha256sum -c - \
	&& unzip -q /tmp/cs2fow.zip -d /opt/cs2fow \
	&& rm /tmp/cs2fow.zip \
	&& chmod +x /opt/cs2fow/tools/cs2fow_baker /opt/cs2fow/tools/vrf/linux64/Source2Viewer-CLI

WORKDIR /app
COPY app.py bake.py test_service.py README.md ./
RUN python -m unittest -q test_service.py \
	&& useradd --create-home --uid 10001 --shell /usr/sbin/nologin cs2fow \
	&& mkdir -p "$RESULTS_DIR" \
	&& chown -R cs2fow:cs2fow /opt/steamcmd "$RESULTS_DIR"

USER cs2fow
EXPOSE 7860
CMD ["python", "app.py"]
