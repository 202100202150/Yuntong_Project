import test from 'node:test';
import assert from 'node:assert/strict';
import windowSecurity from '../desktop/product-window-security.cjs';

const origin = 'http://127.0.0.1:42031';
test('only the two local monitoring pane URLs may open child windows', () => {
  assert.equal(windowSecurity.paneFromUrl(origin + '/workbench?pane=scene', origin), 'scene');
  assert.equal(windowSecurity.paneFromUrl(origin + '/workbench?pane=video', origin), 'video');
  for (const url of [
    origin + '/workbench', origin + '/devices?pane=scene', origin + '/workbench?pane=unknown',
    origin + '/workbench?pane=scene&pane=video', origin + '/workbench?pane=scene&redirect=https://example.com',
    origin + '/workbench?pane=scene#other', 'https://example.com/workbench?pane=scene',
    'http://127.0.0.1:42032/workbench?pane=scene', 'http://user:password@127.0.0.1:42031/workbench?pane=scene',
    'javascript:alert(1)', 'file:///workbench?pane=scene', 'not a URL',
  ]) assert.equal(windowSecurity.paneFromUrl(url, origin), null, url);
});
