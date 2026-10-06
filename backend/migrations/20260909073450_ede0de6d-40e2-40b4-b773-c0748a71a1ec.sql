CREATE TABLE public.weather_snapshots (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  captured_at timestamptz NOT NULL DEFAULT now(),
  venue text NOT NULL,
  station_time text,
  temperature numeric,
  temperature_max numeric,
  temperature_min numeric,
  humidity numeric,
  pressure numeric,
  wind_direction text,
  wind_speed numeric,
  gust_speed numeric,
  rain_10min numeric,
  rain_today numeric,
  soil_water numeric,
  soil_loss_today numeric,
  sunshine_hour numeric,
  sectional jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX weather_snapshots_venue_time_idx ON public.weather_snapshots (venue, captured_at DESC);
GRANT SELECT ON public.weather_snapshots TO authenticated;
GRANT SELECT ON public.weather_snapshots TO anon;
GRANT ALL ON public.weather_snapshots TO service_role;
ALTER TABLE public.weather_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Weather snapshots are public to read" ON public.weather_snapshots FOR SELECT USING (true);