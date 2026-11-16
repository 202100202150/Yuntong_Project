const { app } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { randomBytes } = require('node:crypto');

// Exercise the real toolbar and rendered model, with an isolated local account.
const work = path.resolve(__dirname, '../work');
fs.mkdirSync(work, { recursive: true });
const profile = fs.mkdtempSync(path.join(work, 'scene-reset-'));
process.env.YUNTONG_PROFILE_DIR = profile;
process.env.YUNTONG_DATA_DIR = path.join(profile, 'data');
process.env.YUNTONG_CAMERA_PROJECT_DIR = path.join(profile, 'no-camera-project');
process.env.YUNTONG_CAMERA_PYTHON = path.join(profile, 'no-python.exe');
const output = process.env.YUNTONG_ACTIONS_OUTPUT || profile;
fs.mkdirSync(output, { recursive: true });
const checks = [], errors = [];
let window, closing = false, origin;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const deadline = setTimeout(() => fail(new Error('Scene reset smoke timed out')), 90000);
function fail(error) { clearTimeout(deadline); console.error(error.stack || error); app.exit(1); }
async function evaluate(code) { return window.webContents.executeJavaScript(code, true); }
async function waitFor(code, message, timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { if (await evaluate(code)) return; await delay(50); }
  throw new Error(message);
}
async function click(label) { await evaluate(`document.querySelector('[aria-label="${label}"]').click()`); }
async function capture() {
  const rect = await evaluate(`(()=>{const r=document.querySelector('.campus-scene>canvas').getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()`);
  // A hidden native window may retain its previous compositor frame until capture.
  await window.webContents.capturePage(rect);
  window.webContents.invalidate();
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  await delay(100);
  return window.webContents.capturePage(rect);
}
function pixelDifference(a, b) {
  assert.deepEqual(a.getSize(), b.getSize());
  const first = a.toBitmap(), second = b.toBitmap(); let changed = 0;
  for (let i = 0; i < first.length; i += 4) {
    if (Math.max(Math.abs(first[i] - second[i]), Math.abs(first[i+1] - second[i+1]), Math.abs(first[i+2] - second[i+2])) > 3) changed++;
  }
  return changed / (first.length / 4);
}
async function drag(button) {
  const point = await evaluate(`(()=>{const r=document.querySelector('.campus-scene>canvas').getBoundingClientRect();return {x:Math.round(r.x+r.width*.43),y:Math.round(r.y+r.height*.4)}})()`);
  const input = event => window.webContents.sendInputEvent(event);
  input({type:'mouseMove', ...point}); input({type:'mouseDown', ...point, button, clickCount:1});
  for (let i=1;i<=8;i++) { input({type:'mouseMove',x:point.x+i*12,y:point.y+i*5,button}); await delay(12); }
  input({type:'mouseUp',x:point.x+96,y:point.y+40,button,clickCount:1});
}
async function assertRestored(baseline, name) {
  await click('恢复校园全景');
  await waitFor(`document.body.innerText.includes('校园全景已恢复')`, 'Reset did not acknowledge completion');
  await delay(800); const restored = await capture();
  const difference = pixelDifference(baseline, restored);
  assert.ok(difference < .005, `${name}: ${Math.round(difference*10000)/100}% of pixels still differ after reset`);
  await delay(500);
  assert.ok(pixelDifference(restored, await capture()) < .001, `${name}: camera kept drifting after reset`);
  checks.push({name,passed:true,changedPixelFraction:difference});
}
app.on('browser-window-created', (_event, created) => {
  if (window) return;
  window=created; window.show=()=>{}; window.webContents.setBackgroundThrottling(false);
  window.webContents.on('console-message', event => { if (event.level==='error') errors.push(event.message); });
  window.webContents.once('did-finish-load', () => run().catch(fail));
});
app.on('will-quit', event => {
  if (!closing) return;
  event.preventDefault();
  (async()=>{
    await assert.rejects(fetch(origin+'/api/auth/status', {signal:AbortSignal.timeout(1500)}));
    const report={ok:true,checks,errors,isolatedProfile:true};
    fs.writeFileSync(path.join(output,'scene-reset-validation.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));clearTimeout(deadline);app.exit(0);
  })().catch(fail);
});
async function run() {
  origin=new URL(window.webContents.getURL()).origin;
  const password=randomBytes(24).toString('hex');
  assert.equal(await evaluate(`fetch('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'resetcheck',password:'${password}'})}).then(r=>r.status)`),201);
  await window.loadURL(origin+'/workbench');
  await waitFor(`document.querySelector('[data-testid="campus-scene"]')?.dataset.ready==='true'`, 'Local campus did not load',45000);
  await evaluate('document.fonts.ready.then(()=>true)');await delay(400);
  const baseline=await capture();
  fs.writeFileSync(path.join(output,'scene-reset-default.png'),baseline.toPNG());

  await click('放大三维模型');await click('放大三维模型');await delay(150);
  const zoomed=await capture();assert.ok(pixelDifference(baseline,zoomed)>.05,'Zoom control did not actually change the image');
  fs.writeFileSync(path.join(output,'scene-reset-zoomed.png'),zoomed.toPNG());
  await assertRestored(baseline,'Toolbar restores real zoomed model');
  await drag('left');await assertRestored(baseline,'Reset cancels pending rotation inertia');
  await drag('right');await assertRestored(baseline,'Reset cancels pending pan inertia');
  await click('自动环绕观察');await delay(350);await assertRestored(baseline,'Reset stops auto orbit without subsequent drift');
  assert.equal(await evaluate(`document.querySelector('[aria-label="自动环绕观察"]').getAttribute('aria-pressed')`),'false');

  await evaluate(`Array.from(document.querySelectorAll('.scene-toolbar .segmented button')).find(b=>b.textContent==='平面').click()`);
  await click('恢复校园全景');await delay(400);const plan=await capture();
  await click('放大三维模型');await drag('right');await assertRestored(plan,'Plan mode restores complete campus and retains plan projection');
  assert.equal(await evaluate(`document.querySelector('[data-testid="campus-scene"]').dataset.mode`),'2d');
  await click('放大三维模型');await evaluate(`document.querySelector('.campus-scene>canvas').focus()`);
  window.webContents.sendInputEvent({type:'keyDown',keyCode:'Home'});window.webContents.sendInputEvent({type:'keyUp',keyCode:'Home'});
  await delay(500);assert.ok(pixelDifference(plan,await capture())<.005,'Home key did not reset plan');
  checks.push({name:'Home keyboard shortcut restores the same rendered view',passed:true});

  await evaluate(`Array.from(document.querySelectorAll('.scene-toolbar .segmented button')).find(b=>b.textContent==='透视').click()`);
  await assertRestored(baseline,'Already-restored view still gives completion feedback');
  fs.writeFileSync(path.join(output,'scene-reset-restored.png'),(await capture()).toPNG());
  assert.deepEqual(errors,[]);closing=true;window.close();
}
require('../desktop/product-main.cjs');
