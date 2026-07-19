import { useState, useEffect, useCallback } from 'react';

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
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [filter, setFilter] = useState<string>('');
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(false);

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
    try {
      const res = await fetch('/api/notifications/scan', { method: 'POST' });
      const data = await res.json();
      alert(`Scan complete: ${JSON.stringify(data.detected)}`);
      fetchNotifications();
      fetchUnreadCount();
    } catch (e) {
      console.error('Scan failed:', e);
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
          <button onClick={triggerScan} disabled={scanning}
            className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50">
            {scanning ? 'Scanning...' : 'Run Scan'}
          </button>
        </div>
      </div>

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
