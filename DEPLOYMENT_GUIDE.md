# Deployment Guide – Rate-Limiter Service

This document explains how to build and run the service in production.  
All commands are Linux-centric; adapt where necessary.

-------------------------------------------------------------------------------
1. Prerequisites
-------------------------------------------------------------------------------
• Docker 20.10+ and Docker-Compose (optional but recommended)  
• Git for fetching the repository  
• Any cloud provider or on-prem host capable of exposing port 8080/tcp  

-------------------------------------------------------------------------------
2. Build a Docker image
-------------------------------------------------------------------------------
A minimal image definition:

```dockerfile
# Dockerfile
FROM python:3.11-slim

# Copy source
WORKDIR /srv
COPY . .

# Install runtime dependencies only – no pytest, etc.
RUN pip install --no-cache-dir -r requirements.txt

# Port exposed by aiohttp
EXPOSE 8080

# Configurable QPM – override at run-time
ENV MAX_QPM=100

# Entrypoint
CMD ["python", "-m", "app.rate_limiter"]
```

Build the image:
```bash
docker build -t rate-limiter:latest .
```

-------------------------------------------------------------------------------
3. Run the service
-------------------------------------------------------------------------------

Run the service using Docker-Compose:

```bash
# Run with MAX_QPM=500
docker run -d --name rate-limiter \
  -e MAX_QPM=500 \
  -p 8080:8080 \
  rate-limiter:latest
```

Browse `http://localhost:8080/api/doc` for interactive Swagger UI.

<!-- 
-------------------------------------------------------------------------------
4. Push & deploy on a registry / orchestrator
-------------------------------------------------------------------------------

1. Tag and push:
```bash
docker tag rate-limiter:latest <registry>/rate-limiter:latest
docker push <registry>/rate-limiter:latest
```

2. Use your orchestrator of choice:
```bash -->
