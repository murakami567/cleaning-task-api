alter table public.rooms
  add column if not exists early_checkin_fee integer not null default 0,
  add column if not exists late_checkout_fee integer not null default 0;

alter table public.rooms
  add constraint rooms_early_checkin_fee_nonnegative check (early_checkin_fee >= 0),
  add constraint rooms_late_checkout_fee_nonnegative check (late_checkout_fee >= 0);
