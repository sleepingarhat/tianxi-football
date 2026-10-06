import { useQueries } from "@tanstack/react-query";

import { STATIC_CRESTS } from "@/lib/footballCrestsStatic";

type Team = { name: string; short: string; tla: string; crest: string };
type Payload = { ok: boolean; div: string; source: string | null; teams: Team[] };

/** 隊名正規化：去掉法人／隊型後綴同標點，方便 football-data.co.uk 短名對上官方全名 */
const NOISE = new Set([
  "fc","cf","afc","sc","ac","as","ss","ssc","us","usc","cd","ud","sd","rc","rcd","club","calcio",
  "de","of","the","and","1","04","05","07","08","09","96","98","1899","1900","bv","sv","tsv","vfl",
  "vfb","fsv","spvgg","borussia","real","atletico","athletic","deportivo","sporting","town","city",
  "united","utd","county","albion","rovers","athletiek","kv","kaa","rsc","cercle","olympique","stade",
]);

/** football-data.co.uk 短名對官方全名嘅例外（正規化後對唔上嘅先入表） */
const ALIAS: Record<string, string> = {
  wolves: "Wolverhampton Wanderers",
  espanol: "Espanyol",
  "ath bilbao": "Athletic Bilbao",
  "ath madrid": "Atletico Madrid",
  "sp gijon": "Sporting Gijon",
  rennes: "Rennes Stade Rennais",
  nijmegen: "NEC Nijmegen",
  "az alkmaar": "AZ",
  "man city": "Manchester City",
  "man united": "Manchester United",
  "nott'm forest": "Nottingham Forest",
  "sheffield weds": "Sheffield Wednesday",
  "paris sg": "Paris Saint-Germain",
  "st etienne": "Saint-Etienne",
  "m'gladbach": "Borussia Monchengladbach",
  ein: "Eintracht Frankfurt",
  leverkusen: "Bayer Leverkusen",
  "st pauli": "St. Pauli",
  inter: "Internazionale",
  juventus: "Juventus",
  "verona": "Hellas Verona",
  guimaraes: "Vitoria Guimaraes",
  maritimo: "Maritimo Funchal",
  "academico viseu": "Academico de Viseu",
};

function norm(s: string) {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokens(s: string) {
  return norm(s).split(" ").filter((t) => t.length > 2 && !NOISE.has(t));
}

/** 純字詞對唔上嘅（官方全名淨係「Athletic Club」「AZ」等）直接指定三字代號 */
const ALIAS_TLA: Record<string, string> = {
  "ath bilbao": "ATH",
  "az alkmaar": "AZ",
  "sp lisbon": "SPO",
  guimaraes: "VSC",
  "sp braga": "SCB",
  "la coruna": "DEP",
  sociedad: "RSO",
  betis: "BET",
  celta: "CEL",
  vallecano: "RAY",
  // 「Real／Atletico」屬雜訊字，淨剩「Madrid」會撞隊，要直接指定
  "real madrid": "RMA",
  "ath madrid": "ATL",
  "atletico madrid": "ATL",
};

/** 上游球隊清單未更新（升降班）時嘅後備隊徽（api-sports team id） */
const FIXED_CREST: Record<string, number> = {
  leganes: 537,
  valladolid: 720,
  "las palmas": 534,
};

function bestMatch(rawName: string, teams: Team[]) {
  const key = norm(rawName);
  const wantTla = ALIAS_TLA[key];
  if (wantTla) {
    const byTla = teams.find((t) => t.tla?.toUpperCase() === wantTla);
    if (byTla) return byTla.crest;
  }
  const name = ALIAS[key] ?? rawName;
  const want = tokens(name);
  if (want.length === 0) return null;
  let best: { t: Team; score: number } | null = null;
  for (const t of teams) {
    const cand = [...tokens(t.name), ...tokens(t.short)];
    if (cand.length === 0) continue;
    let hit = 0;
    for (const w of want) {
      if (cand.some((c) => c === w || (w.length >= 5 && c.startsWith(w)) || (c.length >= 5 && w.startsWith(c)))) hit += 1;
    }
    const score = hit / want.length;
    if (score > 0 && (!best || score > best.score)) best = { t, score };
  }
  if (best && best.score >= 0.5) return best.t.crest;
  // 最後一關：整段名稱互相包含（例：Bochum ↔ VfL Bochum）
  const n = norm(name);
  if (n.length >= 4) {
    const sub = teams.find((t) => {
      const a = norm(t.name);
      const b = norm(t.short);
      return a.includes(n) || n.includes(a) || (b.length >= 4 && (b.includes(n) || n.includes(b)));
    });
    if (sub) return sub.crest;
  }
  return null;
}

/**
 * 逐個聯賽拉官方隊徽 URL，喺客戶端做名稱對照。
 * 對唔上（或聯賽未覆蓋）就回 null，由 FootballCrest 退回派生盾形識別標。
 */
export function useCrests(divs: string[]) {
  const uniq = Array.from(new Set(divs.filter(Boolean))).sort();
  const results = useQueries({
    queries: uniq.map((div) => ({
      queryKey: ["footballCrests", div],
      queryFn: async (): Promise<Payload> => {
        const res = await fetch(`/api/public/football-crests?div=${encodeURIComponent(div)}`);
        if (!res.ok) throw new Error(String(res.status));
        return (await res.json()) as Payload;
      },
      staleTime: 86_400_000,
      gcTime: 86_400_000,
      retry: 1,
    })),
  });

  const byDiv = new Map<string, Team[]>();
  uniq.forEach((div, i) => {
    const d = results[i]?.data;
    if (d?.teams?.length) byDiv.set(div, d.teams);
  });

  const cache = new Map<string, string | null>();
  return (div: string, name: string): string | null => {
    const key = `${div}|${name}`;
    if (cache.has(key)) return cache.get(key) ?? null;
    const fixed = FIXED_CREST[norm(name)];
    const crest =
      bestMatch(name, byDiv.get(div) ?? []) ??
      (fixed ? `https://media.api-sports.io/football/teams/${fixed}.png` : null) ??
      STATIC_CRESTS[`${div}|${name.toLowerCase()}`] ??
      null;
    cache.set(key, crest);
    return crest;
  };
}
