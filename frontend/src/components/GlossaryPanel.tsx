import { useState, useEffect, useCallback } from 'react';
import { useLineageStore } from '../store/lineageStore';

interface Term {
  term_id: string;
  name: string;
  definition: string;
  domain: string;
  owner: string;
  status: string;
  synonyms: string;
}

interface Domain {
  domain_id: string;
  name: string;
  description: string;
  owner: string;
  color: string;
}

interface Kpi {
  kpi_id: string;
  name: string;
  definition: string;
  formula_sql: string;
  source_tables: string;
  owner: string;
  domain: string;
  granularity: string;
}

export function GlossaryPanel() {
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const [terms, setTerms] = useState<Term[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [activeTab, setActiveTab] = useState<'terms' | 'domains' | 'kpis'>('terms');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState<Partial<Term>>({
    name: '', definition: '', domain: '', owner: '', status: 'draft', synonyms: ''
  });

  const fetchTerms = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (searchQuery) params.set('q', searchQuery);
      const res = await fetch(`/api/glossary/terms?${params}`);
      const data = await res.json();
      setTerms(data.terms || []);
    } catch (e) {
      console.error('Failed to fetch terms:', e);
    } finally {
      setLoading(false);
    }
  }, [searchQuery]);

  const fetchDomains = useCallback(async () => {
    try {
      const res = await fetch('/api/glossary/domains');
      const data = await res.json();
      setDomains(data.domains || []);
    } catch (e) {
      console.error('Failed to fetch domains:', e);
    }
  }, []);

  const fetchKpis = useCallback(async () => {
    try {
      const res = await fetch('/api/glossary/kpis');
      const data = await res.json();
      setKpis(data.kpis || []);
    } catch (e) {
      console.error('Failed to fetch KPIs:', e);
    }
  }, []);

  useEffect(() => {
    fetchTerms();
    fetchDomains();
    fetchKpis();
  }, [fetchTerms, fetchDomains, fetchKpis]);

  const handleSubmitTerm = async () => {
    try {
      await fetch('/api/glossary/terms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });
      setShowForm(false);
      setFormData({ name: '', definition: '', domain: '', owner: '', status: 'draft', synonyms: '' });
      fetchTerms();
    } catch (e) {
      console.error('Failed to save term:', e);
    }
  };

  const handleDeleteTerm = async (termId: string) => {
    if (!confirm('Delete this term?')) return;
    try {
      await fetch(`/api/glossary/terms/${termId}`, { method: 'DELETE' });
      fetchTerms();
    } catch (e) {
      console.error('Failed to delete term:', e);
    }
  };

  const statusBadge = (status: string) => {
    const colors: Record<string, string> = {
      draft: 'bg-yellow-100 text-yellow-800',
      approved: 'bg-green-100 text-green-800',
      deprecated: 'bg-red-100 text-red-800',
    };
    return (
      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] || 'bg-gray-100 text-gray-800'}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-white">Business Glossary</h2>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
        >
          {showForm ? 'Cancel' : '+ Add Term'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 bg-gray-800 rounded-lg p-1">
        {(['terms', 'domains', 'kpis'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            {tab === 'terms' ? 'Terms' : tab === 'domains' ? 'Domains' : 'KPIs'}
          </button>
        ))}
      </div>

      {/* Search */}
      {activeTab === 'terms' && (
        <input
          type="text"
          placeholder="Search terms..."
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && fetchTerms()}
          className="w-full px-4 py-2 mb-4 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500"
        />
      )}

      {/* Add Term Form */}
      {showForm && (
        <div className="mb-6 p-4 bg-gray-800 rounded-lg border border-gray-700">
          <div className="grid grid-cols-2 gap-4">
            <input placeholder="Term name" value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})}
              className="px-3 py-2 bg-gray-900 border border-gray-600 rounded text-white" />
            <input placeholder="Domain" value={formData.domain} onChange={e => setFormData({...formData, domain: e.target.value})}
              className="px-3 py-2 bg-gray-900 border border-gray-600 rounded text-white" />
            <input placeholder="Owner" value={formData.owner} onChange={e => setFormData({...formData, owner: e.target.value})}
              className="px-3 py-2 bg-gray-900 border border-gray-600 rounded text-white" />
            <select value={formData.status} onChange={e => setFormData({...formData, status: e.target.value})}
              className="px-3 py-2 bg-gray-900 border border-gray-600 rounded text-white">
              <option value="draft">Draft</option>
              <option value="approved">Approved</option>
              <option value="deprecated">Deprecated</option>
            </select>
          </div>
          <textarea placeholder="Definition" value={formData.definition} onChange={e => setFormData({...formData, definition: e.target.value})}
            className="w-full mt-4 px-3 py-2 bg-gray-900 border border-gray-600 rounded text-white" rows={3} />
          <button onClick={handleSubmitTerm}
            className="mt-4 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700">Save Term</button>
        </div>
      )}

      {/* Terms List */}
      {activeTab === 'terms' && (
        <div className="space-y-3">
          {loading ? <p className="text-gray-400">Loading...</p> :
            terms.length === 0 ? <p className="text-gray-500">No terms found. Add your first business term above.</p> :
            terms.map(term => (
              <div key={term.term_id} className="p-4 bg-gray-800 rounded-lg border border-gray-700 hover:border-indigo-500 transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <h3 className="font-semibold text-white">{term.name}</h3>
                    {statusBadge(term.status)}
                    {term.domain && <span className="text-xs text-indigo-400">{term.domain}</span>}
                  </div>
                  {/* Deleting a term is an unrecoverable hard delete of the term
                      and all its asset links, so the backend admin-gates it.
                      Hide the control rather than let it 403 silently. */}
                  {isAdmin && (
                    <button onClick={() => handleDeleteTerm(term.term_id)}
                      title="Delete term (admin)"
                      className="text-red-400 hover:text-red-300 text-sm">×</button>
                  )}
                </div>
                <p className="mt-1 text-sm text-gray-400">{term.definition}</p>
                {term.owner && <p className="mt-1 text-xs text-gray-500">Owner: {term.owner}</p>}
              </div>
            ))
          }
        </div>
      )}

      {/* Domains List */}
      {activeTab === 'domains' && (
        <div className="grid grid-cols-2 gap-4">
          {domains.map(d => (
            <div key={d.domain_id} className="p-4 bg-gray-800 rounded-lg border-l-4" style={{borderLeftColor: d.color || '#6366f1'}}>
              <h3 className="font-semibold text-white">{d.name}</h3>
              <p className="text-sm text-gray-400 mt-1">{d.description}</p>
              {d.owner && <p className="text-xs text-gray-500 mt-2">Owner: {d.owner}</p>}
            </div>
          ))}
          {domains.length === 0 && <p className="text-gray-500 col-span-2">No domains defined yet.</p>}
        </div>
      )}

      {/* KPIs List */}
      {activeTab === 'kpis' && (
        <div className="space-y-3">
          {kpis.map(kpi => (
            <div key={kpi.kpi_id} className="p-4 bg-gray-800 rounded-lg border border-gray-700">
              <div className="flex items-center gap-3">
                <h3 className="font-semibold text-white">{kpi.name}</h3>
                {kpi.domain && <span className="text-xs text-indigo-400">{kpi.domain}</span>}
                {kpi.granularity && <span className="text-xs text-gray-500">{kpi.granularity}</span>}
              </div>
              <p className="mt-1 text-sm text-gray-400">{kpi.definition}</p>
              {kpi.formula_sql && (
                <pre className="mt-2 p-2 bg-gray-900 rounded text-xs text-green-400 overflow-x-auto">{kpi.formula_sql}</pre>
              )}
            </div>
          ))}
          {kpis.length === 0 && <p className="text-gray-500">No KPIs defined yet.</p>}
        </div>
      )}
    </div>
  );
}
