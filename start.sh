#!/bin/sh
# Container entry point for Cloud Run. Prints what it finds (visible in the Cloud Run logs) and ALWAYS starts
# Streamlit, so a missing/unreadable secret shows up as a clear message in the app instead of a failed start.
PORT="${PORT:-8080}"
echo "mobiq: starting as uid $(id -u) on port ${PORT}"

if [ -r /etc/secrets/secrets.toml ]; then
  if cp /etc/secrets/secrets.toml /app/.streamlit/secrets.toml; then
    echo "mobiq: secrets loaded ($(wc -c < /app/.streamlit/secrets.toml) bytes)"
  else
    echo "mobiq: WARNING could not copy /etc/secrets/secrets.toml into /app/.streamlit" >&2
  fi
else
  echo "mobiq: WARNING /etc/secrets/secrets.toml is missing or not readable by uid $(id -u):" >&2
  ls -la /etc/secrets 2>&1 >&2 || true
fi

exec streamlit run app.py \
  --server.port="${PORT}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false
