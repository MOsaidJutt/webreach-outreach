from app_factory import create_app

# File logging (instance/app.log, surfaced by the Admin page) is configured
# inside create_app so it applies to every entry point, gunicorn included.
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
