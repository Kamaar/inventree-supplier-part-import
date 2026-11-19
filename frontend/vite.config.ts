import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteExternalsPlugin } from 'vite-plugin-externals'

/**
 * The following libraries are externalized to avoid bundling them with the plugin.
 * These libraries are expected to be provided by the InvenTree core application.
 */
export const externalLibs: Record<string, string> = {
  react: 'React',
  'react-dom': 'ReactDOM',
  'ReactDom': 'ReactDOM',
  '@mantine/core': 'MantineCore',
  '@mantine/notifications': 'MantineNotifications',
};

const externalKeys = Object.keys(externalLibs);

/**
 * Build the frontend as exported modules, distributed in the 'static' directory of the plugin.
 */
export default defineConfig({
  plugins: [
    react({
      jsxRuntime: 'classic',
    }),
    viteExternalsPlugin(externalLibs),
  ],
  esbuild: {
    jsx: 'preserve',
  },
  build: {
    target: 'esnext',
    cssCodeSplit: false,
    manifest: true,
    sourcemap: true,
    rollupOptions: {
      preserveEntrySignatures: 'exports-only',
      input: ['./src/ImportPage.tsx', './src/Settings.tsx'],
      output: [
        {
          dir: '../supplier_part_import/static',
          entryFileNames: '[name]-[hash].js',
          assetFileNames: 'assets/[name].[ext]',
          globals: externalLibs,
        },
      ],
      external: externalKeys,
    },
  },
  optimizeDeps: {
    exclude: externalKeys,
  },
})
