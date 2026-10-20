// Every token declared in main.css's @theme block must reach the built CSS's
// theme layer. Tailwind prunes @theme variables that no scanned source names
// literally, and only the production build shows it: dev and the unit tier
// read main.css directly. Run after `pnpm run build`.
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const SOURCE = 'src/shared/styles/main.css'
const DIST = 'dist/assets'

function themeBlock(css) {
  const start = css.search(/@theme\b[^{]*\{/)
  if (start === -1) return null
  let depth = 0
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1)
  }
  return null
}

function builtThemeLayer(css) {
  const start = css.indexOf('@layer theme{')
  if (start === -1) return null
  let depth = 0
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1)
  }
  return null
}

const source = themeBlock(readFileSync(SOURCE, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ''))
if (!source) {
  console.error(`ERROR: no @theme block in ${SOURCE}`)
  process.exit(1)
}
const declared = [...new Set([...source.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]))]

let files
try {
  files = readdirSync(DIST).filter((f) => f.endsWith('.css'))
} catch {
  console.error(`ERROR: ${DIST} not found. Run 'pnpm run build' first.`)
  process.exit(1)
}
const layer = files
  .map((f) => builtThemeLayer(readFileSync(join(DIST, f), 'utf8')))
  .find(Boolean)
if (!layer) {
  console.error(`ERROR: no '@layer theme{' block in ${DIST}/*.css`)
  process.exit(1)
}

const missing = declared.filter((token) => !layer.includes(`${token}:`))
if (missing.length > 0) {
  console.error(`FAIL: ${missing.length} of ${declared.length} @theme tokens are missing from the built CSS:`)
  for (const token of missing) console.error(`  ${token}`)
  process.exit(1)
}
console.log(`OK: all ${declared.length} @theme tokens reach the built CSS.`)
