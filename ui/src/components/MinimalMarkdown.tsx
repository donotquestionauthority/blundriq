/** `**bold**` and blank-line paragraphs: all the markup the prompts ask a model for. */
export function MinimalMarkdown({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {text.split(/\n{2,}/g).map((block, bi) => (
        <p key={bi} className="whitespace-pre-line">
          {block.split(/(\*{2,3}[^*]+\*{2,3})/g).map((part, i) => {
            const bold = part.match(/^\*{2,3}([^*]+)\*{2,3}$/);
            return bold ? <strong key={i}>{bold[1].trim()}</strong> : <span key={i}>{part}</span>;
          })}
        </p>
      ))}
    </div>
  );
}
