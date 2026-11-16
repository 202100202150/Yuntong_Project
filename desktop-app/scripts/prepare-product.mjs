import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(desktopRoot);
const npmCli = process.env.npm_execpath || path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js');
async function npm(args, cwd) {
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [npmCli, ...args], { cwd, stdio: 'inherit', windowsHide: true, shell: false });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error('npm ' + args.join(' ') + ' exited ' + code)));
  });
}
if (process.argv.includes('--skip-renderer')) await fs.access(path.join(root, 'frontend', 'dist', 'index.html'));
else await npm(['run', 'build'], path.join(root, 'frontend'));
if (process.argv.includes('--renderer-only')) process.exit(0);

const output = path.join(desktopRoot, 'product-runtime');
if (path.dirname(output) !== desktopRoot || path.basename(output) !== 'product-runtime') throw new Error('Invalid runtime staging path');
const old = await fs.lstat(output).catch(() => null);
if (old?.isSymbolicLink()) throw new Error('Runtime staging path must not be a symlink');
await fs.rm(output, { recursive: true, force: true });
await fs.mkdir(output, { recursive: true });
await fs.cp(path.join(root, 'frontend', 'dist'), path.join(output, 'frontend', 'dist'), { recursive: true });
const backend = path.join(output, 'backend');
await fs.mkdir(backend, { recursive: true });
for (const name of ['package.json', 'package-lock.json']) await fs.copyFile(path.join(root, 'backend', name), path.join(backend, name));
await fs.cp(path.join(root, 'backend', 'src'), path.join(backend, 'src'), { recursive: true });
await npm(['ci', '--omit=dev', '--no-audit', '--no-fund'], backend);

const cameraSource = path.join(root, 'camera-rtsp-low-latency');
const cameraDestination = path.join(output, 'camera-rtsp-low-latency');
await fs.mkdir(cameraDestination, { recursive: true });
for (const entry of await fs.readdir(cameraSource, { withFileTypes: true })) {
  if (!entry.isFile()) continue;
  if (!entry.name.endsWith('.py') && entry.name !== 'aprilgrid-6x6.json' && !/^requirements.*\.txt$/.test(entry.name)) continue;
  if (entry.name.startsWith('test_')) continue;
  await fs.copyFile(path.join(cameraSource, entry.name), path.join(cameraDestination, entry.name));
}
await fs.mkdir(path.join(output, 'desktop-app', 'models'), { recursive: true });
const model = path.join(desktopRoot, 'models', 'drone.onnx');
if (await fs.stat(model).catch(() => null)) {
  await fs.copyFile(model, path.join(output, 'desktop-app', 'models', 'drone.onnx'));
} else {
  console.warn('drone.onnx 未包含于源目录；安装后请配置 YUNTONG_DRONE_MODEL。');
}
console.log('Product runtime prepared: ' + output);
