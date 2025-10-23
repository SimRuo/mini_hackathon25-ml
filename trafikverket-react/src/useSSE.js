import { useEffect, useRef } from "react";

export default function useSSE(sseUrl, onMessage) {
    const esRef = useRef(null);

    useEffect(() => {
        if (!sseUrl) return;
        const es = new EventSource(sseUrl);
        esRef.current = es;

        es.onmessage = (e) => onMessage(e.data);
        es.onerror = () => {
            es.close();
        };

        return () => es.close();
    }, [sseUrl, onMessage]);
}
