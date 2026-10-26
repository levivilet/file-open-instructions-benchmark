import CodeMirror from 'codemirror'
import 'codemirror/lib/codemirror.css'

const parent = document.querySelector('#editor')
const editor = CodeMirror(parent, {value: '', lineNumbers: false, lineWrapping: false, mode: null})
window.documentBridge.onOpen((file) => {
  editor.setValue(file.content)
  parent.dataset.path = file.path
  editor.focus()
  window.documentBridge.displayed()
})
