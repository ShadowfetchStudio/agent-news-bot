import {build} from 'esbuild';
import {mkdir, copyFile, writeFile, readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const destination = '../../linux/agent_newsbot/vendor';
await mkdir(destination, {recursive:true});
// Both shipping engines implement ES2018; omit obsolete core-js v2 polyfills only.
await build({entryPoints:['entry.js'], bundle:true, format:'iife', globalName:'NewsBotXText', target:'es2018', minify:true,
  outfile: destination + '/twitter-text.js', legalComments:'inline', metafile:true,
  plugins:[{name:'native-es2018', setup(build) { build.onLoad({filter:/\.js$/, namespace:'file'}, async args => {
    if (!args.path.includes('/twitter-text/dist/')) return;
    let contents = (await readFile(args.path,'utf8')).replace(/^import ["']core-js\/[^"']+["'];\s*/gm,'');
    if (args.path.endsWith('/extractUrlsWithIndices.js')) {
      // QuickJS omits non-standard RegExp.$n globals. Capture the SAME matches explicitly.
      contents = contents.replace('var _loop = function _loop() {', 'var match; var _loop = function _loop() {')
        .replace(/RegExp\.\$([23457])/g, 'match[$1]')
        .replace('if (url.match(validTcoUrl)) {', 'var tcoMatch = url.match(validTcoUrl); if (tcoMatch) {')
        .replace('RegExp.$1', 'tcoMatch[1]').replace('RegExp.lastMatch', 'tcoMatch[0]')
        .replace('while (extractUrl.exec(text))', 'while ((match = extractUrl.exec(text)))');
      if (/RegExp\.(\$|lastMatch)/.test(contents)) throw Error('Unadapted RegExp static');
    }
    return {contents, loader:'js'};
  }); }}]});
for (const [pkg, file] of [['twitter-text','LICENSE'], ['twemoji-parser','LICENSE.md'], ['punycode','LICENSE-MIT.txt']]) {
  await copyFile('node_modules/'+pkg+'/'+file, destination+'/'+pkg+'-LICENSE.txt');
}
const body = await readFile(destination+'/twitter-text.js');
await writeFile(destination+'/SHA256SUMS', createHash('sha256').update(body).digest('hex')+'  twitter-text.js\n');
