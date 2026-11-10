// Jest 27 (react-scripts 5) runs tests as CommonJS. react-router v8 and its
// dependencies ship ESM-only and reference `import.meta` (only `import.meta.hot`,
// which is a dev-server HMR hook and always undefined under Jest). Transform
// those packages with CRA's Babel preset and swap `import.meta` for an empty
// object so the CommonJS output parses.
const babelJest = require('babel-jest').default || require('babel-jest');

function replaceImportMeta({ types: t }) {
  return {
    visitor: {
      MetaProperty(path) {
        const { meta, property } = path.node;
        if (meta.name === 'import' && property.name === 'meta') {
          path.replaceWith(t.objectExpression([]));
        }
      },
    },
  };
}

module.exports = babelJest.createTransformer({
  presets: [[require.resolve('babel-preset-react-app'), { runtime: 'automatic' }]],
  plugins: [replaceImportMeta],
  babelrc: false,
  configFile: false,
});
