from fastapi import FastAPI

from api.routers import domain_1

app = FastAPI(title="Atlas API")
app.include_router(domain_1.router)
