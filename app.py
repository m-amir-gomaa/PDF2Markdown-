import modal
from modal_fn import app

image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "fastapi",
    "uvicorn",
    "jinja2",
    "python-multipart"
)

@app.function(image=image, mounts=[modal.Mount.from_local_dir("web", remote_path="/root/web"), modal.Mount.from_local_dir("static", remote_path="/root/static")])
@modal.asgi_app()
def web_app():
    from web.main import app as fastapi_app
    return fastapi_app
