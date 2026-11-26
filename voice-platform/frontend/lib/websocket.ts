'use client';

import { useEffect, useRef, useCallback } from 'react';
import { useDashboardStore } from './store';
import { getIdToken } from './useAuth';

function getWsUrl(): string {
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    const port = window.location.port;
    if ((host === 'localhost' || host === '127.0.0.1') && port !== '8000') {
      return `ws://${host}:8055/dashboard-stream`;
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/dashboard-stream`;
  }
  return process.env.NEXT_PUBLIC_BACKEND_WS_URL || 'ws://127.0.0.1:8055/dashboard-stream';
}

async function getAuthenticatedWsUrl(): Promise<string> {
  const token = await getIdToken();
  const base = getWsUrl();
  if (!token) return base;
  const separator = base.includes('?') ? '&' : '?';
  return `${base}${separator}token=${encodeURIComponent(token)}`;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();
  const heartbeatIntervalRef = useRef<NodeJS.Timeout>();

  const {
    setConnected,
    setConnectionStatus,
    setActiveCalls,
    upsertActiveCall,
    removeActiveCall,
    applyTicketUpdate,
    appendTranscript,
    triggerRefresh,
  } = useDashboardStore();

  const connect = useCallback(async () => {
    try {
      setConnectionStatus('connecting');
      const targetUrl = await getAuthenticatedWsUrl();
      // Never log targetUrl directly — it carries a live Firebase ID token in
      // its query string, and console output is captured by common error-
      // monitoring tools (Sentry breadcrumbs, LogRocket, etc.) and extensions.
      console.log('[WS] Connecting to:', targetUrl.split('?')[0]);
      wsRef.current = new WebSocket(targetUrl);

      wsRef.current.onopen = () => {
        console.log('[WS] Connected');
        setConnected(true);
        setConnectionStatus('connected');

        // Heartbeat every 30s
        heartbeatIntervalRef.current = setInterval(() => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
      };

      wsRef.current.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          console.log('[WS] Message:', data.type, data);
          handleMessage(data);
        } catch (error) {
          console.error('[WS] Parse error:', error);
        }
      };

      wsRef.current.onerror = (error) => {
        console.error('[WS] Error:', error);
        setConnectionStatus('error');
      };

      wsRef.current.onclose = () => {
        console.log('[WS] Disconnected');
        setConnected(false);
        setConnectionStatus('disconnected');
        clearInterval(heartbeatIntervalRef.current);

        // Reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(connect, 3000);
      };
    } catch (error) {
      console.error('[WS] Connection error:', error);
      setConnectionStatus('error');
      reconnectTimeoutRef.current = setTimeout(connect, 3000);
    }
  }, [setConnected, setConnectionStatus]);

  const handleMessage = (data: Record<string, unknown>) => {
    switch (data.type) {
      case 'initial_state':
        // Server sends current state on connect
        if (Array.isArray(data.active_calls)) {
          setActiveCalls(data.active_calls as never[]);
        }
        break;

      case 'call_started':
      case 'call_updated':
        if (data.call) {
          upsertActiveCall(data.call as never);
        }
        break;

      case 'active_calls_updated':
        // Periodic poll — update active calls list
        if (Array.isArray(data.active_calls)) {
          setActiveCalls(data.active_calls as never[]);
        }
        break;

      case 'call_ended':
      case 'call_completed':
      case 'call_failed':
        if (data.call && typeof (data.call as Record<string, unknown>).id === 'number') {
          removeActiveCall((data.call as Record<string, number>).id);
        }
        // Trigger REST refresh so completed calls table updates
        triggerRefresh();
        break;

      case 'ticket_created':
        if (data.ticket) {
          applyTicketUpdate(
            data.ticket as never,
            (data.call as never) || null
          );
        }
        triggerRefresh();
        break;

      case 'transcript':
        if (typeof data.call_id === 'number') {
          appendTranscript(data.call_id, {
            role: String(data.role || 'agent'), message: String(data.message || ''),
            sequence_number: typeof data.sequence_number === 'number' ? data.sequence_number : undefined,
            timestamp: typeof data.timestamp === 'string' ? data.timestamp : undefined,
          });
        }
        break;

      case 'agent_handoff':
      case 'order_updated':
        triggerRefresh();
        break;

      case 'appointment_created':
      case 'appointment_updated':
      case 'kyc_updated':
      case 'escalation_triggered':
      case 'human_handoff_started':
      case 'human_handoff_completed':
      case 'knowledge_updated':
      case 'knowledge_deleted':
        triggerRefresh();
        break;

      case 'pong':
        break;

      default:
        console.log('[WS] Unknown message type:', data.type);
    }
  };

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    clearTimeout(reconnectTimeoutRef.current);
    clearInterval(heartbeatIntervalRef.current);
  }, []);

  const sendMessage = useCallback((message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  return { sendMessage, disconnect };
}
