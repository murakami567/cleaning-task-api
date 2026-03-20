from datetime import date
from fastapi import FastAPI
from app.db import supabase

app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/tasks/today")
def get_today_tasks():
    today = date.today().isoformat()
    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("task_date", today)
        .order("property_name")
        .order("room_name")
        .execute()
    )
    return res.data
