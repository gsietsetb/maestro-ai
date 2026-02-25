/**
 * SierraOS Brain — Obsidian Vault Importer
 * Usage: npx tsx scripts/import-obsidian.ts /path/to/vault
 */
import * as fs from 'fs';
import * as path from 'path';
import { ingest, stats, sleep, DELAY_MS, MAX_CONTENT } from './config';

function walkDir(dir: string): string[] {
  const files: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.name.startsWith('.')) continue;
    if (entry.isDirectory()) files.push(...walkDir(full));
    else if (entry.name.endsWith('.md')) files.push(full);
  }
  return files;
}

async function main() {
  const vaultPath = process.argv[2];
  if (!vaultPath || !fs.existsSync(vaultPath)) {
    console.log('SierraOS Brain — Obsidian Importer\n');
    console.log('Usage: npx tsx scripts/import-obsidian.ts /path/to/vault\n');
    console.log('Typical locations:');
    console.log('  Mac: ~/Documents/Obsidian\\ Vault/');
    console.log('  Win: C:\\Users\\you\\Documents\\Obsidian Vault\\');
    console.log('\nFind your vault:');
    console.log('  find ~ -name "*.md" -path "*obsidian*" -maxdepth 4 2>/dev/null | head -5');
    process.exit(1);
  }

  const before = await stats();
  const files = walkDir(vaultPath);
  console.log(`\n🧠 Obsidian → SierraOS Brain`);
  console.log(`📂 Vault: ${vaultPath}`);
  console.log(`📄 Files: ${files.length} markdown files`);
  console.log(`📊 Brain before: ${before.total || 0} notes\n`);

  let ok = 0;
  for (const file of files) {
    const rel = path.relative(vaultPath, file);
    const raw = fs.readFileSync(file, 'utf-8');
    if (!raw.trim()) continue;
    const title = path.basename(file, '.md');
    const folder = path.dirname(rel);
    const content = raw.length > MAX_CONTENT ? raw.slice(0, MAX_CONTENT) + '\n...(truncated)' : raw;
    const noteContent = `Obsidian: "${title}" (${folder})\n\n${content}`;
    const tags = ['obsidian', folder.replace(/[/\\]/g, '-').toLowerCase()].filter(t => t && t !== '.');

    const success = await ingest(noteContent, 'obsidian', tags, 'note', { title, folder, file: rel, chars: raw.length });
    if (success) {
      ok++;
      process.stdout.write(`  ✅ ${ok}: ${rel}\n`);
    } else {
      process.stdout.write(`  ❌ FAIL: ${rel}\n`);
    }
    await sleep(DELAY_MS);
  }

  const after = await stats();
  console.log(`\n📊 Imported: ${ok}/${files.length} notes`);
  console.log(`📊 Brain after: ${after.total || 0} notes`);
}

main().catch(console.error);
