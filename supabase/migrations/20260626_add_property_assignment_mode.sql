alter table public.properties
add column if not exists assignment_mode text not null default 'solo';

alter table public.properties
drop constraint if exists properties_assignment_mode_check;

alter table public.properties
add constraint properties_assignment_mode_check
check (assignment_mode in ('solo', 'shared'));

comment on column public.properties.assignment_mode is '自動割当方式。solo=単独、shared=分業。';

create index if not exists idx_properties_assignment_mode
on public.properties (assignment_mode);
