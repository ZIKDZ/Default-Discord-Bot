-- Run this once in the Supabase SQL editor for your project.

create table if not exists dm_logs (
    id           bigint generated always as identity primary key,
    guild_id     text,
    sender_id    text not null,
    target_id    text not null,
    kind         text not null check (kind in ('plain', 'embed', 'preset')),
    preset_name  text,
    content      text,
    embed_json   jsonb,
    success      boolean not null default true,
    error        text,
    created_at   timestamptz not null default now()
);

create index if not exists idx_dm_logs_guild  on dm_logs (guild_id);
create index if not exists idx_dm_logs_target on dm_logs (target_id);
create index if not exists idx_dm_logs_sender on dm_logs (sender_id);

create table if not exists dm_presets (
    id           bigint generated always as identity primary key,
    guild_id     text not null,
    name         text not null,
    title        text,
    description  text,
    color        integer,
    image_url    text,
    footer       text,
    created_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    unique (guild_id, name)
);

create index if not exists idx_dm_presets_guild on dm_presets (guild_id);

-- Keep updated_at fresh on upsert
create or replace function set_dm_presets_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_dm_presets_updated_at on dm_presets;
create trigger trg_dm_presets_updated_at
before update on dm_presets
for each row execute function set_dm_presets_updated_at();

-- The bot connects with the service_role key (server-side only, never expose it
-- to a client), so RLS can stay enabled with no public policies — service_role
-- bypasses RLS automatically.
alter table dm_logs enable row level security;
alter table dm_presets enable row level security;
