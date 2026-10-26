const { app, BrowserWindow, dialog, ipcMain, Menu } = require('electron')
const { readFile } = require('node:fs/promises')
const path = require('node:path')
const editorName = 'CodeMirror 5'

app.whenReady().then(() => {
  const window = new BrowserWindow({
    width: 1024,
    height: 768,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs') },
  })
  const open = async () => {
    const selection = await dialog.showOpenDialog(window, { properties: ['openFile'] })
    if (selection.canceled || selection.filePaths.length !== 1) return
    const file = selection.filePaths[0]
    const content = await readFile(file, 'utf8')
    window.webContents.send('document', { path: file, content })
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'File', submenu: [{ label: 'Open File', accelerator: 'Ctrl+O', click: open }] },
    { label: 'Edit', submenu: [{ role: 'copy' }] },
  ]))
  ipcMain.on('displayed', () => window.setTitle(editorName + ' — file displayed'))
  window.loadFile(path.join(__dirname, 'index.html'))
})
app.on('window-all-closed', () => app.quit())
