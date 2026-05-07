import { useState, useRef, useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { queryGraph } from '../api/client';
import { Send, Zap, AlertTriangle } from 'lucide-react';

const DEV_WORKSPACE = '00000000-0000-0000-0000-000000000001';

const EXAMPLES = [
  'What breaks if I rename the amount_cents column?',
  'Who owns the payments service?',
  'Which frontend components call the charge endpoint?',
  'What are the invariants I must not violate in this API?',
];

export default function QueryPage() {
  const [question, setQuestion] = useState('');
  const [messages, setMessages]  = useState([]);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const askMutation = useMutation({
    mutationFn: (q) =>
      queryGraph({ question: q, workspace_id: DEV_WORKSPACE, max_hops: 3 }),
    onSuccess: (data, q) => {
      setMessages(prev => [
        ...prev,
        { role: 'user', content: q },
        { role: 'assistant', content: data.answer, sources: data.sources, affected: data.affected_nodes, confidence: data.confidence },
      ]);
    },
    onError: (err, q) => {
      setMessages(prev => [
        ...prev,
        { role: 'user', content: q },
        { role: 'error', content: err.message || 'Query failed' },
      ]);
    },
  });

  const send = (q = question) => {
    if (!q.trim() || askMutation.isPending) return;
    setQuestion('');
    askMutation.mutate(q.trim());
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-8 py-5 border-b border-slate-200 bg-white">
        <h1 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
          <Zap size={18} className="text-brand-500" /> Ask Company Brain
        </h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Ask anything about your codebase. Answers are grounded in the dependency graph.
        </p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-8 py-6 flex flex-col gap-4">
        {messages.length === 0 && (
          <div className="mt-8">
            <p className="text-sm text-slate-400 mb-4">Try asking:</p>
            <div className="grid grid-cols-2 gap-3">
              {EXAMPLES.map(ex => (
                <button
                  key={ex}
                  onClick={() => send(ex)}
                  className="text-left text-sm px-4 py-3 border border-slate-200 rounded-xl
                             hover:border-brand-300 hover:bg-brand-50 transition-colors text-slate-600"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <Message key={i} msg={msg} />
        ))}

        {askMutation.isPending && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
              <Zap size={14} className="text-brand-600 animate-pulse" />
            </div>
            <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-slate-500">
              <span>Traversing dependency graph + synthesising answer…</span>
              <span className="block text-xs text-slate-400 mt-1">Local LLM can take 30–90s — hang tight</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-8 py-4 border-t border-slate-200 bg-white">
        <div className="flex gap-3 items-end">
          <textarea
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="Ask a question about your codebase…"
            rows={2}
            className="flex-1 resize-none border border-slate-200 rounded-xl px-4 py-3 text-sm
                       focus:outline-none focus:ring-2 focus:ring-brand-500 leading-relaxed"
          />
          <button
            onClick={() => send()}
            disabled={!question.trim() || askMutation.isPending}
            className="p-3 bg-brand-600 text-white rounded-xl hover:bg-brand-700
                       disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

function Message({ msg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-lg bg-brand-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm">
          {msg.content}
        </div>
      </div>
    );
  }

  if (msg.role === 'error') {
    return (
      <div className="flex gap-3">
        <div className="w-8 h-8 rounded-full bg-red-100 flex items-center justify-center flex-shrink-0">
          <AlertTriangle size={14} className="text-red-500" />
        </div>
        <div className="bg-red-50 border border-red-200 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-red-700">
          {msg.content}
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div className="flex gap-3">
      <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Zap size={14} className="text-brand-600" />
      </div>
      <div className="flex-1 max-w-2xl">
        <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">
          {msg.content}
        </div>

        {/* Affected nodes */}
        {msg.affected?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {msg.affected.slice(0, 6).map(n => (
              <span
                key={n.id}
                className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200"
              >
                {n.name}
                <span className="ml-1 text-slate-400">+{n.depth}</span>
              </span>
            ))}
            {msg.affected.length > 6 && (
              <span className="text-xs text-slate-400">+{msg.affected.length - 6} more</span>
            )}
          </div>
        )}

        {/* Confidence */}
        <div className="mt-1.5 text-xs text-slate-400">
          Confidence: <span className="font-medium">{msg.confidence}</span>
          {msg.sources?.length > 0 && ` · ${msg.sources.length} sources`}
        </div>
      </div>
    </div>
  );
}
