import uvicorn

from .store import Store
from .app import ROOT

if __name__ == "__main__":
    s = Store(ROOT).settings()["server"]
    uvicorn.run("clefflow.app:app", host=s["host"], port=int(s["port"]), log_level="info")
