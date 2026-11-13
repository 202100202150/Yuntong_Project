import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const child = spawn(require('electron'), [path.join(root, 'desktop', 'main.cjs')], { cwd: root, stdio: 'inherit', windowsHide: true });
child.once('exit', code => process.exit(code ?? 1));
child.once('error', error => { console.error(error); process.exit(1); });
