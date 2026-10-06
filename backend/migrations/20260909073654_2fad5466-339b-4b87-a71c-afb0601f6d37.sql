-- lovable-cron-fallback-reviewed: 288 runs/day; HKJC weather source exposes only live values with no history, so pre-race wind/rain/soil readings must be sampled every 5 minutes to build a backtestable archive
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

SELECT cron.schedule(
  'weather-archive-5min',
  '*/5 * * * *',
  $$
  SELECT net.http_post(
    url := 'https://project--8be5fecb-440e-4e5c-94dc-cbdc1f31edcd-dev.lovable.app/api/public/weather-archive',
    headers := '{"Content-Type":"application/json","x-cron-secret":"b15b6a5cea898d95e8190936650cce67ba5d14520e1e8382"}'::jsonb,
    body := '{}'::jsonb
  );
  $$
);