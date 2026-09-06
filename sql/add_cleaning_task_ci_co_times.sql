alter table public.cleaning_tasks
add column if not exists early_checkin_time time null,
add column if not exists late_checkout_time time null;
