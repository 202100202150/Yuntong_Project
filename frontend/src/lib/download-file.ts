/** Check access first, then let the browser stream the file without buffering a large recording. */
export async function downloadFile(url: string, name?: string): Promise<void> {
  const target = new URL(url, window.location.href);
  if (target.origin !== window.location.origin || !['http:', 'https:'].includes(target.protocol)) {
    throw new Error('下载地址无效，请重新获取文件。');
  }
  const response = await fetch(target.href, { method: 'HEAD', credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) {
    const errors: Record<number, string> = {401:'登录已失效，请重新登录后下载。',403:'当前账号没有下载权限。',404:'文件不存在或已被清理，请刷新列表。'};
    throw new Error(errors[response.status] || `文件暂时无法下载（HTTP ${response.status}）。`);
  }
  if (response.headers.get('content-length') === '0') throw new Error('文件为空，无法下载。');
  if (response.headers.get('content-type')?.includes('text/html')) throw new Error('下载地址未返回文件，请刷新后重试。');
  const link = document.createElement('a');
  link.href = target.href;
  link.download = name || '';
  document.body.appendChild(link);
  link.click();
  link.remove();
}
