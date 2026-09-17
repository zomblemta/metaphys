"""python -m app.gateway：一进程本地应用。"""

import uvicorn
from metaphys.config import get_app_config

from app.gateway.main import create_app

if __name__ == "__main__":
    config = get_app_config()
    uvicorn.run(create_app(), host=config.gateway.host, port=config.gateway.port, log_level=config.log_level)
