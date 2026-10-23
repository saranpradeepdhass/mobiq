# Mobiq on Google Cloud Run.
# Secrets are NOT baked into the image: Cloud Run mounts the Secret Manager secret as a file at
# /etc/secrets/secrets.toml and start.sh copies it to where Streamlit reads it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Runtime dependencies only (pytest is for development).
COPY requirements.txt .
RUN grep -viE '^\s*pytest' requirements.txt > requirements-runtime.txt \
    && pip install -r requirements-runtime.txt

COPY . .

# Run as an unprivileged user; it must be able to write .streamlit/secrets.toml at start-up.
RUN sed -i 's/\r$//' /app/start.sh \
    && chmod +x /app/start.sh \
    && useradd --create-home --uid 10001 mobiq \
    && mkdir -p /app/.streamlit \
    && chown -R mobiq:mobiq /app
USER mobiq

# Cloud Run supplies $PORT (8080 by default).
EXPOSE 8080
CMD ["sh", "/app/start.sh"]
