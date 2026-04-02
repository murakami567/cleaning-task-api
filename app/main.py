from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.tasks import router as tasks_router
from app.routers.beds24 import router as beds24_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cleaning-task-admin.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok"}

app.include_router(tasks_router)
app.include_router(beds24_router)
