# Getting Started with Isle-Mesh

Isle-Mesh is a simplified CLI for managing Docker Compose applications with automated SSL, nginx proxy, and mesh networking.

## 🚀 Quick Start

### Option 1: Convert Existing Docker Compose App

```bash
# Convert your existing docker-compose.yml to a mesh-app
isle init -f docker-compose.yml -d myapp.local

# Start your mesh-enabled services
isle up --build

# View logs
isle logs

# Stop services
isle down
```

### Option 2: Create New Mesh-App from Scratch

```bash
# Create a new directory for your project
mkdir my-mesh-app && cd my-mesh-app

# Initialize the mesh-app
isle init -d myapp.local

# Edit docker-compose.mesh-app.yml to add your services
nano docker-compose.mesh-app.yml

# Start services
isle up
```

## 📋 Core Commands

### Project Management

```bash
# Initialize a new mesh-app
isle init [options]
  -f, --file FILE       # Convert from existing docker-compose.yml
  -o, --output DIR      # Output directory (default: current)
  -d, --domain DOMAIN   # Base domain (default: mesh-app.local)
  -n, --name NAME       # Project name (auto-detected)

# Set current working project
isle config set-project <path>

# Get current project
isle config get-project
```

### Service Management (Docker Compose Style)

```bash
# Start services
isle up                # Start in background
isle up --build        # Build and start
isle up --no-detach    # Start in foreground

# Stop services
isle down              # Stop services
isle down -v           # Stop and remove volumes

# View logs
isle logs              # All services
isle logs backend      # Specific service
isle logs --no-follow  # Don't follow logs

# Check status
isle ps                # List running services
```

### Cleanup

```bash
# Remove all mesh resources
isle prune             # Interactive
isle prune -f          # Force (no prompt)
```

## 📁 Generated Project Structure

When you run `isle init`, it creates:

```
my-mesh-app/
├── docker-compose.mesh-app.yml  # Mesh-integrated compose file
├── setup.yml                     # Environment configuration
├── isle-mesh.yml                 # Mesh network configuration
├── config/
│   ├── env-manifest.json        # Environment variable tracking
│   ├── ssl.env.conf             # SSL certificate config
│   └── *.env                    # Copied environment files
├── ssl/
│   ├── certs/                   # SSL certificates
│   └── keys/                    # Private keys
├── proxy/
│   └── nginx-mesh-proxy.conf    # Auto-generated nginx config
└── ISLE-MESH-README.md          # Detailed setup instructions
```

## 🔧 Advanced Features

### Environment File Management

Isle-Mesh automatically:
- Extracts `env_file` directives from your docker-compose.yml
- Copies environment files to the `config/` directory
- Updates paths in the generated compose file
- Tracks original locations in `config/env-manifest.json`

### SSL & Proxy

Every mesh-app includes:
- Automated SSL certificate generation
- Nginx reverse proxy with mTLS support
- Subdomain routing for each service
- Easy HTTPS access to all services

### Service Labels

Add these labels to services in your docker-compose.yml:

```yaml
services:
  myservice:
    labels:
      mesh.subdomain: "api"      # Subdomain (api.myapp.local)
      mesh.mtls: "true"          # Enable mutual TLS
```

## 📚 Example Workflows

### Example 1: Three-Tier Web App

```bash
# Start with your docker-compose.yml
isle init -f docker-compose.yml -d webapp.local

# Your services are now available at:
# - https://frontend.webapp.local
# - https://api.webapp.local
# - https://db.webapp.local

# Start everything
isle up --build

# View frontend logs
isle logs frontend

# Stop when done
isle down
```

### Example 2: Microservices Development

```bash
# Initialize
isle init -d microservices.local

# Add services to docker-compose.mesh-app.yml
# Then start
isle up

# View all logs
isle logs

# Check service status
isle ps

# Clean up everything
isle prune -f
```

## 🛠️ Advanced Commands

### Mesh Proxy Management

```bash
isle proxy up          # Start mesh proxy
isle proxy down        # Stop mesh proxy
isle proxy logs        # View proxy logs
isle proxy build       # Rebuild proxy config
```

### SSL Certificate Management

```bash
isle ssl list          # List certificates
isle ssl info myapp    # Show certificate info
isle ssl verify myapp  # Verify certificate
isle ssl clean         # Remove all certificates
```

### Embed-Jinja (Template Automation)

```bash
isle jinja up          # Start jinja workflow
isle jinja logs        # View jinja logs
isle jinja status      # Check status
isle jinja clean       # Clean generated files
```

### mDNS System

```bash
isle mdns install      # Install mDNS on host
isle mdns status       # Check mDNS status
isle mdns uninstall    # Remove mDNS
```

## 💡 Tips & Best Practices

1. **Always set a project** before using `isle up/down/logs/ps`:
   ```bash
   isle config set-project /path/to/project
   ```

2. **Use meaningful domains** for local development:
   ```bash
   isle init -d myproject.local
   ```

3. **Clean up regularly** to save disk space:
   ```bash
   isle prune -f
   ```

4. **Check generated README** after initialization:
   ```bash
   cat ISLE-MESH-README.md
   ```

5. **View help anytime**:
   ```bash
   isle help
   ```

## 🐛 Troubleshooting

### Services won't start
```bash
# Check if services are defined
cat docker-compose.mesh-app.yml

# Check logs
isle logs

# Try rebuilding
isle down && isle up --build
```

### "No current project set" error
```bash
# Set the current project
isle config set-project .
```

### SSL certificate errors
```bash
# Regenerate certificates
isle ssl clean
# Then re-init your project
```

## 📖 More Information

- Full command reference: `isle help`
- Project-specific docs: See generated `ISLE-MESH-README.md`
- Advanced configuration: Edit `setup.yml` and `isle-mesh.yml`

## 🎯 Next Steps

1. Try converting your own docker-compose.yml
2. Explore the advanced commands
3. Customize the generated configurations
4. Build your mesh-enabled applications!

Happy meshing! 🚀
