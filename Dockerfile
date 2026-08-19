# Multi-stage build for minimal production image
FROM python:3.12-alpine AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /install

# Production stage
FROM python:3.12-alpine

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /install /install
ENV PYTHONPATH=/install:$PYTHONPATH

# Copy application code
COPY fetch_and_render.py .
COPY templates templates/
COPY output output/

# Create non-root user for security, and make sure the directories the script
# writes to (output/, data/) are writable by it even when run standalone
# without the docker-compose bind mounts
RUN addgroup -S appuser && adduser -S appuser -G appuser \
    && mkdir -p /app/output /app/data \
    && chown -R appuser:appuser /app/output /app/data
USER appuser

# Run the script
ENTRYPOINT ["python", "fetch_and_render.py"]
