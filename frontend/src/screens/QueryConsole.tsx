import { FormEvent, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { Send } from "lucide-react";
import { brainClient } from "../api/brain_client";
import type { Citation, PersonaId, QueryResponse } from "../api/types";
import PersonaSelector from "../components/PersonaSelector";
import CitationsPanel from "../components/CitationsPanel";

type ChatTurn = {
  question: string;
  persona: PersonaId;
  response?: QueryResponse;
};

export default function QueryConsole() {
  const [persona, setPersona] = useState<PersonaId>("developer");
  const [question, setQuestion] = useState("what does the Aetna integration do");
  const [turns, setTurns] = useState<ChatTurn[]>([]);

  const mutation = useMutation({
    mutationFn: () => brainClient.query(question, persona),
    onSuccess: (response) => {
      setTurns((current) => [...current, { question, persona, response }]);
      setQuestion("");
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || mutation.isPending) return;
    mutation.mutate();
  }

  const latest = turns[turns.length - 1]?.response;
  const latestCitations = useMemo(() => collectCitations(latest), [latest]);

  return (
    <section className="page query-page" data-testid="query-page">
      <header className="page-head query-head">
        <div>
          <p className="eyebrow">Persona-aware answers</p>
          <h1>Query</h1>
        </div>
        <PersonaSelector value={persona} onChange={setPersona} />
      </header>

      <div className="query-layout">
        <div className="chat-panel">
          <div className="turn-list">
            {!turns.length && (
              <div className="empty-state large">
                Ask against the extracted network-iq brain. Answers render citations that open in Brain Browser.
              </div>
            )}
            {turns.map((turn, index) => (
              <article className="turn" key={`${turn.question}-${index}`}>
                <div className="question-bubble">
                  <span>{turn.persona}</span>
                  <strong>{turn.question}</strong>
                </div>
                <div className="answer">
                  <ReactMarkdown>{turn.response?.summary_md || turn.response?.raw_markdown || turn.response?.summary || ""}</ReactMarkdown>
                  {turn.response?.confidence && (
                    <p className="confidence">
                      {turn.response.confidence.level} confidence: {turn.response.confidence.rationale}
                    </p>
                  )}
                </div>
              </article>
            ))}
            {mutation.isPending && <div className="panel">Thinking with the current brain context...</div>}
            {mutation.error && <div className="alert">Query failed: {(mutation.error as Error).message}</div>}
          </div>
          <form className="askbar" onSubmit={submit}>
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask about a feature, entity, or change risk"
            />
            <button type="submit" disabled={mutation.isPending || !question.trim()}>
              <Send size={16} />
              Ask
            </button>
          </form>
        </div>

        <aside className="citations-panel">
          <h2>Citations</h2>
          <CitationsPanel citations={latestCitations} />
        </aside>
      </div>
    </section>
  );
}

function collectCitations(response?: QueryResponse): Citation[] {
  if (!response) return [];
  const byUrn = new Map<string, Citation>();
  (response.affected_entities || []).forEach((citation) => {
    byUrn.set(citation.urn, citation);
  });
  (response.call_chain || []).forEach((step) => {
    if (!byUrn.has(step.urn)) {
      byUrn.set(step.urn, {
        urn: step.urn,
        name: step.name,
        why_relevant: step.one_liner,
        confidence: 0.9,
      });
    }
  });
  (response.cited_entity_urns || []).forEach((urn) => {
    if (!byUrn.has(urn)) {
      byUrn.set(urn, { urn, name: urn, why_relevant: "Cited by the response." });
    }
  });
  return [...byUrn.values()];
}
