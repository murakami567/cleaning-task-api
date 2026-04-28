from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.tasks import router as tasks_router
from app.routers.beds24 import router as beds24_router

from app.routers.auth import router as auth_router
from app.routers.employee import router as employee_router

from app.routers.admin_portal import router as admin_portal_router

from app.routers import payroll

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cleaning-task-admin.onrender.com",
        "https://cleaning-task-gusk.onrender.com",  # ←追加
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
app.include_router(auth_router)
app.include_router(employee_router)
app.include_router(admin_portal_router)
app.include_router(payroll.router)
