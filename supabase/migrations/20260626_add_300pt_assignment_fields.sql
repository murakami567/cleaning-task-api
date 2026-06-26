alter table public.properties
add column if not exists assignment_mode text not null default 'solo';

alter table public.properties
drop constraint if exists properties_assignment_mode_check;

alter table public.properties
add constraint properties_assignment_mode_check
check (assignment_mode in ('solo', 'shared', 'both'));

alter table public.rooms
add column if not exists cleaning_score integer not null default 60;

alter table public.staff_members
add column if not exists daily_capacity_point integer not null default 300,
add column if not exists solo_enabled boolean not null default true,
add column if not exists shared_enabled boolean not null default true;

comment on column public.properties.assignment_mode is '自動割当方式。solo=単独、shared=分業、both=両方可。';
comment on column public.rooms.cleaning_score is '自動割当で使用する部屋ごとの作業点数。標準60pt。';
comment on column public.staff_members.daily_capacity_point is '自動割当で使用する1日あたり処理上限点数。標準300pt。';
comment on column public.staff_members.solo_enabled is '単独清掃に割当可能か。';
comment on column public.staff_members.shared_enabled is '分業清掃に割当可能か。';

create index if not exists idx_rooms_cleaning_score on public.rooms (cleaning_score);
create index if not exists idx_staff_members_capacity on public.staff_members (daily_capacity_point);
create index if not exists idx_properties_assignment_mode on public.properties (assignment_mode);
