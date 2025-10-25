from flask import Flask, jsonify
import os
import requests
from requests.exceptions import RequestException

app = Flask(__name__)

SERVICE_NAME = os.getenv('SERVICE_NAME', 'service-b')
PORT = int(os.getenv('PORT', 6000))
DEPLOYMENT_MODE = os.getenv('DEPLOYMENT_MODE', 'isolation')
SERVICE_A_URL = os.getenv('SERVICE_A_URL', None)

@app.route('/')
def index():
    return jsonify({
        'service': SERVICE_NAME,
        'status': 'running',
        'type': DEPLOYMENT_MODE,
        'message': f'Service B is running in {DEPLOYMENT_MODE} mode',
        'can_reach_service_a': SERVICE_A_URL is not None
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'service': SERVICE_NAME})

@app.route('/api/process')
def process_data():
    result = {
        'service': SERVICE_NAME,
        'processed': True,
        'mode': DEPLOYMENT_MODE
    }

    # If in suite mode and SERVICE_A_URL is configured, try to fetch data from Service A
    if DEPLOYMENT_MODE == 'suite' and SERVICE_A_URL:
        try:
            response = requests.get(f'{SERVICE_A_URL}/api/data', timeout=5)
            if response.status_code == 200:
                service_a_data = response.json()
                result['service_a_data'] = service_a_data
                result['integration'] = 'success'
            else:
                result['integration'] = 'failed'
                result['error'] = f'Service A returned status {response.status_code}'
        except RequestException as e:
            result['integration'] = 'error'
            result['error'] = str(e)
    else:
        result['local_data'] = ['processed-item1', 'processed-item2']
        result['integration'] = 'not-configured'

    return jsonify(result)

@app.route('/api/status')
def status():
    return jsonify({
        'service': SERVICE_NAME,
        'deployment_mode': DEPLOYMENT_MODE,
        'service_a_configured': SERVICE_A_URL is not None,
        'service_a_url': SERVICE_A_URL
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)
