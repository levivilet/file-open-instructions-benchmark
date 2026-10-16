await (async function getLocalProblemsSummary(activeUri, applicationId) {
  init_GetActiveEditor();
  const editorId = await getActiveEditorId(applicationId);
  if (editorId === -1) return {errorCount: 0, hasEditor: false, problemCount: 0, warningCount: 0};
  const [allDiagnostics, activeDiagnostics] = await Promise.all([
    Promise.try(() => actualInvoke('Editor.getProblems')),
    Promise.try(() => actualInvoke('Editor.getDiagnostics', editorId)).catch(() => []),
  ]);
  const visibleUris = await getVisibleEditorUris(applicationId);
  const visibleUriSet = new Set(visibleUris);
  if (activeUri) visibleUriSet.add(activeUri);
  const unique = (diagnostics) => {
    const keys = new Set();
    return diagnostics.filter((diagnostic) => {
      const key = JSON.stringify([diagnostic.uri, diagnostic.rowIndex, diagnostic.columnIndex, diagnostic.message, diagnostic.source, diagnostic.type, diagnostic.code]);
      if (keys.has(key)) return false;
      keys.add(key);
      return true;
    });
  };
  const all = unique(allDiagnostics.filter((diagnostic) => visibleUriSet.has(diagnostic.uri)));
  const active = unique(activeDiagnostics);
  return {
    errorCount: active.filter((diagnostic) => diagnostic.type === 'error').length,
    hasEditor: true,
    problemCount: all.length,
    warningCount: active.filter((diagnostic) => diagnostic.type === 'warning').length,
  };
})(activeUri, state51.applicationId)
