alter table public.cleaning_tasks
add column if not exists assignment_locked boolean not null default false;

comment on column public.cleaning_tasks.assignment_locked is '自動割当で変更しない個別ロック。true の場合、自動割当対象から除外する。';

create index if not exists idx_cleaning_tasks_assignment_locked
on public.cleaning_tasks (assignment_locked);

create index if not exists idx_cleaning_tasks_task_date_assignment_locked
on public.cleaning_tasks (task_date, assignment_locked);
