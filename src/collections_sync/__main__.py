"""Entry point for collections-sync service."""
import logging

import uvicorn

from .config import CollectionsSyncConfig

logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    cfg = CollectionsSyncConfig()
    uvicorn.run(
        "collections_sync.app:app",
        host="0.0.0.0",
        port=cfg.port,
        log_level="info",
    )
