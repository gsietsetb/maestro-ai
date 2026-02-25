/**
 * SierraOS Brain — Quick note from CLI
 * Usage: npx tsx scripts/dump-note.ts "Tu nota aqui" [category]
 */
import { ingest, stats } from './config';

async function main() {
  const content = process.argv[2];
  const category = process.argv[3] || 'note';

  if (!content) {
    console.log('SierraOS Brain — Quick Note\n');
    console.log('Usage: npx tsx scripts/dump-note.ts "Tu nota" [category]\n');
    console.log('Categories: note, idea, todo, tech, business, personal\n');
    console.log('Example:');
    console.log('  npx tsx scripts/dump-note.ts "Llamar a Carlos sobre TruesDate Asia" todo');
    console.log('  npx tsx scripts/dump-note.ts "React Server Components son el futuro" tech');
    process.exit(1);
  }

  const ok = await ingest(content, 'cli', [], category);
  if (ok) {
    const s = await stats();
    console.log(`✅ Guardado (${category}) — Brain: ${s.total} notas`);
  } else {
    console.log('❌ Error guardando nota');
  }
}

main().catch(console.error);
