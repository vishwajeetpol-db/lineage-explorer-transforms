import { useState } from 'react';
import { useLineageStore } from '../store/lineageStore';

interface DQMetric {
  rule_id: string;
  column: string;
  rule_type: string;
  pass_rate: number | null;
  total_rows?: number;
  passing_rows?: number;
  failing_rows?: number;
  status: string;
  severity: string;
  error?: string;
}

interface DQResult {
  table_fqn: string;
  metrics: DQMetric[];
  quality_score: number | null;
  quality_grade: string | null;
  rules_evaluated: number;
  rules_total: number;
  sample_size: number;
}

interface Props {
  tableFqn?: string;
}

export function DQMetricsPanel({ tableFqn = '' }: Props) {
  // Running checks executes stored CUSTOM rule expressions as the app service
  // principal, so the backend admin-gates it. Say so up front instead of letting
  // the button return a bare 403.
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const [inputFqn, setInputFqn] = useState(tableFqn);
  const [result, setResult] = useState<DQResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const runMetrics = async () => {
    if (!inputFqn) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`/api/dq-rules/metrics?table_fqn=${encodeURIComponent(inputFqn)}`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setResult(data);
    } catch (e: any) {
      setError(e.message || 'Failed to run metrics');
    } finally {
      setLoading(false);
    }
  };

  const gradeColor = (grade: string | null) => {
    switch (grade) {
      case 'A': return 'text-green-400 bg-green-900/30';
      case 'B': return 'text-blue-400 bg-blue-900/30';
      case 'C': return 'text-yellow-400 bg-yellow-900/30';
      case 'D': return 'text-orange-400 bg-orange-900/30';
      case 'F': return 'text-red-400 bg-red-900/30';
      default: return 'text-gray-400 bg-gray-800';
    }
  };

  const statusIcon = (status: string) => {
    switch (status) {
      case 'pass': return '✅';
      case 'warn': return '⚠️';
      case 'fail': return '❌';
      case 'error': return '💥';
      default: return '➖';
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold text-white mb-2">Data Quality Metrics</h2>
      <p className="text-gray-400 text-sm mb-6">
        Execute DQ rules against a sample and see live pass/fail rates with quality scoring.
      </p>

      <div className="flex gap-3 mb-6">
        <input
          value={inputFqn}
          onChange={e => setInputFqn(e.target.value)}
          placeholder="catalog.schema.table"
          className="flex-1 px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500"
          onKeyDown={e => e.key === 'Enter' && runMetrics()}
        />
        <button onClick={runMetrics} disabled={loading || !inputFqn || !isAdmin}
          title={isAdmin ? undefined : 'Running DQ checks requires admin'}
          className="px-6 py-2 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50">
          {loading ? 'Running...' : 'Run Checks'}
        </button>
      </div>

      {!isAdmin && (
        <p className="text-amber-400/90 text-sm mb-4">
          Running DQ checks executes rule expressions against your data and is restricted to admins.
        </p>
      )}

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      {result && (
        <div>
          {/* Quality Score Card */}
          <div className="mb-6 p-6 bg-gray-800 rounded-lg border border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-400">Overall Quality Score</p>
                <p className="text-3xl font-bold text-white mt-1">
                  {result.quality_score !== null ? `${Math.round(result.quality_score * 100)}%` : 'N/A'}
                </p>
                <p className="text-xs text-gray-500 mt-1">
                  {result.rules_evaluated} of {result.rules_total} rules evaluated · {result.sample_size.toLocaleString()} rows sampled
                </p>
              </div>
              {result.quality_grade && (
                <div className={`w-16 h-16 rounded-full flex items-center justify-center text-2xl font-bold ${gradeColor(result.quality_grade)}`}>
                  {result.quality_grade}
                </div>
              )}
            </div>
          </div>

          {/* Per-rule metrics */}
          {result.metrics.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-gray-400 mb-3">Per-Rule Results</h3>
              {result.metrics.map((m, i) => (
                <div key={i} className="p-3 bg-gray-800 rounded-lg border border-gray-700 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-lg">{statusIcon(m.status)}</span>
                    <div>
                      <p className="text-sm text-white font-medium">
                        {m.rule_type} {m.column && <span className="text-indigo-400">on {m.column}</span>}
                      </p>
                      {m.error && <p className="text-xs text-red-400">{m.error}</p>}
                    </div>
                  </div>
                  <div className="text-right">
                    {m.pass_rate !== null ? (
                      <>
                        <p className={`font-bold ${m.pass_rate >= 0.99 ? 'text-green-400' : m.pass_rate >= 0.9 ? 'text-yellow-400' : 'text-red-400'}`}>
                          {Math.round(m.pass_rate * 100)}%
                        </p>
                        {m.failing_rows !== undefined && m.failing_rows > 0 && (
                          <p className="text-xs text-gray-500">{m.failing_rows.toLocaleString()} failing</p>
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-gray-500">{m.status}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {result.metrics.length === 0 && (
            <p className="text-gray-500 text-sm">No DQ rules defined for this table. Add rules first.</p>
          )}
        </div>
      )}
    </div>
  );
}
