import { createFileRoute } from "@tanstack/react-router";
import { XMLParser } from "fast-xml-parser";

const FEED = "https://feeds.bbci.co.uk/sport/football/rss.xml";
const parser = new XMLParser({ ignoreAttributes: false, processEntities: false });

export const Route = createFileRoute("/api/public/football-news")({
  server: {
    handlers: {
      GET: async () => {
        try {
          const response = await fetch(FEED, { signal: AbortSignal.timeout(8000) });
          if (!response.ok) throw new Error("來源暫時無法連接");
          const xml = await response.text();
          if (xml.length > 500_000) throw new Error("來源資料過大");
          const feed = parser.parse(xml) as { rss?: { channel?: { item?: Array<{ title?: string; link?: string; pubDate?: string }> } } };
          const items = (feed.rss?.channel?.item ?? []).slice(0, 6).map((item) => ({
            title: String(item.title ?? "").trim().slice(0, 180),
            url: String(item.link ?? "").startsWith("https://www.bbc.") ? item.link : FEED,
            published: item.pubDate ?? "",
          })).filter((item) => item.title);
          return Response.json({ source: "BBC Sport 足球", items, fetchedAt: new Date().toISOString() }, {
            headers: { "Cache-Control": "public, max-age=300, s-maxage=900, stale-while-revalidate=3600" },
          });
        } catch {
          return Response.json({ source: "BBC Sport 足球", items: [], unavailable: true }, { status: 503 });
        }
      },
    },
  },
});