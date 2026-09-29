# -*- coding: utf-8 -*-
"""api_server.py — kiruvchi API:  uvicorn api_server:app --host 0.0.0.0 --port 8000

Kod: `app/api/`.
"""
from app import config
from app.api.app import app  # noqa: F401  (`uvicorn api_server:app`)

if __name__ == "__main__":                        # pragma: no cover
    import uvicorn
    uvicorn.run(app, host=config.api().api_host, port=config.api().api_port)
