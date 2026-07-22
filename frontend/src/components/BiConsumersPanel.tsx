import { useState } from 'react';
import { BarChart3, RefreshCw, Monitor, ExternalLink } from 'lucide-react';

interface BiConsumer {
  source_table: string;
  entity_type: string;
  entity_id: string;
  entity_name: string;
  last_query_time: string;
  query_count: number;
}

interface Props {
  catalog?: string;
}

export function BiConsumersPanel({ catalog = '' }: Props) {
  const [inputCatalog, setInputCatalog] = useState(catalog);
  const [consumers, setConsumers] = useState<BiConsumer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lookbackDays, setLookbackDays] = useState<number | null>(null);

  const fetchConsumers = async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (inputCatalog) params.set('catalog', inputCatalog);
      const resp = await fetch(`/api/lineage/bi-consumers?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.available === false) {
        setError(data.error || 'BI consumer detection unavailable');
        setConsumers([]);
      } else {
        setConsumers(data.bi_consumers || []);
        setLookbackDays(data.lookback_days || null);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to fetch BI consumers');
    } finally {
      setLoading(false);
    }
  };

  const entityIcon = (type: string) => {
    switch (type.toUpperCase()) {
      case 'DASHBOARD': return <Monitor size={12} className="text-blue-400" />;
      default: return <ExternalLink size={12} className="text-slate-400" />;
    }
  };

  return (
    <div className="min-h-screen bg-surface text-slate-200 p-6">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <BarChart3 size={22} className="text-cyan-400" />
          <h1 className="text-xl font-semibold">BI Consumers</h1>
        </div>
        <p className="text-sm text-slate-400 mb-4">
          Discover dashboards, notebooks, and BI tools that consume your lineage tables.
        </p>

        <div className="flex gap-2 mb-6">
          <input
            className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-accent/50"
            placeholder="Catalog (optional — leave blank for all)"
            value={inputCatalog}
            onChange={(e) => setInputCatalog(e.target.value)}
          />
          <button
            onClick={fetchConsumers}
            disabled={loading}
            className="flex items-center gap-1.5 px-4 py-2 bg-accent/20 hover:bg-accent/30 border border-accent/30 rounded-lg text-sm font-medium text-accent-light transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {loading ? 'Scanning...' : 'Detect Consumers'}
          </button>
        </div>

        {error && (
          <div className="p-3 mb-4 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-300">
            {error}
          </div>
        )}

        {consumers.length > 0 && (
          <div className="bg-white/[0.02] border border-white/[0.06] rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-white/[0.06] flex items-center justify-between">
              <span className="text-xs text-slate-400 font-medium">
                {consumers.length} consumer{consumers.length !== 1 ? 's' : ''} found
              </span>
              {lookbackDays && (
                <span className="text-xs text-slate-500">Last {lookbackDays} days</span>
              )}
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.04] text-slate-500">
                  <th className="text-left px-4 py-2 font-medium">Consumer</th>
                  <th className="text-left px-4 py-2 font-medium">Type</th>
                  <th className="text-left px-4 py-2 font-medium">Source Table</th>
                  <th className="text-right px-4 py-2 font-medium">Queries</th>
                  <th className="text-right px-4 py-2 font-medium">Last Access</th>
                </tr>
              </thead>
              <tbody>
                {consumers.map((c, i) => (
                  <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                    <td className="px-4 py-2.5 flex items-center gap-1.5">
                      {entityIcon(c.entity_type)}
                      <span className="text-slate-300">{c.entity_name || c.entity_id}</span>
                    </td>
                    <td className="px-4 py-2.5 text-slate-400">{c.entity_type}</td>
                    <td className="px-4 py-2.5 text-slate-400 font-mono">{c.source_table}</td>
                    <td className="px-4 py-2.5 text-right text-slate-300">{c.query_count}</td>
                    <td className="px-4 py-2.5 text-right text-slate-500">{c.last_query_time?.split('T')[0] || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && consumers.length === 0 && lookbackDays !== null && (
          <div className="text-center py-12 text-slate-500 text-sm">
            No BI consumers detected. Try broadening the catalog scope.
          </div>
        )}
      </div>
    </div>
  );
}
