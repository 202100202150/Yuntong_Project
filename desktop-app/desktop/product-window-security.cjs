const permittedPanes = new Set(['scene', 'video']);

function sameOrigin(url, origin) {
  try {
    const parsed = new URL(url);
    return parsed.origin === origin && !parsed.username && !parsed.password;
  } catch { return false; }
}

function paneFromUrl(url, origin) {
  if (!sameOrigin(url, origin)) return null;
  const parsed = new URL(url);
  const entries = [...parsed.searchParams.entries()];
  if (parsed.pathname !== '/workbench' || parsed.hash || entries.length !== 1 || entries[0][0] !== 'pane') return null;
  return permittedPanes.has(entries[0][1]) ? entries[0][1] : null;
}

module.exports = { sameOrigin, paneFromUrl };
