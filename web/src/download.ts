/** 触发浏览器下载。Tauri 打包后同样有效（WebView 走一样的路径）。 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 立刻 revoke 在部分浏览器会打断下载，给一拍缓冲
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
