# Frontend Dockerfile
# Serves static HTML without mTLS (plain HTTP)

FROM python:3.11-slim

WORKDIR /app

# Copy application code
COPY app.py .

# Install dependencies
RUN pip install --no-cache-dir falcon

# Expose HTTP port
EXPOSE 8080

# Run the app
CMD ["python", "app.py"]
