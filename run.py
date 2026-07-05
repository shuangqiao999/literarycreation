"""LiteraryCreation dev launcher — starts the API server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "literarycreation.api:app",
        host="127.0.0.1",
        port=8760,
        reload=True,
        log_level="info",
    )
