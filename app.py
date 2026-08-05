import modal
from modal_fn import app

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi",
        "uvicorn",
        "jinja2",
        "python-multipart"
    )
    .add_local_dir("web", remote_path="/root/web")
    .add_local_dir("static", remote_path="/root/static")
)

@app.function(image=image)
@modal.asgi_app()
def web_app():
    from web.main import app as fastapi_app
    return fastapi_app
