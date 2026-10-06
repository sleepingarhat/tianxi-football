CREATE TABLE public.football_dual_ledger (
  match_key text PRIMARY KEY,
  version text NOT NULL DEFAULT 'dual-v1',
  div text NOT NULL,
  home text NOT NULL,
  away text NOT NULL,
  kickoff_utc timestamptz NOT NULL,
  locked_at timestamptz NOT NULL DEFAULT now(),
  p_a double precision[] NOT NULL,
  lambda double precision[] NOT NULL,
  p_b_d double precision NOT NULL,
  p_final double precision[] NOT NULL,
  prediction text NOT NULL CHECK (prediction IN ('home','draw','away')),
  odds double precision[],
  odds_source text NOT NULL DEFAULT 'none',
  pick_odds double precision
);
GRANT SELECT ON public.football_dual_ledger TO anon, authenticated;
GRANT ALL ON public.football_dual_ledger TO service_role;
ALTER TABLE public.football_dual_ledger ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Dual ledger is public to read" ON public.football_dual_ledger FOR SELECT USING (true);
CREATE OR REPLACE FUNCTION public.football_dual_ledger_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN RAISE EXCEPTION 'football_dual_ledger is append-only'; END; $$;
CREATE TRIGGER football_dual_ledger_no_change BEFORE UPDATE OR DELETE ON public.football_dual_ledger
FOR EACH ROW EXECUTE FUNCTION public.football_dual_ledger_immutable();