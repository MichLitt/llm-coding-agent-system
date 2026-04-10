import click

from coder_agent.config import cfg


@click.command(name="serve")
@click.option("--host", default=None, help="Host to bind the API server")
@click.option("--port", default=None, type=int, help="Port to bind the API server")
def serve_command(host: str | None, port: int | None) -> None:
    try:
        from coder_agent.service.app import run_server
    except ImportError as exc:
        raise click.ClickException(
            "FastAPI server dependencies are missing. Install `fastapi` and `uvicorn` first."
        ) from exc
    run_server(host or cfg.service.host, port or cfg.service.port)
