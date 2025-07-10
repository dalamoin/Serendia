from flask import Flask, request, jsonify
import os
import json

app = Flask(__name__)

# Optional: Set your token for verifying requests from Procore
EXPECTED_TOKEN = os.environ.get("PROCORE_WEBHOOK_SECRET")

@app.route("/", methods=["POST"])
def webhook():
    print("🔔 Webhook triggered!")

    # Log headers
    print("📨 Headers:")
    print(json.dumps(dict(request.headers), indent=2))

    # Authorization header check (optional security)
    if EXPECTED_TOKEN:
        auth_header = request.headers.get("Authorization")
        if auth_header != EXPECTED_TOKEN:
            print("❌ Invalid token")
            return jsonify({"error": "Unauthorized"}), 403
        print("✅ Authorization token verified")

    # Parse and log request payload
    data = request.get_json(silent=True)
    if not data:
        print("❌ No JSON payload received.")
        return jsonify({"error": "Invalid payload"}), 400

    print("📦 Payload:")
    print(json.dumps(data, indent=2))

    # Business logic example: check line item or commitment ID
    commitment_id = data.get("id")
    if commitment_id:
        print(f"📄 Processing Purchase Order ID: {commitment_id}")
        # Add logic here to interact with Procore API as needed
    else:
        print("⚠️ No commitment ID found in payload.")

    return jsonify({"status": "received"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
