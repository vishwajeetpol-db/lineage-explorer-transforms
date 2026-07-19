import { useState } from 'react';

/**
 * ExportPanel - Provides OpenLineage export/import and graph snapshot functionality.
 * Covers capabilities 07 (Versioned Lineage) and 17 (Open Standards).
 */
export function ExportPanel({ catalog = '', schema = '' }: { catalog?: string; schema?: string }) {
  const [activeTab, setActiveTab] = useState<'export' | 'import' | 'snapshots'>('export');
  const [exporting, setExporting] = useState(false);
  const [importData, setImportData] = useState('');
  const [importResult, setImportResult] = useState<any>(null);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [snapshotLoading, setSnapshotLoading] = useState(false);

  const handleExportOpenLineage = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams({ catalog });
      if (schema) params.set('schema', schema);
      params.set('include_columns', 'true');
      const res = await fetch(`/api/export/openlineage?${params}`);
      const data = await res.json();
      // Download as JSON file
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `openlineage_${catalog}${schema ? '.' + schema : ''}_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Export failed:', e);
    } finally {
      setExporting(false);
    }
  };

  const handleImportOpenLineage = async () => {
    try {
      const events = JSON.parse(importData);
      const res = await fetch('/api/import/openlineage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events: Array.isArray(events) ? events : [events] }),
      });
      setImportResult(await res.json());
    } catch (e: any) {
      setImportResult({ error: e.message });
    }
  };

  const loadSnapshots = async () => {
    setSnapshotLoading(true);
    try {
      const params = catalog ? `?scope=${catalog}${schema ? '.' + schema : ''}` : '';
      const res = await fetch(`/api/snapshots${params}`);
      const data = await res.json();
      setSnapshots(data.snapshots || []);
    } catch (e) {
      console.error('Failed to load snapshots:', e);
    } finally {
      setSnapshotLoading(false);
    }
  };

  const captureSnapshot = async () => {
    try {
      const res = await fetch('/api/snapshots/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ catalog, schema_name: schema || null }),
      });
      const data = await res.json();
      alert(`Snapshot captured: ${data.node_count} nodes, ${data.edge_count} edges`);
      loadSnapshots();
    } catch (e) {
      console.error('Capture failed:', e);
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold text-white mb-6">Export & Interop</h2>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-gray-800 rounded-lg p-1">
        {(['export', 'import', 'snapshots'] as const).map(tab => (
          <button key={tab} onClick={() => { setActiveTab(tab); if (tab === 'snapshots') loadSnapshots(); }}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'
            }`}>
            {tab === 'export' ? 'OpenLineage Export' : tab === 'import' ? 'Import Events' : 'Graph Snapshots'}
          </button>
        ))}
      </div>

      {activeTab === 'export' && (
        <div className="space-y-4">
          <p className="text-gray-400 text-sm">
            Export the current lineage graph as OpenLineage RunEvents (JSON). Compatible with
            Marquez, Atlan, DataHub, and OpenMetadata.
          </p>
          <div className="p-4 bg-gray-800 rounded-lg border border-gray-700">
            <p className="text-sm text-gray-300">Scope: <span className="text-indigo-400">{catalog}{schema ? '.' + schema : ' (all schemas)'}</span></p>
          </div>
          <button onClick={handleExportOpenLineage} disabled={!catalog || exporting}
            className="px-6 py-3 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50">
            {exporting ? 'Exporting...' : 'Export as OpenLineage JSON'}
          </button>
        </div>
      )}

      {activeTab === 'import' && (
        <div className="space-y-4">
          <p className="text-gray-400 text-sm">
            Paste OpenLineage RunEvent JSON to import external lineage into the graph.
          </p>
          <textarea value={importData} onChange={e => setImportData(e.target.value)}
            placeholder='{"eventType": "COMPLETE", "job": {...}, "inputs": [...], "outputs": [...]}'
            className="w-full h-48 px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white font-mono text-xs" />
          <button onClick={handleImportOpenLineage} disabled={!importData}
            className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
            Import Events
          </button>
          {importResult && (
            <pre className="p-3 bg-gray-900 rounded text-xs text-gray-300">
              {JSON.stringify(importResult, null, 2)}
            </pre>
          )}
        </div>
      )}

      {activeTab === 'snapshots' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-gray-400 text-sm">Point-in-time graph snapshots for historical comparison.</p>
            <button onClick={captureSnapshot} disabled={!catalog}
              className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-50">
              Capture Now
            </button>
          </div>
          {snapshotLoading ? <p className="text-gray-500">Loading...</p> :
            snapshots.length === 0 ? <p className="text-gray-500">No snapshots yet. Capture one to start tracking changes.</p> :
            <div className="space-y-2">
              {snapshots.map((s: any) => (
                <div key={s.snapshot_id} className="p-3 bg-gray-800 rounded-lg border border-gray-700 flex items-center justify-between">
                  <div>
                    <p className="text-white text-sm font-medium">{s.label || s.scope}</p>
                    <p className="text-xs text-gray-500">{new Date(s.captured_at).toLocaleString()} · {s.node_count} nodes · {s.edge_count} edges</p>
                  </div>
                  <span className="text-xs text-gray-600 font-mono">{s.snapshot_id.slice(0, 8)}</span>
                </div>
              ))}
            </div>
          }
        </div>
      )}
    </div>
  );
}
