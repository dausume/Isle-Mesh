#!/usr/bin/env python3
"""
Simple Python Flask API
Similar to localhost-mdns prototype backend
"""

from flask import Flask, jsonify
from datetime import datetime
import os

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({
        'message': 'Welcome to Python Test API',
        'status': 'running',
        'timestamp': datetime.now().isoformat(),
        'environment': os.getenv('ENVIRONMENT', 'development')
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'python-api'
    })

@app.route('/api/data')
def get_data():
    return jsonify({
        'items': [
            {'id': 1, 'name': 'Item 1'},
            {'id': 2, 'name': 'Item 2'},
            {'id': 3, 'name': 'Item 3'}
        ]
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
