create table if not exists public.facility_reports (
  id uuid primary key default gen_random_uuid(),
  task_id uuid null,
  task_date date not null default current_date,
  property_name text not null default '',
  room_name text not null default '',
  description text not null,
  photo_url text not null default '',
  reported_by uuid null,
  reported_by_name text not null default '',
  created_at timestamptz not null default now()
);

create index if not exists facility_reports_task_date_idx
  on public.facility_reports (task_date desc);

create index if not exists facility_reports_created_at_idx
  on public.facility_reports (created_at desc);

alter table public.facility_reports enable row level security;

-- APIサーバーはサービスロールでアクセスするため、クライアントからの直接操作は許可しません。
