import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';
import type { Components } from 'react-markdown';

interface Props {
  content: string;
  /** When true, renders inline (no block padding). Used inside citation snippets. */
  compact?: boolean;
}

const components: Components = {
  // Headings — scale down to fit answer card context
  h1: ({ children }) => <h3 style={{ fontSize: 15, fontWeight: 600, margin: '16px 0 6px', color: 'var(--text-primary)' }}>{children}</h3>,
  h2: ({ children }) => <h4 style={{ fontSize: 14, fontWeight: 600, margin: '14px 0 5px', color: 'var(--text-primary)' }}>{children}</h4>,
  h3: ({ children }) => <h5 style={{ fontSize: 13, fontWeight: 600, margin: '12px 0 4px', color: 'var(--text-primary)' }}>{children}</h5>,

  // Paragraphs
  p: ({ children }) => <p style={{ margin: '0 0 10px', fontSize: 14, lineHeight: 1.6, color: 'var(--text-secondary)' }}>{children}</p>,

  // Inline code
  code: ({ children, className, ...props }) => {
    const isBlock = !!className; // rehype-highlight adds language-* class
    if (isBlock) {
      return (
        <code className={className} style={{
          display: 'block', padding: '12px 14px', borderRadius: 6, fontSize: 12,
          fontFamily: 'var(--font-mono)', lineHeight: 1.5, overflowX: 'auto',
          background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
        }} {...props}>{children}</code>
      );
    }
    return (
      <code style={{
        fontFamily: 'var(--font-mono)', fontSize: '0.88em',
        background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
        borderRadius: 3, padding: '1px 5px', color: 'var(--text-primary)',
      }}>{children}</code>
    );
  },

  // Pre wrapper — remove default browser margin
  pre: ({ children }) => (
    <pre style={{ margin: '8px 0 12px', borderRadius: 6, overflow: 'hidden', background: 'transparent' }}>
      {children}
    </pre>
  ),

  // Links — open in new tab
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'underline' }}>
      {children}
    </a>
  ),

  // Blockquote — used for caveats in markdown answers
  blockquote: ({ children }) => (
    <blockquote style={{
      margin: '10px 0', padding: '10px 14px',
      borderLeft: '3px solid var(--accent-primary)',
      background: 'var(--accent-soft)', borderRadius: '0 6px 6px 0',
      color: 'var(--text-secondary)', fontSize: 13,
    }}>{children}</blockquote>
  ),

  // Tables (remark-gfm)
  table: ({ children }) => (
    <div style={{ overflowX: 'auto', margin: '10px 0' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 600, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)', borderBottom: '1px solid var(--border-default)' }}>{children}</th>
  ),
  td: ({ children }) => (
    <td style={{ padding: '6px 10px', fontSize: 13, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-default)' }}>{children}</td>
  ),

  // Lists
  ul: ({ children }) => <ul style={{ paddingLeft: 18, margin: '6px 0 10px', color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.6 }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ paddingLeft: 18, margin: '6px 0 10px', color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.6 }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 4 }}>{children}</li>,

  // Strong / em
  strong: ({ children }) => <strong style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{children}</strong>,
};

export default function AnswerMarkdown({ content, compact }: Props) {
  return (
    <div style={{ fontSize: 14, lineHeight: 1.6, ...(compact ? {} : {}) }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
