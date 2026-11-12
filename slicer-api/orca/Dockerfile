# Printbuddy's OrcaSlicer sidecar: the slicer-api fork at a pinned commit with Printbuddy's /arrange route added
# (apply.sh), on the OrcaSlicer CLI. The stages are the fork's own Dockerfile but for where the source comes from.
# Built by slicer-api/docker-compose.yml from this folder.
ARG SLICER_API_REPO=https://github.com/vmhomelab/orca-slicer-api.git
ARG SLICER_API_COMMIT=1415ea2fc02e642b7a9b6b8ce905661de86ac8f6

FROM node:22-bookworm AS build

ARG ORCA_VERSION=2.3.2
ARG TARGETARCH
ARG SLICER_API_REPO
ARG SLICER_API_COMMIT

WORKDIR /app

RUN git clone --quiet "$SLICER_API_REPO" /app \
	&& git -C /app checkout --quiet --detach "$SLICER_API_COMMIT" \
	&& rm -rf /app/.git

# Download OrcaSlicer based on architecture (as the fork's Dockerfile)
RUN if [ "$TARGETARCH" = "arm64" ]; then \
	curl -fsSL -o orca.AppImage "https://github.com/kldzj/orca-slicer-arm64/releases/download/v${ORCA_VERSION}-arm64/OrcaSlicer-${ORCA_VERSION}-arm64-linux.AppImage"; \
	else \
	curl -fsSL -o orca.AppImage "https://github.com/SoftFever/OrcaSlicer/releases/download/v${ORCA_VERSION}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage"; \
	fi \
	&& chmod +x orca.AppImage \
	&& ./orca.AppImage --appimage-extract > /dev/null \
	&& rm orca.AppImage

COPY apply.sh /printbuddy/apply.sh
COPY src /printbuddy/src
COPY tests /printbuddy/tests
RUN sh /printbuddy/apply.sh /app /printbuddy

RUN npm ci && npm run build

FROM ubuntu:24.04

RUN apt-get update \
	&& apt-get upgrade -y \
	&& apt-get install -y --no-install-recommends \
	curl ca-certificates gnupg \
	&& mkdir -p /etc/apt/keyrings \
	&& curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
	&& echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
	nodejs \
	libgl1 libopengl0 libgl1-mesa-dri libegl1 libglu1-mesa \
	libsm6 libice6 libmspack0 \
	libgtk-3-0 \
	libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
	libwebkit2gtk-4.1-0 \
	&& update-ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

COPY --from=build /app/dist/src /app/dist
COPY --from=build /app/node_modules /app/node_modules
COPY --from=build /app/squashfs-root /app/squashfs-root

ENV PORT=3000
ENV ORCASLICER_PATH=/app/squashfs-root/AppRun
ENV ORCASLICER_RESOURCES_PATH=/app/squashfs-root/resources
ENV DATA_PATH=/app/data
ENV NODE_ENV=production

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD curl -f http://localhost:3000/health || exit 1

CMD ["node", "app/dist/index.js"]
