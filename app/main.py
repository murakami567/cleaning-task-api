from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.logger import get_logger
from app.routers.accounts import router as accounts_router
from app.routers.task_times import router as task_times_router
from app.routers.facility_trouble import router as facility_trouble_router
from app.routers.tasks import router as tasks_router
from app.routers.beds24 import router as beds24_router
from app.routers.auth import router as auth_router
from app.routers.employee_tasks_override import router as employee_tasks_router
from app.routers.employee import router as employee_router
from app.routers.admin_portal import router as admin_portal_router
from app.routers.jinjer import router as jinjer_router
from app.routers.lineworks import router as lineworks_router
from app.routers.mate_carte import router as mate_carte_router
from app.routers import payroll

logger = get_logger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cleaning-task-admin.onrender.com",
        "https://cleaning-task-gusk.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled error: {request.method} {request.url.path} - {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "サーバーエラーが発生しました。"},
    )


@app.get("/")
def root():
    return {"status": "ok"}


app.include_router(accounts_router)
app.include_router(task_times_router)
app.include_router(facility_trouble_router)
app.include_router(tasks_router)
app.include_router(beds24_router)
app.include_router(auth_router)
app.include_router(employee_tasks_router)
app.include_router(employee_router)
app.include_router(admin_portal_router)
app.include_router(jinjer_router)
app.include_router(lineworks_router)
app.include_router(mate_carte_router)
app.include_router(payroll.router)

logger.info("cleaning-task-api started")
