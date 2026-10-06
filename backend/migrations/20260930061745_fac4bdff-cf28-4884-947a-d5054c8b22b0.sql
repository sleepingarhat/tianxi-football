CREATE TABLE public.football_lineup_snapshots (
  match_key text PRIMARY KEY,
  bsd_event_id bigint,
  captured_at timestamptz NOT NULL DEFAULT now(),
  kickoff_utc timestamptz NOT NULL,
  div text NOT NULL, home text NOT NULL, away text NOT NULL,
  lineup_status text,
  lineups jsonb,
  unavailable jsonb,
  bsd_prediction jsonb,
  error text
);
GRANT SELECT ON public.football_lineup_snapshots TO anon, authenticated;
GRANT ALL ON public.football_lineup_snapshots TO service_role;
ALTER TABLE public.football_lineup_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lineup snapshots public read" ON public.football_lineup_snapshots FOR SELECT USING (true);

CREATE TABLE public.football_lineup_settle (
  match_key text PRIMARY KEY,
  settled_at timestamptz NOT NULL DEFAULT now(),
  official jsonb,
  home_hits int, away_hits int,
  home_formation_ok boolean, away_formation_ok boolean
);
GRANT SELECT ON public.football_lineup_settle TO anon, authenticated;
GRANT ALL ON public.football_lineup_settle TO service_role;
ALTER TABLE public.football_lineup_settle ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lineup settle public read" ON public.football_lineup_settle FOR SELECT USING (true);

CREATE OR REPLACE FUNCTION public.lineup_append_only() RETURNS trigger LANGUAGE plpgsql SET search_path TO 'public'
AS $$ BEGIN RAISE EXCEPTION 'append-only table'; END; $$;
CREATE TRIGGER lineup_snap_no_change BEFORE UPDATE OR DELETE ON public.football_lineup_snapshots FOR EACH ROW EXECUTE FUNCTION public.lineup_append_only();
CREATE TRIGGER lineup_settle_no_change BEFORE UPDATE OR DELETE ON public.football_lineup_settle FOR EACH ROW EXECUTE FUNCTION public.lineup_append_only();