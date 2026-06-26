create table if not exists public.staff_property_priorities (
  id uuid primary key default gen_random_uuid(),
  staff_id uuid not null references public.staff_members(id) on delete cascade,
  property_id uuid not null references public.properties(id) on delete cascade,
  priority_order integer not null default 999,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (staff_id, property_id)
);

comment on table public.staff_property_priorities is '自動割当で使用するスタッフごとの物件優先順位。priority_order が小さいほど優先。';
comment on column public.staff_property_priorities.priority_order is '1が最優先。未設定の場合はチェック解除済み、対応可能物件の順でフォールバックする。';

create index if not exists idx_staff_property_priorities_staff_id
on public.staff_property_priorities (staff_id);

create index if not exists idx_staff_property_priorities_property_id
on public.staff_property_priorities (property_id);

create index if not exists idx_staff_property_priorities_priority
on public.staff_property_priorities (property_id, priority_order);
