FROM python:3.11-slim

# --- System dependencies ---
# curl-cffi needs libcurl; we also need gcc for some native builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libcurl4-openssl-dev \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# --- App directory ---
WORKDIR /app

# --- Copy project files ---
COPY pyproject.toml ./
COPY config/ ./config/
COPY src/ ./src/

# --- Install Python dependencies ---
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# --- Data directory (will be bind-mounted to persistent volume) ---
RUN mkdir -p /app/data/logs

# --- Copy environment file (overridden at runtime via -e or .env mount) ---
COPY .env.cloud .env

# --- Expose port ---
EXPOSE 8080

# --- Healthcheck ---
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# --- Run the app ---
CMD ["python", "-m", "src.main"]
