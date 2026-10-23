from weatherstation.app import create_app

app, station, transport = create_app()
print("IoTpr3 security dashboard: http://127.0.0.1:5002/security")
app.run(host="127.0.0.1", port=5002, threaded=True, use_reloader=False)
