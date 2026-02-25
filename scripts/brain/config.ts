export const BRAIN_API = process.env.BRAIN_API || 'https://divenamic-api.divenamic.workers.dev/api/brain';
export const BRAIN_TOKEN = process.env.BRAIN_TOKEN || 'sierraos_brain_2026';
export const DELAY_MS = 500;
export const MAX_CONTENT = 2000;

export const headers = {
  'Content-Type': 'application/json',
  'Authorization': `Bearer ${BRAIN_TOKEN}`,
};

export async function ingest(
  content: string,
  source: string,
  tags: string[] = [],
  category = 'note',
  metadata: Record<string, unknown> = {}
): Promise<boolean> {
  try {
    const res = await fetch(`${BRAIN_API}/ingest`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ content, source, category, tags, metadata }),
    });
    const data = await res.json() as any;
    return !!data.ok;
  } catch {
    return false;
  }
}

export async function stats(): Promise<any> {
  const res = await fetch(`${BRAIN_API}/stats`, { headers });
  return res.json();
}

export const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
