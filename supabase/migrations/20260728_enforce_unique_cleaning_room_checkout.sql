-- 1回の清掃は「部屋 + チェックアウト日」につき1レコードに統一する。
-- 既存重複は、持越・作業中・完了・担当設定済みの順で運用情報が多いレコードを残す。

with ranked as (
  select
    id,
    row_number() over (
      partition by room_key, checkout_date
      order by
        case status
          when '持越' then 60
          when '清掃中' then 50
          when '清掃開始' then 45
          when '清掃完了' then 40
          when '完了' then 40
          when '未着手' then 20
          when 'CXL' then 10
          else 0
        end desc,
        case when assigned_staff_id is not null then 1 else 0 end desc,
        case when checker_id is not null then 1 else 0 end desc,
        case when assignment_locked is true then 1 else 0 end desc,
        updated_at desc nulls last,
        created_at desc nulls last,
        id
    ) as duplicate_rank
  from cleaning_tasks
  where room_key is not null
    and room_key <> ''
    and checkout_date is not null
)
delete from cleaning_tasks
where id in (
  select id
  from ranked
  where duplicate_rank > 1
);

create unique index if not exists cleaning_tasks_room_checkout_unique
  on cleaning_tasks (room_key, checkout_date)
  where room_key is not null
    and room_key <> ''
    and checkout_date is not null;
