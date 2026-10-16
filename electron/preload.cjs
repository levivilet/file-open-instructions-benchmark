const { ipcRenderer } = require('electron')
window.addEventListener('DOMContentLoaded', () => {
  const documentView = document.querySelector('textarea')
  ipcRenderer.on('document', (_event, file) => {
    documentView.value = file.content
    documentView.dataset.path = file.path
    documentView.focus()
    ipcRenderer.send('displayed')
  })
})
