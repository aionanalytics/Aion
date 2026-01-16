/**
 * useSSE - React hook for Server-Sent Events
 * 
 * Manages SSE connection lifecycle and provides real-time data updates.
 */

import { useEffect, useRef, useState } from "react";

export interface UseSSEOptions<T> {
  url: string;
  enabled?: boolean;
  onData?: (data: T) => void;
  onError?: (error: Event) => void;
}

export function useSSE<T = any>({
  url,
  enabled = true,
  onData,
  onError,
}: UseSSEOptions<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled) {
      // Close existing connection if disabled
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
        setIsConnected(false);
      }
      return;
    }

    // Create EventSource connection
    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      setIsConnected(true);
      setError(null);
    };

    eventSource.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as T;
        setData(parsed);
        onData?.(parsed);
      } catch (err) {
        console.error("Failed to parse SSE data:", err);
        setError("Failed to parse server data");
      }
    };

    eventSource.onerror = (event) => {
      setIsConnected(false);
      setError("Connection error");
      onError?.(event);
      
      // EventSource will automatically reconnect
      // but we'll close it if there's a persistent error
      if (eventSource.readyState === EventSource.CLOSED) {
        eventSource.close();
      }
    };

    // Cleanup on unmount
    return () => {
      eventSource.close();
      eventSourceRef.current = null;
      setIsConnected(false);
    };
  }, [url, enabled]);

  return {
    data,
    error,
    isConnected,
    close: () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
        setIsConnected(false);
      }
    },
  };
}
