const vscode = require('vscode');

let auto = true;
let squashed = false;

function cfg() { return vscode.workspace.getConfiguration(); }

function gridRange(editor) {
  const n = vscode.workspace.getConfiguration('pixelGrid').get('size', 16);
  const doc = editor.document;
  if (doc.languageId !== 'json' && doc.languageId !== 'jsonc') return null;
  const re = new RegExp('^\\s*"[^"]{' + n + '}",?\\s*$');
  const line = editor.selection.active.line;
  if (!re.test(doc.lineAt(line).text)) return null;
  let a = line, b = line;
  while (a > 0 && re.test(doc.lineAt(a - 1).text)) a--;
  while (b < doc.lineCount - 1 && re.test(doc.lineAt(b + 1).text)) b++;
  return b - a + 1 >= n ? { a, b } : null;
}

// Virtual (decoration-only) coordinate labels: column header on the line above
// the grid, row numbers in the left gutter. Nothing here touches the document.
const labelStyle = { color: new vscode.ThemeColor('editorLineNumber.foreground') };
const headerDeco = vscode.window.createTextEditorDecorationType({});
const rowDeco = vscode.window.createTextEditorDecorationType({});

function px(n) { return n.toFixed(2) + 'px'; }

function label(text, leftPx) {
  return {
    ...labelStyle,
    contentText: text,
    textDecoration: 'none; position:absolute; left:' + px(leftPx) + '; pointer-events:none; opacity:0.75;',
  };
}

// --- Output-color preview -----------------------------------------------
// Ports mintech/mintech_textures.py's AutoShader ("standard" shader spec,
// textures.json) plus a few palettes.json entries so each grid cell can be
// boxed in the color the real generator would paint there. Legend
// (textures.json): '.'/' ' = clear, '#$%' = auto-shaded, '0'-'4' = explicit
// tone index. Keep these constants in sync if those files change.
//
// Character -> class: across every tool template (hammer, wrench, screwdriver,
// saw, file, pliers, mortar_pestle), '%' is the wood handle and '$' is the
// stone part (mortar_bowl), each drawn as its own layer (parts.json layers'
// keep/drop) in its own palette. Everything else ('#' and pinned tones) is
// the material itself — previewed here as iron. A layer's shape (and thus its
// auto-shading) only sees its own class's cells, matching keep/drop in reality.
const CLEAR_CHARS = new Set(['.', ' ']);
const CLASSES = {
  wood: { chars: new Set(['%']), tones: ['#281E0B', '#493615', '#684E1E', '#896727', '#A98A4A'] },
  stone: { chars: new Set(['$']), tones: ['#3B3B3B', '#5A5A5A', '#747474', '#8F8F8F', '#B4B4B4'] },
  iron: { chars: null, tones: ['#36393E', '#6D757F', '#9CA3AF', '#D1D5DB', '#FFFFFF'] }, // null = "everything else"
};
function classOf(ch) {
  if (CLASSES.wood.chars.has(ch)) return 'wood';
  if (CLASSES.stone.chars.has(ch)) return 'stone';
  return 'iron';
}
const STANDARD = {
  light: [-1, -1],
  tones: { thin_lit: 3, thin_shade: 1, outline: 0, rim_shade: 1, rim_lit: 2,
           highlight: 4, lit: 3, shade: 1, ambient_high: 3, ambient_mid: 2, ambient_low: 1 },
  thresholds: { thin_dot: 0.0, thin_ambient: 0.55, rim_dot: -0.3, rim_ambient: 0.6,
                highlight_dot: 0.4, highlight_ambient: 0.35, ambient_high: 0.65, ambient_mid: 0.35 },
};
const sign = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
const side = (s, neg, pos) => (s < 0 ? neg : s > 0 ? pos : false);

// Faithful port of AutoShader.auto_tone for context="self": `solid` decides
// which neighbours count, so passing a per-class mask reproduces a layer's
// keep/drop restriction (e.g. only '%' cells shape the wood handle's normals).
function autoTone(y, x, solid, n) {
  const { light: [lx, ly], tones: role, thresholds: limit } = STANDARD;
  const sx = sign(lx), sy = sign(ly);
  const empty = (dy, dx) => !solid(y + dy, x + dx);
  const top = empty(-1, 0), bottom = empty(1, 0), left = empty(0, -1), right = empty(0, 1);
  const tl = empty(-1, -1), tr = empty(-1, 1), bl = empty(1, -1), br = empty(1, 1);
  const ny = (bottom - top) + 0.5 * (bl + br - tl - tr);
  const nx = (right - left) + 0.5 * (tr + br - tl - bl);
  const light = nx * lx + ny * ly;
  const project = (yy, xx) => -(lx * xx + ly * yy);
  const corners = [project(0, 0), project(0, n - 1), project(n - 1, 0), project(n - 1, n - 1)];
  const low = Math.min(...corners), high = Math.max(...corners);
  const ambient = high === low ? 0.5 : 1 - (project(y, x) - low) / (high - low);
  const towardEdge = side(sy, top, bottom) || side(sx, left, right);
  const awayEdge = side(-sy, top, bottom) || side(-sx, left, right);
  const litCorner = !!(sx || sy) && empty(sy, sx);
  const darkCorner = !!(sx || sy) && empty(-sy, -sx);

  if ((top && bottom) || (left && right) || (tl && br) || (tr && bl)) {
    return (light > limit.thin_dot || ambient > limit.thin_ambient) ? role.thin_lit : role.thin_shade;
  }
  if (top || bottom || left || right) {
    if (light < limit.rim_dot || awayEdge) return role.outline;
    return ambient < limit.rim_ambient ? role.rim_shade : role.rim_lit;
  }
  if (litCorner || towardEdge) {
    return (light > limit.highlight_dot && ambient > limit.highlight_ambient) ? role.highlight : role.lit;
  }
  if (darkCorner || awayEdge) return role.shade;
  return ambient > limit.ambient_high ? role.ambient_high : (ambient > limit.ambient_mid ? role.ambient_mid : role.ambient_low);
}

// One decoration type per (class, tone) pair, e.g. swatchDeco.wood[3].
const swatchDeco = Object.fromEntries(Object.entries(CLASSES).map(([name, { tones }]) => [
  name,
  tones.map((hex) => vscode.window.createTextEditorDecorationType({ backgroundColor: hex, opacity: '0.85', isWholeLine: false })),
]));

function drawSwatches(editor, r, q, n) {
  const doc = editor.document;
  const grid = [];
  for (let i = 0; i < n && r.a + i <= r.b; i++) grid.push(doc.lineAt(r.a + i).text.substr(q + 1, n));
  // A layer's shape only sees its own class's cells (mirrors keep/drop).
  const solidFor = (cls) => (y, x) =>
    y >= 0 && y < grid.length && x >= 0 && x < n && !CLEAR_CHARS.has(grid[y][x]) && classOf(grid[y][x]) === cls;
  const byClassTone = Object.fromEntries(Object.entries(CLASSES).map(([name, { tones }]) => [name, tones.map(() => [])]));
  for (let y = 0; y < grid.length; y++) {
    for (let x = 0; x < n; x++) {
      const ch = grid[y][x];
      if (CLEAR_CHARS.has(ch)) continue;
      const cls = classOf(ch);
      const tone = /[0-4]/.test(ch) ? Number(ch) : autoTone(y, x, solidFor(cls), n);
      const line = r.a + y, col = q + 1 + x;
      byClassTone[cls][tone].push(new vscode.Range(line, col, line, col + 1));
    }
  }
  for (const cls of Object.keys(CLASSES)) {
    swatchDeco[cls].forEach((deco, tone) => editor.setDecorations(deco, byClassTone[cls][tone]));
  }
}

function clearSwatches(editor) {
  for (const cls of Object.keys(CLASSES)) swatchDeco[cls].forEach((deco) => editor.setDecorations(deco, []));
}

function clearLabels(editor) {
  editor.setDecorations(headerDeco, []);
  editor.setDecorations(rowDeco, []);
  clearSwatches(editor);
}

function drawLabels(editor, r) {
  const pg = vscode.workspace.getConfiguration('pixelGrid');
  const n = pg.get('size', 16);
  const cellPx = pg.get('cellRatio', 1.0) * cfg().get('editor.fontSize', 12);
  const doc = editor.document;
  const q = doc.lineAt(r.a).text.indexOf('"'); // pixels start at column q + 1
  const header = Array.from({ length: n }, (_, i) => (i + 1) % 10).join('');
  // Insert the header as a "ghost" line break via a display:block pseudo-element.
  // It must NOT land on the grid's own first row: that row's line height is
  // squashed to exactly one square cell (see apply()), so a second block box
  // squeezed into it just overlaps the row instead of pushing it down. Anchoring
  // it as an `after` on the line above — whose height is unconstrained — gives
  // it room to become its own line without competing with the grid at all.
  const hostLine = r.a > 0 ? r.a - 1 : r.a;
  const pseudo = r.a > 0 ? 'after' : 'before';
  const hostPos = r.a > 0
    ? new vscode.Position(hostLine, doc.lineAt(hostLine).text.length)
    : new vscode.Position(hostLine, 0);
  const headerOpts = {
    [pseudo]: {
      contentText: header,
      color: labelStyle.color,
      textDecoration: 'none; display:block; padding-left:' + px((q + 1) * cellPx) + '; pointer-events:none; opacity:0.75;',
    },
  };
  editor.setDecorations(headerDeco, [{ range: new vscode.Range(hostPos, hostPos), renderOptions: headerOpts }]);

  const rows = [];
  for (let i = 0; i < n && r.a + i <= r.b; i++) {
    const p = new vscode.Position(r.a + i, 0);
    const num = String(i + 1).padStart(2, ' ').replace(' ', '\u00a0');
    rows.push({ range: new vscode.Range(p, p), renderOptions: { before: label(num, Math.max(0, q - 2) * cellPx) } });
  }
  editor.setDecorations(rowDeco, rows);

  drawSwatches(editor, r, q, n);
}

async function apply(ctx) {
  const editor = vscode.window.activeTextEditor;
  const range = auto && editor && vscode.window.state.focused ? gridRange(editor) : null;
  const want = !!range;
  for (const e of vscode.window.visibleTextEditors) if (e !== editor) clearLabels(e);
  if (editor) {
    if (want) drawLabels(editor, range); else clearLabels(editor);
  }
  if (want === squashed) return;
  squashed = want;
  const c = cfg();
  if (want) {
    const pg = vscode.workspace.getConfiguration('pixelGrid');
    const cell = pg.get('cellRatio', 1.0);
    const charW = pg.get('charWidthRatio', 0.6);
    const fs = c.get('editor.fontSize', 12);
    if (ctx.globalState.get('origSaved') !== true) {
      await ctx.globalState.update('orig', {
        lh: c.inspect('editor.lineHeight').globalValue ?? null,
        ls: c.inspect('editor.letterSpacing').globalValue ?? null,
      });
      await ctx.globalState.update('origSaved', true);
    }
    await c.update('editor.lineHeight', cell, vscode.ConfigurationTarget.Global);
    await c.update('editor.letterSpacing', Math.max(0, (cell - charW) * fs), vscode.ConfigurationTarget.Global);
  } else {
    await restore(ctx);
  }
}

async function restore(ctx) {
  if (ctx.globalState.get('origSaved') !== true) return;
  const o = ctx.globalState.get('orig') || {};
  const c = cfg();
  await c.update('editor.lineHeight', o.lh ?? undefined, vscode.ConfigurationTarget.Global);
  await c.update('editor.letterSpacing', o.ls ?? undefined, vscode.ConfigurationTarget.Global);
  await ctx.globalState.update('origSaved', false);
}

// Unconditional escape hatch: clears the settings outright and drops any
// saved "orig" snapshot, for when squashed/origSaved drift from reality
// (e.g. a crash mid-update) and the normal restore() no-ops.
async function hardReset(ctx) {
  const c = cfg();
  await c.update('editor.lineHeight', undefined, vscode.ConfigurationTarget.Global);
  await c.update('editor.letterSpacing', undefined, vscode.ConfigurationTarget.Global);
  await ctx.globalState.update('origSaved', false);
  await ctx.globalState.update('orig', undefined);
  squashed = false;
  for (const e of vscode.window.visibleTextEditors) clearLabels(e);
  vscode.window.setStatusBarMessage('Pixel Grid: hard reset', 2000);
}

async function activate(ctx) {
  await restore(ctx); // recover from a crash while squashed
  const run = () => apply(ctx);
  ctx.subscriptions.push(
    headerDeco, rowDeco, ...Object.values(swatchDeco).flat(),
    vscode.window.onDidChangeTextEditorSelection(run),
    vscode.window.onDidChangeActiveTextEditor(run),
    vscode.workspace.onDidChangeTextDocument(run),
    vscode.window.onDidChangeWindowState(run),
    vscode.commands.registerCommand('pixelGrid.toggleAuto', async () => {
      auto = !auto;
      vscode.window.setStatusBarMessage('Pixel Grid auto spacing: ' + (auto ? 'on' : 'off'), 2000);
      await apply(ctx);
    }),
    vscode.commands.registerCommand('pixelGrid.hardReset', () => hardReset(ctx))
  );
  run();
}

async function deactivate() {}

module.exports = { activate, deactivate };
