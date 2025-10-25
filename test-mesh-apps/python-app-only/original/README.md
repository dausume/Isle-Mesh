# Python App Only - Original

**Purpose**: Original Python Flask API before mesh conversion
**Status**: Reference implementation (NOT mesh-enabled)

## Description

This is a simple Python Flask API application that serves as the baseline for testing CLI mesh conversion. This version does NOT use Isle-Mesh CLI and runs as a standard Docker Compose application.

## Files

```
original/
├── app.py                  # Flask application
├── requirements.txt        # Python dependencies
├── Dockerfile             # Container definition
├── docker-compose.yml     # Standard compose file
├── .env                   # Environment variables
└── README.md              # This file
```

## Running the Original App

### Start the application
```bash
cd test-mesh-apps/python-app-only/original
docker compose up --build
```

### Access the API
- **Base URL**: `http://localhost:5000`
- **Health Check**: `http://localhost:5000/health`
- **Data Endpoint**: `http://localhost:5000/api/data`

### Test the API
```bash
# Test root endpoint
curl http://localhost:5000

# Test health endpoint
curl http://localhost:5000/health

# Test data endpoint
curl http://localhost:5000/api/data
```

### Stop the application
```bash
docker compose down
```

## Application Details

### Endpoints
- `GET /` - Welcome message with timestamp
- `GET /health` - Health check endpoint
- `GET /api/data` - Sample data endpoint

### Environment Variables
Configured in `.env`:
- `APP_NAME` - Application name
- `APP_VERSION` - Version number
- `DEBUG` - Debug mode flag
- `LOG_LEVEL` - Logging level
- `SECRET_KEY` - Secret key for sessions
- `DATABASE_URL` - Database connection string

Also in `docker-compose.yml`:
- `ENVIRONMENT` - Runtime environment
- `PORT` - Application port
- `API_KEY` - API authentication key

### Container Details
- **Image**: Python 3.11 slim
- **Port**: 5000
- **Framework**: Flask 3.0.0

## Next Steps

See `../mesh-converted/` for the Isle-Mesh converted version of this application.

## Comparison Points

When comparing with mesh-converted version, note:
1. **Access Method**: HTTP localhost vs HTTPS subdomain
2. **Configuration**: Single compose file vs mesh configuration
3. **Network**: Default bridge vs mesh network
4. **SSL**: No SSL vs automated SSL
5. **Proxy**: Direct access vs nginx reverse proxy
6. **Environment**: Single .env vs tracked config/

## Related

- `../mesh-converted/` - Mesh-enabled version
- `/mesh-prototypes/localhost-mdns/` - Hand-crafted prototype with similar architecture
