export const timestamp = () =>
  new Intl.DateTimeFormat('sv-SE', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  })
    .format(new Date())
    .replaceAll('-', '')
    .replaceAll(':', '')
    .replace(' ', '_');

export const baseName = (name: string) => name.replace(/\.[^.]+$/, '') || 'media';