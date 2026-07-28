from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.logger import get_logger
from app.routers.accounts import router as accounts_router
from app.routers.task_times import router as task_times_router
from app.routers.facility_property_override import router as facility_property_router
from app.routers.facility_trouble import router as facility_trouble_router
from app.routers.tasks import router as tasks_router
from app.routers.order_management_sync import router as order_management_sync_router
from app.routers.beds24 import router as beds24_router
from app.routers.auth import router as auth_router
from app.routers.employee_tasks_override import router as employee_tasks_router
from app.routers.employee import router as employee_router
from app.routers.admin_home_active_override import router as admin_home_active_router
from app.routers.admin_portal_home_override import router as admin_portal_home_override_router
from app.routers.admin_portal_prep_list_override import router as admin_portal_prep_list_override_router
from app.routers.admin_portal import router as admin_portal_router
from app.routers.admin_portal_construction_proxy import router as admin_portal_construction_proxy_router
from app.routers.jinjer_sync_override import router as jinjer_sync_override_router
from app.routers.jinjer_attendance_override import router as jinjer_attendance_override_router
from app.routers.jinjer import router as jinjer_router
from app.routers.lineworks import router as lineworks_router
from app.routers.notifications import router as notifications_router
from app.routers.mate_carte import router as mate_carte_router
from app.routers.backups import router as backups_router
from app.routers.monthly_reports import router as monthly_reports_router
from app.routers.auto_assign_300pt import router as auto_assign_router
from app.routers.staff_schedules import router as staff_schedules_router
from app.routers.shift_attendee_priority_override import router as shift_attendee_priority_router
from app.routers.compat import router as compat_router
from app.routers.properties_management import router as properties_management_router
from app.routers.rooms_management import router as rooms_management_router
from app.routers.secure_master_reads import router as secure_master_reads_router
from app.routers import payroll

logger = get_logger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {request.method} {request.url.path} - {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "サーバーエラーが発生しました。"})


@app.get("/")
def root():
    return {"status": "ok"}


app.include_router(accounts_router)
app.include_router(task_times_router)
app.include_router(facility_property_router)
app.include_router(facility_trouble_router)
app.include_router(tasks_router)
app.include_router(order_management_sync_router)
app.include_router(beds24_router)
app.include_router(auth_router)
app.include_router(employee_tasks_router)
app.include_router(employee_router)
app.include_router(admin_home_active_router)
app.include_router(admin_portal_home_override_router)
app.include_router(admin_portal_prep_list_override_router)
app.include_router(admin_portal_router)
app.include_router(admin_portal_construction_proxy_router)
app.include_router(jinjer_sync_override_router)
app.include_router(jinjer_attendance_override_router)
app.include_router(jinjer_router)
app.include_router(lineworks_router)
app.include_router(notifications_router)
app.include_router(mate_carte_router)
app.include_router(backups_router)
app.include_router(monthly_reports_router)
app.include_router(auto_assign_router)
app.include_router(secure_master_reads_router)
app.include_router(staff_schedules_router)
app.include_router(shift_attendee_priority_router)
app.include_router(compat_router)
app.include_router(properties_management_router)
app.include_router(rooms_management_router)
app.include_router(payroll.router)

logger.info("cleaning-task-api started")
