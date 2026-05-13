import { HistoryView } from './History';

export default function Saved() {
  return (
    <HistoryView
      savedOnly={true}
      title="Saved"
      emptyMessage="No saved queries yet. Thumbs-up an answer to save it."
    />
  );
}
