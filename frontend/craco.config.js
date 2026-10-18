// craco.config.js
const path = require("path");
require("dotenv").config();

// Check if we're in development/preview mode (not production build)
// Craco sets NODE_ENV=development for start, NODE_ENV=production for build
const isDevServer = process.env.NODE_ENV !== "production";

// Environment variable overrides
const config = {
  enableHealthCheck: process.env.ENABLE_HEALTH_CHECK === "true",
};

// Conditionally load health check modules only if enabled
let WebpackHealthPlugin;
let setupHealthEndpoints;
let healthPluginInstance;

if (config.enableHealthCheck) {
  WebpackHealthPlugin = require("./plugins/health-check/webpack-health-plugin");
  setupHealthEndpoints = require("./plugins/health-check/health-endpoints");
  healthPluginInstance = new WebpackHealthPlugin();
}

let webpackConfig = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
        // eslint-plugin-react-hooks v7 ships the React Compiler rules in its
        // `recommended` preset. This codebase predates them, so the upgrade
        // surfaces them as build-breaking errors. Keep the two long-standing
        // rules above enforced and turn the new compiler-only rules off until
        // the components are migrated.
        "react-hooks/immutability": "off",
        "react-hooks/preserve-manual-memoization": "off",
        "react-hooks/set-state-in-effect": "off",
      },
    },
  },
  webpack: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
    configure: (webpackConfig) => {

      // Tailwind CSS v4 moved its PostCSS plugin out of the `tailwindcss`
      // package into `@tailwindcss/postcss`. CRA (react-scripts) still injects
      // the bare `tailwindcss` package as a PostCSS plugin whenever a
      // `tailwind.config.js` exists at the app root, and it hard-codes
      // `config: false` so `postcss.config.js` is never read. Swap the plugin
      // in the postcss-loader options so the v4 build works.
      const swapTailwindPostcssPlugin = (rules) => {
        for (const rule of rules || []) {
          if (!rule) continue;
          if (Array.isArray(rule.oneOf)) swapTailwindPostcssPlugin(rule.oneOf);
          if (Array.isArray(rule.use)) {
            for (const use of rule.use) {
              const plugins = use && use.options && use.options.postcssOptions
                && use.options.postcssOptions.plugins;
              if (Array.isArray(plugins)) {
                use.options.postcssOptions.plugins = plugins.map((plugin) =>
                  plugin === "tailwindcss" ? "@tailwindcss/postcss" : plugin
                );
              }
            }
          }
        }
      };
      swapTailwindPostcssPlugin(webpackConfig.module && webpackConfig.module.rules);

      // Add ignored patterns to reduce watched directories
        webpackConfig.watchOptions = {
          ...webpackConfig.watchOptions,
          ignored: [
            '**/node_modules/**',
            '**/.git/**',
            '**/build/**',
            '**/dist/**',
            '**/coverage/**',
            '**/public/**',
        ],
      };

      // Add health check plugin to webpack if enabled
      if (config.enableHealthCheck && healthPluginInstance) {
        webpackConfig.plugins.push(healthPluginInstance);
      }
      return webpackConfig;
    },
  },
};

webpackConfig.devServer = (devServerConfig) => {
  // Add health check endpoints if enabled
  if (config.enableHealthCheck && setupHealthEndpoints && healthPluginInstance) {
    const originalSetupMiddlewares = devServerConfig.setupMiddlewares;

    devServerConfig.setupMiddlewares = (middlewares, devServer) => {
      // Call original setup if exists
      if (originalSetupMiddlewares) {
        middlewares = originalSetupMiddlewares(middlewares, devServer);
      }

      // Setup health endpoints
      setupHealthEndpoints(devServer, healthPluginInstance);

      return middlewares;
    };
  }

  return devServerConfig;
};

// Jest 27 (react-scripts 5) ignores package.json "exports", so it can't
// resolve the `react-router/dom` subpath that react-router-dom v7 imports.
// Point it at the CommonJS build so components using react-router can be
// rendered in tests.
webpackConfig.jest = {
  configure: (jestConfig) => ({
    ...jestConfig,
    moduleNameMapper: {
      ...(jestConfig.moduleNameMapper || {}),
      '^react-router/dom$': '<rootDir>/node_modules/react-router/dist/development/dom-export.js',
    },
  }),
};

// Wrap with visual edits (automatically adds babel plugin, dev server, and overlay in dev mode)
if (isDevServer) {
  try {
    const { withVisualEdits } = require("@emergentbase/visual-edits/craco");
    webpackConfig = withVisualEdits(webpackConfig);
  } catch (err) {
    if (err.code === 'MODULE_NOT_FOUND' && err.message.includes('@emergentbase/visual-edits/craco')) {
      console.warn(
        "[visual-edits] @emergentbase/visual-edits not installed \u2014 visual editing disabled."
      );
    } else {
      throw err;
    }
  }
}

module.exports = webpackConfig;
