# -----------------------------
# スタッフ作成
# -----------------------------
@app.post("/staffs/create")
def create_staff(
    staff_code: str = Body(...),
    staff_name: str = Body(...),
    role: str = Body("staff"),
    is_active: bool = Body(True),
    sort_order: int = Body(999),
    note: str = Body(""),
):
    payload = {
        "staff_code": staff_code,
        "staff_name": staff_name,
        "role": role,
        "is_active": is_active,
        "sort_order": sort_order,
        "note": note,
    }

    res = supabase.table("staff_members").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="staff creation failed")

    return res.data[0]


# -----------------------------
# スタッフ更新
# -----------------------------
@app.post("/staffs/update")
def update_staff(
    staff_id: str = Body(...),
    staff_code: str = Body(...),
    staff_name: str = Body(...),
    role: str = Body("staff"),
    is_active: bool = Body(True),
    sort_order: int = Body(999),
    note: str = Body(""),
):
    payload = {
        "staff_code": staff_code,
        "staff_name": staff_name,
        "role": role,
        "is_active": is_active,
        "sort_order": sort_order,
        "note": note,
    }

    res = (
        supabase.table("staff_members")
        .update(payload)
        .eq("id", staff_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="staff update failed")

    return res.data[0]
