alter table public.rooms
  add column if not exists keybox_number text,
  add column if not exists spare_number text,
  add column if not exists room_note text;
