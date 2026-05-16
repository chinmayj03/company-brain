import type { PersonaId } from "../api/types";

type Persona = {
  id: PersonaId | "customer_success" | "cfo" | "ceo";
  label: string;
  disabled?: boolean;
};

const personas: Persona[] = [
  { id: "developer", label: "Developer" },
  { id: "pm", label: "PM" },
  { id: "vp_eng", label: "VP Eng" },
  { id: "customer_success", label: "CS", disabled: true },
  { id: "cfo", label: "CFO", disabled: true },
  { id: "ceo", label: "CEO", disabled: true },
];

export default function PersonaSelector({
  value,
  onChange,
}: {
  value: PersonaId;
  onChange: (value: PersonaId) => void;
}) {
  return (
    <label className="field compact">
      <span>Persona</span>
      <select value={value} onChange={(event) => onChange(event.target.value as PersonaId)}>
        {personas.map((persona) => (
          <option key={persona.id} value={persona.id} disabled={persona.disabled}>
            {persona.label}{persona.disabled ? " - coming soon" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
