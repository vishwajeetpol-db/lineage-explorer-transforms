import { useState, useEffect, useCallback } from 'react';
import { useLineageStore } from '../store/lineageStore';

interface Notification {
  notif_id: string;
  notif_type: string;
  severity: string;
  title: string;
  detail: string;
  table_fqn: string;
  column_name: string;
  detected_at: string;
  is_read: boolean;
}

export function NotificationsPanel() {
  // POST /api/notifications/scan is admin-gated on the backend, so a non-admin
  // pressing "Run Scan" got a 403. fetch() does not reject on 4xx, the 403 body
  // parsed as JSON just fine, and the catch never ran — so the user saw
  // "Scan complete: undefined" over an unchanged empty list and concluded the
  // schema-change / DQ-degradation / sensitive-flow detectors had found nothing.
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [filter, setFilter] = useState<string>('');
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  const fetchNotifications = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filter) params.set('notif_type', filter);
      const res = await fetch(`/api/notifications?${params}`);
      const data = await res.json();
      setNotifications(data.notifications || []);
    } catch (e) {
      console.error('Failed to fetch notifications:', e);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  const fetchUnreadCount = useCallback(async () => {
    try {
      const res = await fetch('/api/notifications/unread-count');
      const data = await res.json();
      setUnreadCount(data.count || 0);
    } catch (e) { /* non-fatal */ }
  }, []);

  useEffect(() => {
    fetchNotifications();
    fetchUnreadCount();
  }, [fetchNotifications, fetchUnreadCount]);

  const markAllRead = async () => {
    await fetch('/api/notifications/mark-read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: true }),
    });
    fetchNotifications();
    fetchUnreadCount();
  };

  const triggerScan = async () => {
    setScanning(true);
    setScanError(null);
    try {
      const res = await fetch('/api/notifications/scan', { method: 'POST' });
      // fetch() only rejects on network failure, so a 4xx/5xx has to be checked
      // explicitly — otherwise an error body is reported as a successful scan.
      if (!res.ok) {
        let detail = `Scan failed (HTTP ${res.status})`;
        try {
          const body = await res.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* non-JSON error body — keep the status-code message */
        }
        setScanError(detail);
        return;
      }
      const data = await res.json();
      const detected = data?.detected;
      setScanError(null);
      alert(
        detected === undefined
          ? 'Scan complete.'
          : `Scan complete: ${JSON.stringify(detected)}`
      );
      fetchNotifications();
      fetchUnreadCount();
    } catch (e) {
      console.error('Scan failed:', e);
      setScanError('Scan failed — could not reach the server.');
    } finally {
      setScanning(false);
    }
  };

  const severityIcon = (severity: string) => {
    switch (severity) {
      case 'critical': return '🔴';
      case 'warning': return '🟡';
      default: return '🔵';
    }
  };

  const typeLabel = (type: string) => {
    switch (type) {
      case 'schema_change': return 'Schema Change';
      case 'dq_degradation': return 'DQ Issue';
      case 'sensitive_flow': return 'Sensitive Data';
      default: return type;
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-bold text-white">Notifications</h2>
          {unreadCount > 0 && (
            <span className="px-2 py-0.5 bg-red-600 text-white text-xs font-bold rounded-full">
              {unreadCount}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={markAllRead}
            className="px-3 py-1.5 text-sm bg-gray-700 text-gray-300 rounded hover:bg-gray-600">
            Mark all read
          </button>
          {/* Scanning is admin-gated on the backend — don't offer a control that
              can only 403. */}
          {isAdmin && (
            <button onClick={triggerScan} disabled={scanning}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50">
              {scanning ? 'Scanning...' : 'Run Scan'}
            </button>
          )}
        </div>
      </div>

      {scanError && (
        <div className="mb-4 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {scanError}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-2 mb-4">
        {['', 'schema_change', 'dq_degradation', 'sensitive_flow'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              filter === f ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
            }`}>
            {f ? typeLabel(f) : 'All'}
          </button>
        ))}
      </div>

      {/* Notifications List */}
      <div className="space-y-2">
        {loading ? <p className="text-gray-400">Loading...</p> :
          notifications.length === 0 ? <p className="text-gray-500">No notifications. Run a scan to detect issues.</p> :
          notifications.map(n => (
            <div key={n.notif_id}
              className={`p-4 rounded-lg border transition-colors ${
                n.is_read ? 'bg-gray-800/50 border-gray-800' : 'bg-gray-800 border-gray-700'
              }`}>
              <div className="flex items-start justify-between">
                <div className="flex items-start gap-2">
                  <span className="text-lg">{severityIcon(n.severity)}</span>
                  <div>
                    <p className={`font-medium ${n.is_read ? 'text-gray-400' : 'text-white'}`}>{n.title}</p>
                    <p className="text-sm text-gray-500 mt-0.5">{n.detail}</p>
                    <div className="flex gap-3 mt-2 text-xs text-gray-600">
                      <span>{typeLabel(n.notif_type)}</span>
                      {n.table_fqn && <span className="text-indigo-400">{n.table_fqn}</span>}
                      <span>{new Date(n.detected_at).toLocaleString()}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))
        }
      </div>
    </div>
  );
}
