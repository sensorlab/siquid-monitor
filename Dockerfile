# Playback-mock dashboard — proof of concept, NOT a production image.
# Runtime deps only (no kaleido/Chrome). The recorded data is mounted at /data
# at runtime (see docker-compose.yml) — it is not baked into the image.
FROM python:3.12-slim

WORKDIR /app

# Install the package (runtime deps resolved from pyproject.toml).
COPY pyproject.toml README.md LICENSE ./
COPY siquid_monitor ./siquid_monitor
RUN pip install --no-cache-dir .

# Serve on all interfaces inside the container; read data from the mounted volume.
ENV HOST=0.0.0.0 \
    PORT=8050 \
    SIQUID_DATA_DIR=/data

EXPOSE 8050

CMD ["siquid-monitor"]
