from __future__ import annotations

import logging
import threading

from weatherstation.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app, station, mqtt = create_app()
stop = threading.Event()

def sample_loop():
    while not stop.wait(5):
        app.config["AGRI"].sample()

worker = threading.Thread(target=sample_loop, name="agri-simulator", daemon=True)
worker.start()
print("IoT 3.2 simulator: http://127.0.0.1:5001/part2")
try:
    app.run(host="127.0.0.1", port=5001, threaded=True, use_reloader=False)
finally:
    stop.set()
