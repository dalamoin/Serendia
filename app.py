from flask import Flask, request
import logging
import sys

app = Flask(__name__)

# Configure logging to stdout for Cloud Run
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

@app.route('/', methods=['POST'])
def handle_webhook():
    app.logger.info("✅ Webhook hit")

    # Log request headers
    headers = dict(request.headers)
    app.logger.info("📩 Headers: %s", headers)

    # Log raw request body
    raw_body = request.data.decode('utf-8', errors='replace')
    app.logger.info("📦 Raw Body: %s", raw_body)

    # Attempt to parse JSON body
    json_data = request.get_json(silent=True)
    if json_data:
        app.logger.info("📄 Parsed JSON: %s", json_data)
    else:
        app.logger.warning("⚠️ No JSON payload could be parsed. Raw body was: %s", raw_body)

    return '✅ Webhook received', 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
