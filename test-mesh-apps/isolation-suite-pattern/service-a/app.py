from flask import Flask, jsonify
import os

app = Flask(__name__)

SERVICE_NAME = os.getenv('SERVICE_NAME', 'service-a')
PORT = int(os.getenv('PORT', 5000))

@app.route('/')
def index():
    return jsonify({
        'service': SERVICE_NAME,
        'status': 'running',
        'type': 'isolation',
        'message': 'Service A is running independently'
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'service': SERVICE_NAME})

@app.route('/api/data')
def get_data():
    return jsonify({
        'service': SERVICE_NAME,
        'data': ['item1', 'item2', 'item3'],
        'source': 'service-a-database'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)
