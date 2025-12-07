#!/usr/bin/env python3
"""
Isle Agent Sync - mDNS Receiver and Config Generator
Receives mDNS data from the host (via isle-host-agent or other sources) and
generates nginx configuration fragments for the isle-vlan-agent.
"""

import falcon
import json
import threading
from datetime import datetime
from wsgiref.simple_server import make_server, WSGIRequestHandler

# Store received mDNS services
received_services = {}
lock = threading.Lock()


class MDNSReceiveResource:
    """API endpoint for receiving mDNS service data from host"""

    def on_post(self, req, resp):
        """Receive mDNS service data from host mDNS detector"""
        try:
            # Read the JSON body
            raw_json = req.bounded_stream.read()
            service_data = json.loads(raw_json)

            # Validate required fields
            if 'name' not in service_data:
                resp.status = falcon.HTTP_400
                resp.content_type = 'application/json'
                resp.text = json.dumps({"error": "Missing 'name' field"})
                return

            # Add metadata
            service_name = service_data['name']
            service_data['received_at'] = datetime.now().isoformat()
            service_data['last_updated'] = datetime.now().isoformat()

            # Store the service
            with lock:
                if service_name in received_services:
                    # Update existing service, preserve received_at
                    service_data['received_at'] = received_services[service_name].get('received_at', service_data['received_at'])
                    print(f"🔄 Updated service: {service_name}")
                else:
                    print(f"✅ Received new service: {service_name}")

                received_services[service_name] = service_data

            # TODO: Trigger nginx config generation here
            # generate_nginx_config(service_data)

            # Return success
            resp.status = falcon.HTTP_200
            resp.content_type = 'application/json'
            resp.text = json.dumps({
                "status": "received",
                "service_name": service_name,
                "timestamp": datetime.now().isoformat()
            })

        except json.JSONDecodeError:
            resp.status = falcon.HTTP_400
            resp.content_type = 'application/json'
            resp.text = json.dumps({"error": "Invalid JSON"})
        except Exception as e:
            resp.status = falcon.HTTP_500
            resp.content_type = 'application/json'
            resp.text = json.dumps({"error": str(e)})


class ServicesResource:
    """API endpoint to retrieve received mDNS services"""

    def on_get(self, req, resp):
        """Return all received services"""
        with lock:
            resp.status = falcon.HTTP_200
            resp.content_type = 'application/json'
            resp.text = json.dumps({
                "services": list(received_services.values()),
                "count": len(received_services),
                "timestamp": datetime.now().isoformat()
            }, indent=2)


class ServiceDetailResource:
    """API endpoint to retrieve a specific service"""

    def on_get(self, req, resp, service_name):
        """Return details for a specific service"""
        with lock:
            if service_name in received_services:
                resp.status = falcon.HTTP_200
                resp.content_type = 'application/json'
                resp.text = json.dumps(received_services[service_name], indent=2)
            else:
                resp.status = falcon.HTTP_404
                resp.content_type = 'application/json'
                resp.text = json.dumps({
                    "error": "Service not found",
                    "service_name": service_name
                })


class ServiceRemoveResource:
    """API endpoint to remove a service that has disappeared"""

    def on_delete(self, req, resp, service_name):
        """Remove a service that has disappeared"""
        with lock:
            if service_name in received_services:
                del received_services[service_name]
                print(f"❌ Removed service: {service_name}")

                # TODO: Remove corresponding nginx config
                # remove_nginx_config(service_name)

                resp.status = falcon.HTTP_200
                resp.content_type = 'application/json'
                resp.text = json.dumps({
                    "status": "removed",
                    "service_name": service_name
                })
            else:
                resp.status = falcon.HTTP_404
                resp.content_type = 'application/json'
                resp.text = json.dumps({
                    "error": "Service not found",
                    "service_name": service_name
                })


class HealthResource:
    """Health check endpoint"""

    def on_get(self, req, resp):
        resp.status = falcon.HTTP_200
        resp.content_type = 'application/json'
        resp.text = json.dumps({
            "status": "healthy",
            "service_count": len(received_services),
            "timestamp": datetime.now().isoformat()
        })


class SyncStatusResource:
    """Status endpoint showing sync state"""

    def on_get(self, req, resp):
        with lock:
            resp.status = falcon.HTTP_200
            resp.content_type = 'application/json'
            resp.text = json.dumps({
                "status": "running",
                "services_synced": len(received_services),
                "services": list(received_services.keys()),
                "timestamp": datetime.now().isoformat()
            }, indent=2)


# Falcon app
app = falcon.App()
app.add_route("/mdns", MDNSReceiveResource())
app.add_route("/services", ServicesResource())
app.add_route("/services/{service_name}", ServiceDetailResource())
app.add_route("/services/{service_name}/remove", ServiceRemoveResource())
app.add_route("/health", HealthResource())
app.add_route("/sync/status", SyncStatusResource())

# Quiet HTTP handler to suppress request logs
class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    # Bind to 0.0.0.0 so host and other containers can reach us
    HOST = '0.0.0.0'
    PORT = 8888

    print("=" * 70)
    print("🚀 Isle Agent Sync - mDNS Receiver & Config Generator")
    print("=" * 70)
    print(f"🌐 API Server: http://{HOST}:{PORT}")
    print(f"📥 mDNS receive: POST http://{HOST}:{PORT}/mdns")
    print(f"📋 Services list: GET http://{HOST}:{PORT}/services")
    print(f"🔄 Sync status: GET http://{HOST}:{PORT}/sync/status")
    print(f"💚 Health check: GET http://{HOST}:{PORT}/health")
    print("=" * 70)
    print("⚡ Waiting for mDNS data from host...")
    print("=" * 70)

    with make_server(HOST, PORT, app, handler_class=QuietHandler) as httpd:
        httpd.serve_forever()
