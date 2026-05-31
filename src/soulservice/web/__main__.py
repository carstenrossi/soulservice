import uvicorn

from soulservice.core.config import settings
from soulservice.web.app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host=settings.web_host, port=settings.web_port)
