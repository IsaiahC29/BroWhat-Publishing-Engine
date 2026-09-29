import os
import json
from flask import Flask, jsonify

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 8080))

@app.route("/")
def home():
    return jsonify({
        "service": "BroWhat Publishing Engine",
        "status": "online",
        "version": "1.0"
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)