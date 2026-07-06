alter table public.properties
add column if not exists task_color text not null default '#ffffff';

alter table public.properties
add constraint properties_task_color_format
check (task_color ~ '^#[0-9A-Fa-f]{6}$')
not valid;

update public.properties
set task_color = '#ffffff'
where task_color is null or task_color = '';
