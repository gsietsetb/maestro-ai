/**
 * SierraOS Brain — ChatGPT Export Importer
 * Usage: npx tsx scripts/import-gpt.ts /path/to/conversations.json
 *
 * Export from: ChatGPT → Settings → Data controls → Export data
 */
import * as fs from 'fs';
import { ingest, stats, sleep, DELAY_MS, MAX_CONTENT } from './config';

interface GPTMessage {
  id: string;
  author: { role: string };
  content: { parts?: string[] };
  create_time?: number;
}

interface GPTConversation {
  title: string;
  create_time: number;
  mapping: Record<string, { message?: GPTMessage }>;
}

function extractUserMessages(conv: GPTConversation): string[] {
  return Object.values(conv.mapping)
    .filter(n => n.message?.author?.role === 'user' && n.message?.content?.parts?.length)
    .map(n => n.message!.content.parts!.join('\n'))
    .filter(t => t.trim().length > 10);
}

async function main() {
  const filePath = process.argv[2];
  if (!filePath || !fs.existsSync(filePath)) {
    console.log('SierraOS Brain — ChatGPT Importer\n');
    console.log('Usage: npx tsx scripts/import-gpt.ts /path/to/conversations.json\n');
    console.log('How to export:');
    console.log('  1. Go to https://chatgpt.com → Settings → Data controls → Export data');
    console.log('  2. Download zip from email');
    console.log('  3. Unzip and find conversations.json');
    process.exit(1);
  }

  const before = await stats();
  const raw = fs.readFileSync(filePath, 'utf-8');
  const conversations: GPTConversation[] = JSON.parse(raw);
  console.log(`\n🧠 ChatGPT → SierraOS Brain`);
  console.log(`📄 Conversations: ${conversations.length}`);
  console.log(`📊 Brain before: ${before.total || 0} notes\n`);

  let ok = 0, skipped = 0;
  for (const conv of conversations) {
    const msgs = extractUserMessages(conv);
    if (msgs.length === 0) { skipped++; continue; }

    const title = conv.title || 'Untitled';
    const date = new Date((conv.create_time || 0) * 1000);
    const content = msgs.join('\n---\n');
    const trimmed = content.length > MAX_CONTENT ? content.slice(0, MAX_CONTENT) + '\n...(truncated)' : content;
    const noteContent = `GPT: "${title}" (${date.toLocaleDateString('es')})\n\n${trimmed}`;

    const success = await ingest(noteContent, 'chatgpt', ['gpt', 'conversation'], 'note', {
      title, date: date.toISOString(), messageCount: msgs.length,
    });
    if (success) {
      ok++;
      if (ok % 10 === 0) process.stdout.write(`  ✅ ${ok} imported...\n`);
    }
    await sleep(DELAY_MS);
  }

  const after = await stats();
  console.log(`\n📊 Imported: ${ok} conversations (${skipped} skipped)`);
  console.log(`📊 Brain after: ${after.total || 0} notes`);
}

main().catch(console.error);
