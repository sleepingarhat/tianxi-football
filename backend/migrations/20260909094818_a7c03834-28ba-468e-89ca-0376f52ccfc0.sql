select cron.schedule(
  'weather-sync-github-nightly',
  '50 15 * * *',
  $$
  SELECT net.http_post(
    url := 'https://project--8be5fecb-440e-4e5c-94dc-cbdc1f31edcd-dev.lovable.app/api/public/weather-sync-github',
    headers := '{"Content-Type":"application/json","x-cron-secret":"b15b6a5cea898d95e8190936650cce67ba5d14520e1e8382"}'::jsonb,
    body := '{}'::jsonb
  );
  $$
);