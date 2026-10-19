// Jest 27 (react-scripts 5) ignores package.json "exports", so subpath
// imports such as `react-router/dom` or `@radix-ui/primitive/is-development`
// can't be found. Fall back to reading "exports" ourselves (CommonJS
// conditions) when the default resolver fails.
const fs = require('fs');
const path = require('path');

const CONDITIONS = ['require', 'node', 'development', 'default'];

function pick(target) {
  if (typeof target === 'string') return target;
  if (Array.isArray(target)) {
    for (const t of target) {
      const hit = pick(t);
      if (hit) return hit;
    }
    return null;
  }
  if (target && typeof target === 'object') {
    for (const cond of CONDITIONS) {
      if (cond in target) {
        const hit = pick(target[cond]);
        if (hit) return hit;
      }
    }
  }
  return null;
}

function splitSpecifier(request) {
  const parts = request.split('/');
  const n = request.startsWith('@') ? 2 : 1;
  return { name: parts.slice(0, n).join('/'), subpath: ['.', ...parts.slice(n)].join('/') };
}

module.exports = (request, options) => {
  try {
    return options.defaultResolver(request, options);
  } catch (error) {
    if (request.startsWith('.') || path.isAbsolute(request)) throw error;
    const { name, subpath } = splitSpecifier(request);
    const dirs = [...(options.paths || [])];
    for (let dir = options.basedir; ; dir = path.dirname(dir)) {
      dirs.push(path.join(dir, 'node_modules'));
      if (path.dirname(dir) === dir) break;
    }
    for (const dir of dirs) {
      const pkgDir = path.join(dir, name);
      const pkgJson = path.join(pkgDir, 'package.json');
      if (!fs.existsSync(pkgJson)) continue;
      const { exports: exp } = JSON.parse(fs.readFileSync(pkgJson, 'utf8'));
      if (!exp) continue;
      const entry = typeof exp === 'string' || Array.isArray(exp) || !Object.keys(exp).some((k) => k.startsWith('.'))
        ? (subpath === '.' ? exp : null)
        : exp[subpath];
      const target = pick(entry);
      if (target) return path.resolve(pkgDir, target);
    }
    throw error;
  }
};
