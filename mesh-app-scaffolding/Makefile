# Makefile for mesh-proxy builder

.PHONY: help build run watch clean install-deps test shell

# Default target
help:
	@echo "Mesh Proxy Builder - Make targets"
	@echo ""
	@echo "Docker targets:"
	@echo "  make docker-build    Build the Docker image"
	@echo "  make docker-run      Generate proxy config using Docker"
	@echo "  make docker-watch    Run in watch mode (auto-rebuild on changes)"
	@echo "  make docker-shell    Open interactive shell in container"
	@echo "  make docker-clean    Remove Docker image and containers"
	@echo ""
	@echo "Local targets:"
	@echo "  make build           Generate proxy config locally"
	@echo "  make install-deps    Install local dependencies (yq, jinja2)"
	@echo "  make test            Run tests on generated config"
	@echo "  make clean           Remove generated output files"
	@echo ""
	@echo "Usage examples:"
	@echo "  make docker-run DOMAIN=custom.local"
	@echo "  make docker-run COMPOSE=../isle/docker-compose.yml"
	@echo "  make build DOMAIN=mesh-app.local"

# Docker targets
docker-build:
	@echo "Building Docker image..."
	docker build -t mesh-proxy-builder .

docker-run: docker-build
	@echo "Running mesh-proxy builder..."
	docker-compose run --rm mesh-proxy-builder $(ARGS)

docker-watch: docker-build
	@echo "Starting watch mode..."
	docker-compose up mesh-proxy-watcher

docker-shell: docker-build
	@echo "Opening interactive shell..."
	docker-compose run --rm mesh-proxy-builder bash

docker-clean:
	@echo "Cleaning up Docker resources..."
	docker-compose down
	-docker rmi mesh-proxy-builder

# Local targets
install-deps:
	@echo "Installing dependencies..."
	@command -v yq >/dev/null 2>&1 || { \
		echo "Installing yq..."; \
		mkdir -p ~/.local/bin; \
		wget -q https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O ~/.local/bin/yq; \
		chmod +x ~/.local/bin/yq; \
	}
	@python3 -c "import jinja2" 2>/dev/null || { \
		echo "Installing Jinja2..."; \
		pip3 install --user jinja2; \
	}
	@echo "✓ Dependencies installed"

build: install-deps
	@echo "Building proxy configuration locally..."
	./build-mesh-proxy.sh $(ARGS)

test:
	@echo "Testing generated configuration..."
	@if [ -f output/nginx-mesh-proxy.conf ]; then \
		echo "✓ Configuration file exists"; \
		echo "  Size: $$(du -h output/nginx-mesh-proxy.conf | cut -f1)"; \
		echo "  Lines: $$(wc -l < output/nginx-mesh-proxy.conf)"; \
		echo ""; \
		echo "Checking syntax..."; \
		grep -q "upstream backend" output/nginx-mesh-proxy.conf && echo "✓ Backend upstream found" || echo "✗ Backend upstream missing"; \
		grep -q "upstream frontend" output/nginx-mesh-proxy.conf && echo "✓ Frontend upstream found" || echo "✗ Frontend upstream missing"; \
		grep -q "server_name.*mesh-app.local" output/nginx-mesh-proxy.conf && echo "✓ Server name configured" || echo "✗ Server name missing"; \
		grep -q "ssl_certificate" output/nginx-mesh-proxy.conf && echo "✓ SSL configured" || echo "✗ SSL missing"; \
	else \
		echo "✗ Configuration file not found"; \
		exit 1; \
	fi

clean:
	@echo "Cleaning output directory..."
	rm -rf output/*.conf
	@echo "✓ Clean complete"

# Examples with parameters
example-custom-domain:
	@echo "Example: Building with custom domain..."
	docker-compose run --rm mesh-proxy-builder --domain custom.local

example-mtls:
	@echo "Example: Building with mTLS services..."
	docker-compose run --rm mesh-proxy-builder --service-mtls backend --service-mtls api

example-local:
	@echo "Example: Building locally..."
	./build-mesh-proxy.sh --domain test.local

# Quick targets for common operations
quick: docker-build docker-run test
	@echo "✓ Quick build and test complete"

dev: docker-build docker-watch
	@echo "Starting development mode..."
