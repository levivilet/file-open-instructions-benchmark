const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('documentBridge', {
  onOpen: (callback) => ipcRenderer.on('document', (_event, file) => callback(file)),
  displayed: () => ipcRenderer.send('displayed'),
})
