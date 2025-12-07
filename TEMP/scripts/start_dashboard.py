from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Bootstrap sys.path pour pouvoir importer bot.*
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# On importe TON router existant
from bot.api.godmode_dashboard import router as godmode_router  # type: ignore  # noqa: E402

logger = logging.getLogger("start_dashboard")


def create_app() -> FastAPI:
    """
    App FastAPI pour le dashboard GODMODE.
    """
    app = FastAPI(
        title="BOT GODMODE – Dashboard",
        description="API runtime + dashboard GODMODE (PAPER_ONCHAIN)",
        version="0.1.0",
    )

    # Fichiers statiques (front)
    static_dir = ROOT_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    else:
        logger.warning("Répertoire static introuvable: %s", static_dir)

    # Router GODMODE
    app.include_router(godmode_router)

    # Racine -> redirection vers l'UI
    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/godmode/ui")

    return app


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Démarre le dashboard GODMODE (FastAPI)")
    parser.add_argument("--host", default="0.0.0.0", help="Host d'écoute (défaut: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8001, help="Port HTTP (défaut: 8001)")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Mode reload (dev seulement)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    app = create_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

