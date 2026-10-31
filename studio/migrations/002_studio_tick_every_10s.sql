-- Fix for studio_board: the 60-second tick loop ran inside one transaction, so rows it touched stayed locked for up to a minute
-- and the pipeline's status updates (3s anon statement timeout) timed out. Run the tick every 10 seconds as its own short transaction.
do $$
begin
  if exists (select 1 from cron.job where jobname = 'studio-room-tick-60s') then
    perform cron.unschedule('studio-room-tick-60s');
  end if;
  if exists (select 1 from cron.job where jobname = 'studio-room-tick-10s') then
    perform cron.unschedule('studio-room-tick-10s');
  end if;
  perform cron.schedule('studio-room-tick-10s', '10 seconds', 'select public.studio_room_tick();');
end;
$$;

drop function if exists public.studio_room_tick_loop();

-- Clear leftover smoke-test text from the first verification.
update public.studio_board set description = null, pipeline_name = null where description = 'smoke test done' or description = 'smoke test';
