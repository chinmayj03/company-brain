import { useState } from 'react';
import { CheckCircle } from 'lucide-react';

const TYPES = [
  { value: 'business_context', label: 'Business context', hint: 'Why was this built this way?' },
  { value: 'invariant',        label: 'Invariant',        hint: 'A rule that must always hold' },
  { value: 'risk_flag',        label: 'Risk flag',        hint: 'What to watch out for' },
  { value: 'deprecation_note', label: 'Deprecation',      hint: 'Being phased out' },
];

export default function AnnotationEditor({ commitMessage, onSave, onCancel }) {
  const [type, setText]  = useState('business_context');
  const [text, setBody]  = useState('');
  const [fields, setFlds] = useState('');

  const handleSave = () => {
    if (!text.trim()) return;
    onSave({
      type,
      text: text.trim(),
      fields: fields.split(',').map(f => f.trim()).filter(Boolean),
    });
  };

  return (
    <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
      {commitMessage && (
        <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2 border border-slate-100">
          Annotating: <span className="italic">{commitMessage.slice(0, 80)}</span>
        </p>
      )}

      {/* Type pills */}
      <div className="flex flex-wrap gap-1.5">
        {TYPES.map(t => (
          <button
            key={t.value}
            onClick={() => setText(t.value)}
            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
              type === t.value
                ? 'bg-brand-600 text-white border-brand-600'
                : 'border-slate-200 text-slate-600 hover:border-brand-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <p className="text-xs text-slate-400">{TYPES.find(t => t.value === type)?.hint}</p>

      {/* Text area */}
      <textarea
        value={text}
        onChange={e => setBody(e.target.value)}
        placeholder="Describe the business context, invariant, or risk in plain English…"
        rows={3}
        className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2.5 resize-none
                   focus:outline-none focus:ring-2 focus:ring-brand-500 leading-relaxed"
      />

      {/* Optional field list */}
      <input
        value={fields}
        onChange={e => setFlds(e.target.value)}
        placeholder="Applies to fields (optional): charge.amount, transactions.user_id"
        className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2
                   focus:outline-none focus:ring-2 focus:ring-brand-500"
      />

      <div className="flex gap-2 justify-end">
        <button
          onClick={onCancel}
          className="text-xs px-3 py-1.5 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={!text.trim()}
          className="text-xs px-3 py-1.5 bg-brand-600 text-white rounded-lg hover:bg-brand-700
                     disabled:opacity-40 flex items-center gap-1.5"
        >
          <CheckCircle size={12} /> Save annotation
        </button>
      </div>
    </div>
  );
}
