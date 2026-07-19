import { useState } from 'react';

interface Candidate {
  candidate_table: string;
  candidate_column: string;
  hop_distance: number;
  score: number;
  evidence: string[];
}

interface RootCauseResult {
  candidates: Candidate[];
  upstream_path: any[];
  timeline: any[];
  summary: string;
}

interface Props {
  catalog?: string;
  schema?: string;
  table?: string;
  column?: string;
}

export function RootCauseWizard({ catalog = '', schema = '', table = '', column = '' }: Props) {
  const [formState, setFormState] = useState({
    catalog, schema, table, column, anomaly_timestamp: '',
  });
  const [result, setResult] = useState<RootCauseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [step, setStep] = useState<'input' | 'analyzing' | 'results'>('input');

  const runAnalysis = async () => {
    setLoading(true);
    setError('');
    setStep('analyzing');
    try {
      const res = await fetch('/api/root-cause/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          catalog: formState.catalog,
          schema_name: formState.schema,
          table: formState.table,
          column: formState.column,
          anomaly_timestamp: formState.anomaly_timestamp || undefined,
          max_hops: 6,
          include_dq: true,
          include_run_health: true,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setResult(data);
      setStep('results');
    } catch (e: any) {
      setError(e.message || 'Analysis failed');
      setStep('input');
    } finally {
      setLoading(false);
    }
  };

  const scoreColor = (score: number) => {
    if (score >= 0.8) return 'text-red-400';
    if (score >= 0.5) return 'text-yellow-400';
    return 'text-green-400';
  };

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <h2 className="text-2xl font-bold text-white mb-2">Root Cause Analysis</h2>
      <p className="text-gray-400 text-sm mb-6">
        Trace a data quality issue or anomaly back to its likely source by walking
        upstream lineage and correlating with producer run failures.
      </p>

      {step === 'input' && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Catalog</label>
              <input value={formState.catalog} onChange={e => setFormState({...formState, catalog: e.target.value})}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white" placeholder="my_catalog" />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Schema</label>
              <input value={formState.schema} onChange={e => setFormState({...formState, schema: e.target.value})}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white" placeholder="my_schema" />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Table</label>
              <input value={formState.table} onChange={e => setFormState({...formState, table: e.target.value})}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white" placeholder="affected_table" />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Column</label>
              <input value={formState.column} onChange={e => setFormState({...formState, column: e.target.value})}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white" placeholder="affected_column" />
            </div>
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Anomaly Timestamp (optional, ISO 8601)</label>
            <input type="datetime-local" value={formState.anomaly_timestamp}
              onChange={e => setFormState({...formState, anomaly_timestamp: e.target.value})}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white" />
          </div>
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button onClick={runAnalysis} disabled={!formState.catalog || !formState.schema || !formState.table || !formState.column}
            className="w-full py-3 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed">
            Analyze Root Cause
          </button>
        </div>
      )}

      {step === 'analyzing' && (
        <div className="text-center py-12">
          <div className="animate-spin w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-gray-400">Walking upstream lineage and correlating failures...</p>
          <p className="text-xs text-gray-600 mt-2">This may take 10-30 seconds</p>
        </div>
      )}

      {step === 'results' && result && (
        <div className="space-y-6">
          <button onClick={() => setStep('input')} className="text-sm text-indigo-400 hover:text-indigo-300">
            ← Run another analysis
          </button>

          {result.candidates.length === 0 ? (
            <div className="p-6 bg-green-900/20 border border-green-800 rounded-lg">
              <p className="text-green-400 font-medium">No likely root cause found</p>
              <p className="text-sm text-gray-400 mt-1">
                No upstream producer failures or DQ violations were detected in the analysis window.
              </p>
            </div>
          ) : (
            <div>
              <h3 className="text-lg font-semibold text-white mb-3">
                {result.candidates.length} candidate{result.candidates.length > 1 ? 's' : ''} found
              </h3>
              <div className="space-y-3">
                {result.candidates.map((c, i) => (
                  <div key={i} className="p-4 bg-gray-800 rounded-lg border border-gray-700">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium text-white">
                          #{i + 1} {c.candidate_table}
                          {c.candidate_column && <span className="text-indigo-400">.{c.candidate_column}</span>}
                        </p>
                        <p className="text-xs text-gray-500 mt-1">{c.hop_distance} hop{c.hop_distance > 1 ? 's' : ''} upstream</p>
                      </div>
                      <span className={`text-lg font-bold ${scoreColor(c.score)}`}>
                        {Math.round(c.score * 100)}%
                      </span>
                    </div>
                    {c.evidence.length > 0 && (
                      <div className="mt-3 space-y-1">
                        {c.evidence.map((ev, j) => (
                          <p key={j} className="text-xs text-gray-400 flex items-center gap-1">
                            <span className="text-yellow-500">⚠</span> {ev}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
