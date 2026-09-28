// priority: 1000
// kubejs/startup_scripts/mintech_items.js
//
// Registers every item described by <instance>/mintech/ and publishes the catalog code as
// global.MinTech so kubejs/server_scripts/mintech_recipes.js expands the exact same ids.
// mintech/mintech.json is the index; its "include" list names the rest (materials, parts,
// ores, families, ...) and they are deep-merged into one config here, exactly as the Python
// side does. Expansion rules mirror mintech/mintech_textures.py (see "$doc" in the JSON).
// Written in plain ES5 for Rhino: no arrows, let/const, template strings or ES6 built-ins.

global.MinTech = (function () {
  var CONFIG_DIR = 'mintech/';
  var CONFIG_PATH = CONFIG_DIR + 'mintech.json';
  var TOKEN = /\{([^{}]+)\}/g;
  var WHOLE = /^\{([^{}]+)\}$/;
  var MAX_DEPTH = 8;

  function Unresolved(path) { this.path = path; this.message = 'unresolved {' + path + '}'; }

  function has(o, k) { return o !== null && typeof o === 'object' && Object.prototype.hasOwnProperty.call(o, k); }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function asList(v) { return v === null || v === undefined ? [] : Array.isArray(v) ? v : [v]; }
  function clone(v) {
    if (Array.isArray(v)) return v.map(clone);
    if (!isObj(v)) return v;
    var o = {};
    Object.keys(v).forEach(function (k) { o[k] = clone(v[k]); });
    return o;
  }
  function fn(v) { return typeof v === 'function'; }

  // JsonIO returns Java Maps/Lists (KubeJS 6) or Gson elements (other versions): make it plain JS.
  // Keys starting with '$' are comments and are dropped, as in the Python script.
  function toNative(v) {
    var out, it, k, i;
    if (v === null || v === undefined) return null;
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return v;
    if (fn(v.isJsonNull) && fn(v.isJsonPrimitive)) {
      if (v.isJsonNull()) return null;
      if (v.isJsonPrimitive()) {
        var p = v.getAsJsonPrimitive();
        return p.isBoolean() ? p.getAsBoolean() == true : p.isNumber() ? Number(p.getAsDouble()) : String(p.getAsString());
      }
    }
    if (fn(v.keySet) && fn(v.get)) {
      out = {};
      it = v.keySet().iterator();
      while (it.hasNext()) { k = String(it.next()); if (k.charAt(0) !== '$') out[k] = toNative(v.get(k)); }
      return out;
    }
    if (fn(v.size) && fn(v.get)) {
      out = [];
      for (i = 0; i < v.size(); i++) out.push(toNative(v.get(i)));
      return out;
    }
    if (fn(v.doubleValue)) return Number(v.doubleValue());
    if (fn(v.booleanValue)) return v.booleanValue() == true;
    if (Array.isArray(v)) return v.map(toNative);
    if (Object.prototype.toString.call(v) === '[object Object]') {
      out = {};
      Object.keys(v).forEach(function (key) { if (key.charAt(0) !== '$') out[key] = toNative(v[key]); });
      return out;
    }
    return String(v);
  }

  // ---- {path|filter} interpolation -------------------------------------------------
  function stringify(v) {
    if (v === null || v === undefined) return 'null';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
  function titleCase(text) {
    return String(text).split('_').filter(function (w) { return w.length > 0; })
      .map(function (w) { return w.charAt(0).toUpperCase() + w.substring(1); }).join(' ');
  }
  var FILTERS = {
    lower: function (v) { return stringify(v).toLowerCase(); },
    upper: function (v) { return stringify(v).toUpperCase(); },
    title: titleCase,
    tag: function (v) { var s = stringify(v); return s.charAt(0) === '#' ? s.substring(1) : s; },
    path: function (v) { var s = stringify(v); return s.substring(s.indexOf(':') + 1); },
    ns: function (v) { var s = stringify(v); return s.indexOf(':') < 0 ? 'minecraft' : s.replace(/^#+/, '').split(':')[0]; },
    ingredient: function (v) { var s = stringify(v); return s.charAt(0) === '#' ? { tag: s.substring(1) } : { item: s }; }
  };

  function lookup(ctx, path) {
    var cur = ctx, keys = path.split('.'), i, key;
    for (i = 0; i < keys.length; i++) {
      key = keys[i];
      if (Array.isArray(cur) && /^\d+$/.test(key) && Number(key) < cur.length) cur = cur[Number(key)];
      else if (isObj(cur) && has(cur, key)) cur = cur[key];
      else throw new Unresolved(path);
    }
    return cur;
  }
  function evaluate(expr, ctx) {
    var pieces = expr.split('|').map(function (s) { return s.trim(); });
    var value = lookup(ctx, pieces[0]);
    for (var i = 1; i < pieces.length; i++) {
      if (!has(FILTERS, pieces[i])) throw new Error('unknown filter "' + pieces[i] + '"');
      value = FILTERS[pieces[i]](value);
    }
    return value;
  }
  function interpolate(value, ctx, depth) {
    depth = depth || 0;
    if (depth > MAX_DEPTH) throw new Error('interpolation nested deeper than ' + MAX_DEPTH + ' (reference cycle?)');
    if (typeof value === 'string') {
      var whole = WHOLE.exec(value);
      if (whole) return interpolate(evaluate(whole[1], ctx), ctx, depth + 1);
      if (value.indexOf('{') < 0) return value;
      var out = value.replace(TOKEN, function (m, expr) { return stringify(evaluate(expr, ctx)); });
      return out === value ? value : interpolate(out, ctx, depth + 1);
    }
    if (Array.isArray(value)) return value.map(function (v) { return interpolate(v, ctx, depth); });
    if (isObj(value)) {
      var o = {};
      Object.keys(value).forEach(function (k) { o[k] = interpolate(value[k], ctx, depth); });
      return o;
    }
    return value;
  }

  // ---- inheritance -----------------------------------------------------------------
  function deep(a, b) {
    if (!isObj(a) || !isObj(b)) return clone(b);
    var out = clone(a);
    Object.keys(b).forEach(function (k) { out[k] = has(out, k) ? deep(out[k], b[k]) : clone(b[k]); });
    return out;
  }
  var MERGE = {
    replace: function (a, b) { return clone(b); },
    merge: function (a, b) {
      var out = isObj(a) ? clone(a) : {};
      if (isObj(b)) Object.keys(b).forEach(function (k) { out[k] = clone(b[k]); });
      return out;
    },
    deep: deep,
    concat: function (a, b) { return (Array.isArray(a) ? clone(a) : []).concat(Array.isArray(b) ? clone(b) : [clone(b)]); }
  };
  function inherit(sources, rules) {
    var out = {};
    sources.forEach(function (src) {
      if (!isObj(src)) return;
      Object.keys(src).forEach(function (k) {
        if (!has(out, k)) { out[k] = clone(src[k]); return; }
        var mode = rules[k] || 'replace';
        if (!has(MERGE, mode)) throw new Error('unknown merge mode "' + mode + '" for ' + k);
        out[k] = MERGE[mode](out[k], src[k]);
      });
    });
    return out;
  }

  // ---- catalog: families x materials x parts ---------------------------------------
  function matches(selector, material) {
    if (selector === '*') return true;
    if (selector.charAt(0) === '@') return asList(material.groups).indexOf(selector.substring(1)) >= 0;
    return selector === material.id;
  }
  function excluded(rules, material, partId) {
    return asList(rules).some(function (rule) {
      rule = String(rule);
      var cut = rule.indexOf(':');
      var mat = cut < 0 ? rule : rule.substring(0, cut), part = cut < 0 ? '' : rule.substring(cut + 1);
      return matches(mat, material) && (part === '' || part === '*' || part === partId);
    });
  }
  function qualify(id, ns) { id = String(id); return id.indexOf(':') < 0 ? ns + ':' + id : id; }
  function pattern(entry, name) {
    try { return interpolate(entry.spec.patterns[name], entry.ctx); }
    catch (e) { throw new Error(entry.key + ': patterns.' + name + ' ' + e.message); }
  }

  function buildCatalog(cfg) {
    var ns = cfg.meta.namespace, rules = cfg.inheritance || {}, parts = cfg.parts || {};
    var families = cfg.families || {}, rawMaterials = cfg.materials || {};
    var materials = {}, index = {}, entries = [], owners = {};

    Object.keys(rawMaterials).forEach(function (mid) {
      var m = { name: titleCase(mid), palette: mid, groups: [], inputs: {}, outputs: {}, stats: {}, overrides: {} };
      [cfg.material_defaults, rawMaterials[mid]].forEach(function (src) {
        if (isObj(src)) Object.keys(src).forEach(function (k) { m[k] = clone(src[k]); });
      });
      m.id = mid;
      materials[mid] = m;
    });

    Object.keys(families).forEach(function (famId) {
      var family = clone(families[famId]);
      family.id = famId;
      var matSel = has(family, 'materials') ? asList(family.materials) : ['*'];
      var partSel = has(family, 'parts') ? asList(family.parts) : ['*'];
      var mids = [], pids = [];
      matSel.forEach(function (sel) {
        if (sel !== '*' && sel.charAt(0) !== '@' && !has(materials, sel)) throw new Error('families.' + famId + ': unknown material ' + sel);
        Object.keys(materials).forEach(function (mid) { if (matches(sel, materials[mid]) && mids.indexOf(mid) < 0) mids.push(mid); });
      });
      partSel.forEach(function (sel) {
        (sel === '*' ? Object.keys(parts) : [sel]).forEach(function (pid) {
          if (!has(parts, pid)) throw new Error('families.' + famId + ': unknown part ' + pid);
          if (pids.indexOf(pid) < 0) pids.push(pid);
        });
      });
      mids.forEach(function (mid) {
        var material = materials[mid], ov = material.overrides || {};
        pids.forEach(function (pid) {
          if (excluded(family.exclude, material, pid)) return;
          var spec = inherit([cfg.defaults, family.defaults, parts[pid], ov['*'], ov[pid]], rules);
          spec.id = pid;
          if (spec.name === null || spec.name === undefined) spec.name = titleCase(pid);
          var entry = {
            key: famId + ':' + mid + ':' + pid, spec: spec,
            ctx: { meta: cfg.meta, vars: cfg.vars || {}, family: family, material: material, part: spec, items: index }
          };
          if (!index[famId]) index[famId] = {};
          if (!index[famId][mid]) index[famId][mid] = {};
          index[famId][mid][pid] = qualify(pattern(entry, 'id'), ns);
          entry.famId = famId; entry.mid = mid; entry.pid = pid;
          entries.push(entry);
        });
      });
    });

    entries.forEach(function (e) {
      e.ctx.parts = index[e.famId][e.mid];
      e.ctx.self = e.id = e.ctx.parts[e.pid];
      if (has(owners, e.id)) throw new Error('item id ' + e.id + ' produced by both ' + owners[e.id] + ' and ' + e.key);
      owners[e.id] = e.key;
      e.ctx.texture = e.texture = qualify(pattern(e, 'texture'), ns);
      e.ctx.name = e.name = String(pattern(e, 'name'));
    });
    return entries;
  }

  // The index file plus every fragment its 'include' list names, deep-merged in order, so a
  // section spread over several files ('parts' across parts.json and ores.json, say) arrives
  // here as one dict. Mirrors load_config() in mintech/mintech_textures.py.
  function readJson(path) {
    var raw = JsonIO.read(path);
    if (raw === null || raw === undefined) throw new Error(path + ' not found in the instance folder');
    return toNative(raw);
  }

  function load() {
    var cfg = readJson(CONFIG_PATH);
    var includes = asList(cfg.include);
    delete cfg.include;
    includes.forEach(function (name) {
      cfg = deep(cfg, readJson(CONFIG_DIR + name));
    });
    return { config: cfg, catalog: buildCatalog(cfg) };
  }

  return {
    load: load, interpolate: interpolate, Unresolved: Unresolved,
    isUnresolved: function (e) { return e instanceof Unresolved; },
    asList: asList, filters: FILTERS
  };
})();

StartupEvents.registry('item', function (event) {
  var MT = global.MinTech, data;
  try { data = MT.load(); }
  catch (e) { console.error('[mintech] cannot load the mintech/ config: ' + e.message); return; }

  var registered = 0;
  data.catalog.forEach(function (entry) {
    if (entry.spec.type === 'block') return; // registered as a block below instead
    var builder = event.create(entry.id).texture(entry.texture).displayName(entry.name);
    var calls = entry.spec.item || {};
    Object.keys(calls).forEach(function (method) {
      try { builder[method](MT.interpolate(calls[method], entry.ctx)); }
      catch (e) { console.warn('[mintech] ' + entry.key + ': item.' + method + ' skipped (' + e.message + ')'); }
    });
    registered++;
  });
  console.info('[mintech] registered ' + registered + ' items');
});

// Parts marked 'type': 'block' (e.g. ore/deepslate_ore) become real in-world blocks instead of
// plain items; KubeJS auto-registers their BlockItem, so they are skipped in the 'item' pass above.
StartupEvents.registry('block', function (event) {
  var MT = global.MinTech, data;
  try { data = MT.load(); }
  catch (e) { console.error('[mintech] cannot load the mintech/ config: ' + e.message); return; }

  var registered = 0;
  data.catalog.forEach(function (entry) {
    if (entry.spec.type !== 'block') return;
    var builder = event.create(entry.id).textureAll(entry.texture).displayName(entry.name);
    var calls = entry.spec.block || {};
    Object.keys(calls).forEach(function (method) {
      try { builder[method](MT.interpolate(calls[method], entry.ctx)); }
      catch (e) { console.warn('[mintech] ' + entry.key + ': block.' + method + ' skipped (' + e.message + ')'); }
    });
    registered++;
  });
  console.info('[mintech] registered ' + registered + ' blocks');
});
