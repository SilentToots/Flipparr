// Every declaration in a stylesheet, with the at-rules it sits inside. Not a
// full CSS parser -- it only has to understand this project's two files --
// but it keeps line numbers so a failure says where to look.
export function declarations(css) {
  const out = [];
  const stack = [];
  let line = 1;
  let buffer = "";
  let bufferLine = 1;
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i];
    if (ch === "/" && css[i + 1] === "*") {
      const end = css.indexOf("*/", i + 2);
      const stop = end === -1 ? css.length : end + 2;
      for (let j = i; j < stop; j += 1) if (css[j] === "\n") line += 1;
      i = stop - 1;
      continue;
    }
    if (ch === "\n") line += 1;
    if (ch === "{") {
      stack.push(buffer.trim());
      buffer = "";
      bufferLine = line;
      continue;
    }
    if (ch === ";" || ch === "}") {
      const text = buffer.trim();
      const colon = text.indexOf(":");
      if (colon > 0 && stack.length) {
        out.push({
          property: text.slice(0, colon).trim(),
          value: text.slice(colon + 1).trim(),
          line: bufferLine,
          selector: stack[stack.length - 1],
          media: stack.filter((s) => s.startsWith("@media")),
        });
      }
      buffer = "";
      bufferLine = line;
      if (ch === "}") stack.pop();
      continue;
    }
    if (!buffer.trim()) bufferLine = line;
    buffer += ch;
  }
  return out;
}
