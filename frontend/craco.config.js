// craco.config.js
const path = require("path");
require("dotenv").config();

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

module.exports = webpackConfig;
