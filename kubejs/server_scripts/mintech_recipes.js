// kubejs/server_scripts/mintech_recipes.js
//
// Tags and placeholder crafting progression for everything in mintech.json.
// Uses the catalog code published by startup_scripts/mintech_items.js (global.MinTech).
// mintech.json is re-read on /reload, but new or renamed items need a game restart.
// Plain ES5 for Rhino.

(function () {
  var MT = global.MinTech;
  if (!MT) {
    console.error('[mintech] global.MinTech missing: is startup_scripts/mintech_items.js installed?');
    return;
  }

  var data;
  try { data = MT.load(); }
  catch (e) { console.error('[mintech] cannot load mintech.json: ' + e.message); return; }

  var cfg = data.config;
  var baseCtx = { meta: cfg.meta, vars: cfg.vars || {} };

  // Interpolate, or return null (and remember why) when a {reference} is missing.
  function resolve(value, ctx, label, skipped) {
    try { return MT.interpolate(value, ctx); }
    catch (e) {
      if (!MT.isUnresolved(e)) throw e;
      skipped.push(label + ' (' + e.message + ')');
      return null;
    }
  }

  // ---- tags ---------------------------------------------------------------------------
  ServerEvents.tags('item', function (event) {
    var skipped = [];
    var globalTags = cfg.tags || {};
    Object.keys(globalTags).forEach(function (tag) {
      var values = resolve(globalTags[tag], baseCtx, 'tags.' + tag, skipped);
      if (values !== null) event.add(MT.filters.tag(tag), values);
    });
    data.catalog.forEach(function (entry) {
      MT.asList(entry.spec.tags).forEach(function (tag) {
        var name = resolve(tag, entry.ctx, entry.key + ' tag ' + tag, skipped);
        if (name !== null) event.add(MT.filters.tag(name), entry.id);
      });
    });
    if (skipped.length) console.info('[mintech] skipped tags: ' + skipped.join(', '));
  });

  // ---- recipes ------------------------------------------------------------------------
  var BUILDERS = {
    shapeless: function (event, r) { return event.shapeless(Item.of(r.output, r.count), r.inputs); },
    shaped: function (event, r) { return event.shaped(Item.of(r.output, r.count), r.pattern, r.key); }
  };

  ServerEvents.recipes(function (event) {
    var skipped = [], added = 0, seen = {};
    data.catalog.forEach(function (entry) {
      MT.asList(entry.spec.recipes).forEach(function (raw, i) {
        var name = raw.name || (raw.type + '_' + i);
        var label = entry.key + ' recipe ' + name;
        var ctx = {};
        Object.keys(entry.ctx).forEach(function (k) { ctx[k] = entry.ctx[k]; });
        ctx.recipe = { name: name, index: i, type: raw.type };

        var r = resolve(raw, ctx, label, skipped);
        var id = resolve(raw.id || entry.spec.patterns.recipe_id, ctx, label + ' id', skipped);
        if (r === null || id === null) return;
        if (!BUILDERS[r.type]) { console.error('[mintech] ' + label + ': unknown type "' + r.type + '" (shapeless | shaped)'); return; }
        if (seen[id]) { console.error('[mintech] ' + label + ': duplicate recipe id ' + id + ' (also ' + seen[id] + ')'); return; }
        seen[id] = label;
        if (r.output === undefined) r.output = entry.id;
        if (r.count === undefined) r.count = 1;

        try {
          var recipe = BUILDERS[r.type](event, r);
          if (r.damage) recipe.damageIngredient(r.damage);
          recipe.id(id);
          added++;
        } catch (e) {
          console.error('[mintech] ' + label + ': ' + (e.message || e));
        }
      });
    });
    console.info('[mintech] added ' + added + ' recipes' + (skipped.length ? '; skipped ' + skipped.join(', ') : ''));
  });
})();
