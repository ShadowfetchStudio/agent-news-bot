// Application wrapper around twitter-text 3.1.0. Inputs are data, never evaluated.
import parseTweet from 'twitter-text/dist/esm/parseTweet.js';
import extractUrls from 'twitter-text/dist/esm/extractUrlsWithIndices.js';
import {parse as extractEmoji} from 'twemoji-parser';

const clean = value => String(value || '').normalize('NFC').replace(/[\uFEFF\uFFFE\uFFFF]/g, '').trim();
const join = (...parts) => parts.filter(Boolean).join(' ');
export function measure(text) {
  const result = parseTweet(String(text));
  return {weightedLength: result.weightedLength, valid: result.valid};
}
function tokens(text) {
  const spans = new Map();
  for (const item of [...extractEmoji(text), ...extractUrls(text)]) spans.set(item.indices[0], item.indices[1]);
  const result = [];
  for (let i = 0; i < text.length;) {
    const end = spans.get(i) || i + (text.codePointAt(i) > 0xffff ? 2 : 1);
    result.push(text.slice(i, end)); i = end;
  }
  return result;
}
export function compose(title, url, tags) {
  title = clean(title); url = clean(url);
  // A URL is an indivisible entity. Do not turn a malformed/oversize URL into a broken download link.
  if (url && (extractUrls(url).length !== 1 || extractUrls(url)[0].url !== url)) url = '';
  let selected = [];
  for (const tag of tags.slice(0, 6).map(clean).filter(Boolean)) {
    if (measure(join('…', url, ...selected, tag)).weightedLength <= 280) selected.push(tag);
  }
  const suffix = join(url, ...selected);
  const chunks = tokens(title);
  const budget = 280 - (suffix ? 1 + measure(suffix).weightedLength : 0);
  const weights = chunks.map(token => measure(token).weightedLength);
  let text = join(title, suffix);
  if (weights.reduce((sum, weight) => sum + weight, 0) > budget) {
    let prefix = '', count = 0;
    const ellipsis = measure('…').weightedLength;
    for (let i = 0; i < chunks.length; i++) {
      if (count + weights[i] + ellipsis > budget) break;
      prefix += chunks[i]; count += weights[i];
    }
    text = join(prefix.trimEnd() + '…', suffix);
  }
  return {text, ...measure(text)};
}
// String bridge keeps identical behavior in JavaScriptCore and QuickJS.
export function request(json) {
  const value = JSON.parse(json);
  return JSON.stringify(value.op === 'measure' ? measure(value.text) : compose(value.title, value.url, value.tags || []));
}
