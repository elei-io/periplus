import { loadEnvFile } from 'node:process'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
try { loadEnvFile(new URL('../../../.env', import.meta.url)) }
catch (error) { if (error.code !== 'ENOENT') throw error }
process.argv = [process.argv[0], require.resolve('next/dist/bin/next'), 'dev', ...process.argv.slice(2)]
await import('next/dist/bin/next')
