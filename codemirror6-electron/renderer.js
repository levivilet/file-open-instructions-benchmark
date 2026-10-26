import {EditorState} from '@codemirror/state'
import {EditorView, keymap} from '@codemirror/view'
import {standardKeymap} from '@codemirror/commands'

const parent = document.querySelector('#editor')
const editor = new EditorView({
  state: EditorState.create({doc: '', extensions: [keymap.of(standardKeymap)]}),
  parent,
})
window.documentBridge.onOpen((file) => {
  editor.dispatch({changes: {from: 0, to: editor.state.doc.length, insert: file.content}})
  parent.dataset.path = file.path
  editor.focus()
  window.documentBridge.displayed()
})
