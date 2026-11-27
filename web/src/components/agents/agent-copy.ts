const NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];

/** The intro sentence counts the catalogue so it cannot drift from the cards below it. */
export function specialistSummary(specialists: number): string {
  const count = NUMBER_WORDS[specialists] ?? String(specialists);
  const noun = specialists === 1 ? "specialist agent handles" : "specialist agents handle";
  return `${count} ${noun} the shopping experience, each with its own tools. The orchestrator routes every request to the right one over the A2A protocol.`;
}

export function toolCount(n: number): string {
  return `${n} ${n === 1 ? "tool" : "tools"}`;
}
