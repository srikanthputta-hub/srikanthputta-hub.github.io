-- Live board for The Money Blueprint studio (the YouTube studio's version of the S.P.E.H.R. Office).
-- ADDITIVE ONLY: one new table, three helper functions, one cron job. Nothing existing is touched.
-- Mirrors public.office_board (status, pipeline, rooms, 5s coffee break, conference for shared pipelines) but is
-- stricter: anonymous callers may READ, and UPDATE only the status columns. No insert, delete or truncate.
-- It holds only public studio status text (role, task, title); no patient or personal data.

create table public.studio_board (
  id text primary key,
  role text not null unique,
  status text not null default 'idle' check (status in ('idle','working','blocked','failed')),
  description text check (description is null or char_length(description) <= 300),
  model text check (model is null or char_length(model) <= 60),
  pipeline_name text check (pipeline_name is null or char_length(pipeline_name) <= 80),
  pipeline_stage integer,
  pipeline_total integer,
  started_at timestamptz,
  updated_at timestamptz not null default now(),
  home_room text not null,
  room text not null,
  on_break_until timestamptz,
  constraint studio_board_home_room_check check (home_room in ('newsroom','standards_desk','production_floor','publishing_suite','control_room','exec_suite','conference','breakroom','lobby')),
  constraint studio_board_room_check check (room in ('newsroom','standards_desk','production_floor','publishing_suite','control_room','exec_suite','conference','breakroom','lobby'))
);

create or replace function public.studio_board_on_change()
returns trigger
language plpgsql
as $$
begin
  NEW.updated_at := now();
  if TG_OP = 'INSERT' then
    if NEW.home_room is null then
      NEW.home_room := case NEW.role
        when 'head_of_content' then 'newsroom' when 'researcher' then 'newsroom'
        when 'data_analyst' then 'newsroom' when 'scriptwriter' then 'newsroom'
        when 'fact_checker' then 'standards_desk' when 'policy_editor' then 'standards_desk'
        when 'originality_editor' then 'standards_desk'
        when 'director' then 'production_floor' when 'chart_designer' then 'production_floor'
        when 'voice_actor' then 'production_floor' when 'video_editor' then 'production_floor'
        when 'thumbnail_designer' then 'production_floor'
        when 'publisher' then 'publishing_suite' when 'producer' then 'publishing_suite'
        when 'analyst' then 'publishing_suite'
        when 'ops' then 'control_room'
        when 'boss' then 'exec_suite'
        else 'lobby'
      end;
    end if;
    if NEW.room is null then
      NEW.room := NEW.home_room;
    end if;
    return NEW;
  end if;

  NEW.home_room := OLD.home_room; -- desks are fixed; clients cannot move a role

  -- Finished a task: working -> idle sends the role for a 5-second coffee break.
  if OLD.status = 'working' and NEW.status = 'idle' and NEW.on_break_until is null then
    NEW.on_break_until := now() + interval '5 seconds';
    NEW.room := 'breakroom';
  end if;

  -- Picked up a task: clear any break and head back to the desk (the tick moves shared pipelines to the conference room).
  if NEW.status = 'working' and OLD.status is distinct from 'working' then
    NEW.on_break_until := null;
    if NEW.room = 'breakroom' then
      NEW.room := NEW.home_room;
    end if;
  end if;
  return NEW;
end;
$$;

create trigger trg_studio_board_change
before insert or update on public.studio_board
for each row execute function public.studio_board_on_change();

create or replace function public.studio_room_tick()
returns void
language plpgsql
as $$
begin
  -- Breaks that have run their 5 seconds end.
  update public.studio_board set on_break_until = null, room = home_room
    where on_break_until is not null and on_break_until <= now();

  -- Two or more roles working the same pipeline at once meet in the conference room.
  with grp as (
    select pipeline_name from public.studio_board
    where status = 'working' and pipeline_name is not null and on_break_until is null
    group by pipeline_name having count(*) >= 2
  )
  update public.studio_board b set room = 'conference'
    from grp g
    where b.pipeline_name = g.pipeline_name and b.status = 'working' and b.on_break_until is null and b.room <> 'conference';

  -- Anyone left alone in the conference room goes back to their desk.
  update public.studio_board b set room = b.home_room
    where b.status = 'working' and b.on_break_until is null and b.room = 'conference'
      and not exists (
        select 1 from public.studio_board o
        where o.status = 'working' and o.on_break_until is null
          and o.pipeline_name is not null and o.pipeline_name = b.pipeline_name and o.id <> b.id);

  -- Everyone who is not working or on a break sits at their own desk.
  update public.studio_board set room = home_room
    where status <> 'working' and on_break_until is null and room <> home_room;
end;
$$;

-- pg_cron's floor is one minute; loop inside it so rooms update about every 10 seconds.
create or replace function public.studio_room_tick_loop()
returns void
language plpgsql
as $$
declare i int;
begin
  for i in 1..6 loop
    perform public.studio_room_tick();
    if i < 6 then perform pg_sleep(10); end if;
  end loop;
end;
$$;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'studio-room-tick-60s') then
    perform cron.unschedule('studio-room-tick-60s');
  end if;
  perform cron.schedule('studio-room-tick-60s', '* * * * *', 'select public.studio_room_tick_loop();');
end;
$$;

alter table public.studio_board enable row level security;
create policy studio_board_read on public.studio_board for select to anon, authenticated using (true);
create policy studio_board_status_update on public.studio_board for update to anon, authenticated using (true) with check (true);

revoke all on public.studio_board from anon, authenticated;
grant select on public.studio_board to anon, authenticated;
grant update (status, description, model, pipeline_name, pipeline_stage, pipeline_total, started_at) on public.studio_board to anon, authenticated;

insert into public.studio_board (id, role, model) values
  ('role-head_of_content', 'head_of_content', 'AI model'),
  ('role-researcher', 'researcher', 'AI model'),
  ('role-data_analyst', 'data_analyst', 'automated code'),
  ('role-scriptwriter', 'scriptwriter', 'AI model'),
  ('role-fact_checker', 'fact_checker', 'AI model'),
  ('role-policy_editor', 'policy_editor', 'AI model'),
  ('role-originality_editor', 'originality_editor', 'automated code'),
  ('role-director', 'director', 'automated code'),
  ('role-chart_designer', 'chart_designer', 'automated code'),
  ('role-voice_actor', 'voice_actor', 'automated code'),
  ('role-video_editor', 'video_editor', 'automated code'),
  ('role-thumbnail_designer', 'thumbnail_designer', 'automated code'),
  ('role-publisher', 'publisher', 'automated code'),
  ('role-producer', 'producer', 'automated code'),
  ('role-analyst', 'analyst', 'AI model'),
  ('role-ops', 'ops', 'automated code'),
  ('role-boss', 'boss', 'you + Claude Code');
