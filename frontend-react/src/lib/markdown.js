// Safe Markdown for AI answers: everything is HTML-escaped first, then a small subset is
// turned into tags (headings, lists, tables, code, bold/italic). Knowledge citations such as
// [K3] become buttons with class "cite" and data-ref="K3".

export const esc = s =>
  String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function inline(s) {
  return s
    .replace(/&lt;br\s*\/?&gt;/gi, "<br>") // models put <br> in table cells; the rest stays escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[(K\d+)\]/g, '<button type="button" class="cite" data-ref="$1" aria-label="Source $1">$1</button>');
}

const LIST_RE = /^\s*([-*•]|\d+[.)])\s+/;
const indentOf = l => l.match(/^\s*/)[0].replace(/\t/g, "    ").length;

// A list starting at lines[i] with the given indent; deeper-indented list lines become nested
// lists; a blank line followed by a sibling item continues it.
function parseList(lines, i, indent) {
  const first = lines[i];
  const ordered = /^\s*\d+[.)]\s+/.test(first);
  const start = ordered ? parseInt(first.trim(), 10) : 1;
  const items = [];
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (j < lines.length && LIST_RE.test(lines[j]) && indentOf(lines[j]) >= indent) {
        i = j;
        continue;
      }
      break;
    }
    const ind = indentOf(line);
    if (LIST_RE.test(line) && ind === indent) {
      items.push({ text: line.replace(LIST_RE, ""), children: "" });
      i++;
      continue;
    }
    if (ind > indent && items.length) {
      if (LIST_RE.test(line)) {
        const r = parseList(lines, i, ind);
        items[items.length - 1].children += r.html;
        i = r.next;
        continue;
      }
      items[items.length - 1].text += " " + line.trim();
      i++;
      continue;
    }
    break;
  }
  const lis = items.map(it => `<li>${inline(it.text)}${it.children}</li>`).join("");
  const html = ordered ? `<ol${start > 1 ? ` start="${start}"` : ""}>${lis}</ol>` : `<ul>${lis}</ul>`;
  return { html, next: i };
}

/** Markdown text -> safe HTML string (use with dangerouslySetInnerHTML). */
export function markdown(src) {
  const lines = esc(src).replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${buf.join("\n")}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
      i++;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(
        `<div class="tbl"><table><thead><tr>${head.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows
          .map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`)
          .join("")}</tbody></table></div>`
      );
      continue;
    }
    if (LIST_RE.test(line)) {
      const r = parseList(lines, i, indentOf(line));
      out.push(r.html);
      i = r.next;
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const para = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*•]|\d+[.)])\s|\s*\|)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("");
}
