import StockDetailContent from "./StockDetailContent";
import fs from "fs";
import path from "path";

export async function generateStaticParams() {
  // Dynamically read tickers from history_data.json at build time
  const dataPath = path.join(process.cwd(), "..", "docs", "history_data.json");
  try {
    const raw = fs.readFileSync(dataPath, "utf-8");
    const data = JSON.parse(raw);
    const tickers = Object.keys(data.tickers || {});
    return tickers.map((ticker) => ({ ticker }));
  } catch {
    // Fallback: read from tickers.yml
    const yamlPath = path.join(process.cwd(), "..", "tickers.yml");
    try {
      const yamlRaw = fs.readFileSync(yamlPath, "utf-8");
      const codes = [...yamlRaw.matchAll(/code:\s*"([^"]+)"/g)].map((m) => m[1]);
      return codes.map((ticker) => ({ ticker }));
    } catch {
      // Final fallback: no hard-coded ticker limit.
      return [];
    }
  }
}

interface PageProps {
  params: Promise<{ ticker: string }>;
}

export default async function Page({ params }: PageProps) {
  const { ticker } = await params;
  return <StockDetailContent ticker={ticker} />;
}
