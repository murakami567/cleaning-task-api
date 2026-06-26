alter table public.properties
add column if not exists max_assignable_count integer;

comment on column public.properties.max_assignable_count is '自動割当で同一スタッフへ割り当て可能な、この物件の1日あたり最大清掃数。NULLの場合は制限なし。';

create index if not exists idx_properties_max_assignable_count
on public.properties (max_assignable_count);
