import { useState } from 'react';
import { Radio, RefreshCw, Zap, ArrowRight } from 'lucide-react';

interface StreamNode {
  table_fqn: string;
  table_type: string;
  source_format?: string;
  is_streaming: boolean;
}

interface StreamEdge {
  source: string;
  target: string;
  relationship: string;
}

interface Props {
  catalog?: string;
}

export function StreamingTopologyPanel({ catalog = '' }: Props) {
  const [inputCatalog, setInputCatalog] = useState(catalog);
  const [nodes, setNodes] = useState<StreamNode[]>([]);
  const [edges, setEdges] = useState<StreamEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const fetchTopology = async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (inputCatalog) params.set('catalog', inputCatalog);
      const resp = await fetch(`/api/lineage/streaming-topology?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.available === false) {
        setError(data.error || 'Streaming topology unavailable');
        setNodes([]);
        setEdges([]);
      } else {
        setNodes(data.streaming_tables || []);
        setEdges(data.edges || []);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to fetch streaming topology');
    } finally {
      setLoading(false);
    }
  };

  const streamingNodes = nodes.filter(n => n.is_streaming);
  const sourceNodes = nodes.filter(n => !n.is_streaming);

  return (
    <div className="min-h-screen bg-surface text-slate-200 p-6">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <Radio size={22} className="text-emerald-400" />
          <h1 className="text-xl font-semibold">Streaming Topology</h1>
        </div>
        <p className="text-sm text-slate-400 mb-4">
          Visualize streaming tables and their source-to-sink data flow.
        </p>

        <div className="flex gap-2 mb-6">
          <input
            className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-accent/50"
            placeholder="Catalog (optional — leave blank for all)"
            value={inputCatalog}
            onChange={(e) => setInputCatalog(e.target.value)}
          />
          <button
            onClick={fetchTopology}
            disabled={loading}
            className="flex items-center gap-1.5 px-4 py-2 bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 rounded-lg text-sm font-medium text-emerald-300 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {loading ? 'Scanning...' : 'Detect Topology'}
          </button>
        </div>

        {error && (
          <div className="p-3 mb-4 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-300">
            {error}
          </div>
        )}

        {nodes.length > 0 && (
          <div className="space-y-4">
            {/* Summary */}
            <div className="grid grid-cols-2 gap-3">
              <div className="p-4 bg-emerald-500/5 border border-emerald-500/15 rounded-xl">
                <div className="text-2xl font-bold text-emerald-300">{streamingNodes.length}</div>
                <div className="text-xs text-slate-400 mt-1">Streaming Tables</div>
              </div>
              <div className="p-4 bg-blue-500/5 border border-blue-500/15 rounded-xl">
                <div className="text-2xl font-bold text-blue-300">{sourceNodes.length}</div>
                <div className="text-xs text-slate-400 mt-1">Source Tables</div>
              </div>
            </div>

            {/* Streaming tables list */}
            <div className="bg-white/[0.02] border border-white/[0.06] rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-white/[0.06]">
                <span className="text-xs text-slate-400 font-medium">Streaming Data Flow</span>
              </div>
              <div className="divide-y divide-white/[0.03]">
                {edges.map((edge, i) => (
                  <div key={i} className="px-4 py-3 flex items-center gap-2 hover:bg-white/[0.02]">
                    <span className="text-xs font-mono text-slate-400 flex-1 truncate">{edge.source}</span>
                    <ArrowRight size={12} className="text-emerald-400 flex-shrink-0" />
                    <span className="text-xs font-mono text-slate-300 flex-1 truncate">{edge.target}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      {edge.relationship || 'stream'}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Streaming tables detail */}
            {streamingNodes.length > 0 && (
              <div className="bg-white/[0.02] border border-white/[0.06] rounded-xl overflow-hidden">
                <div className="px-4 py-3 border-b border-white/[0.06]">
                  <span className="text-xs text-slate-400 font-medium">Streaming Table Details</span>
                </div>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/[0.04] text-slate-500">
                      <th className="text-left px-4 py-2 font-medium">Table</th>
                      <th className="text-left px-4 py-2 font-medium">Type</th>
                      <th className="text-left px-4 py-2 font-medium">Format</th>
                    </tr>
                  </thead>
                  <tbody>
                    {streamingNodes.map((n, i) => (
                      <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                        <td className="px-4 py-2.5 flex items-center gap-1.5">
                          <Zap size={11} className="text-emerald-400" />
                          <span className="font-mono text-slate-300">{n.table_fqn}</span>
                        </td>
                        <td className="px-4 py-2.5 text-slate-400">{n.table_type}</td>
                        <td className="px-4 py-2.5 text-slate-500">{n.source_format || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {!loading && !error && nodes.length === 0 && edges.length === 0 && (
          <div className="text-center py-12 text-slate-500 text-sm">
            Click "Detect Topology" to discover streaming tables and their data flow.
          </div>
        )}
      </div>
    </div>
  );
}
