import { defineConfig } from 'vite';
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { resolve, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '..');
const built = resolve(here, 'built');
const sha = file => createHash('sha256').update(readFileSync(file)).digest('hex');
// Git's Windows checkout policy may change CRLF/LF without changing source.
// Runtime assets are still verified byte-for-byte; source text is normalized.
const sourceSha = file => createHash('sha256').update(readFileSync(file, 'utf8').replace(/\r\n/g, '\n')).digest('hex');
const contract = () => execFileSync(process.env.MEP_PYTHON || 'python', ['-c',
  'import sys;sys.stdout.reconfigure(encoding="utf-8");import preview;print(preview.contract_script())'],
  { cwd: repo, encoding: 'utf8' });
const shell = () => readFileSync(resolve(here, 'src/shell.html'), 'utf8');
const demo = {
  source: '개발 화면 샘플 — 실제 도면 검증 결과가 아닙니다', center: [2500, 0], bbox: [0, -100, 5000, 100],
  elements: { wall: [{ eid: 'w:dev-sample', centerline: [[0, 0], [5000, 0]], width: 200, height: 2800, layer: 'DEMO' }] },
  params: {}, floors: [], project_edits: {}, source_drawing: { status: 'unavailable', floors: [], warnings: ['실제 프로젝트 검토는 Python GUI에서 여세요.'] }
};

function filesUnder(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(item => {
    const path = resolve(dir, item.name);
    return item.isDirectory() ? filesUnder(path) : [path];
  });
}

export default defineConfig({
  root: here,
  resolve: { alias: [
    { find: 'mep-edit', replacement: resolve(repo, 'vendor/edit_geometry.js') },
    { find: 'three/addons/controls/OrbitControls.js', replacement: resolve(repo, 'vendor/OrbitControls.js') },
    { find: /^three$/, replacement: resolve(repo, 'vendor/three.module.js') }
  ] },
  optimizeDeps: { include: ['mep-edit'] },
  server: { host: '127.0.0.1', strictPort: true },
  build: {
    outDir: built,
    emptyOutDir: true,
    target: 'es2020',
    lib: { entry: resolve(here, 'src/app.js'), name: 'MepPreview', formats: ['iife'], fileName: () => 'preview.js', cssFileName: 'preview' },
    sourcemap: false
  },
  plugins: [{
    name: 'mep-offline-shell',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!['/', '/index.html'].includes((req.url || '').split('?')[0])) return next();
        try {
          const replacements = {
            '/*__DATA__*/': JSON.stringify(demo).replace(/</g, '\\u003c'),
            '/*__STYLE__*/': '', '/*__CONTRACT__*/': contract()
          };
          let html = shell().replace(/\/\*__(DATA|STYLE|CONTRACT)__\*\//g, key => replacements[key]);
          html = html.replace('<script id="mep-app">/*__APP__*/</script>', '<script type="module" src="/src/app.js"></script>');
          html = await server.transformIndexHtml(req.url, html);
          res.statusCode = 200;
          res.setHeader('Content-Type', 'text/html; charset=utf-8');
          res.end(html);
        } catch (error) { next(error); }
      });
    },
    closeBundle() {
      writeFileSync(resolve(built, 'shell.html'), shell());
      const assets = Object.fromEntries(['preview.js', 'preview.css', 'shell.html'].map(name => [name, sha(resolve(built, name))]));
      const inputPaths = [...filesUnder(resolve(here, 'src')),
        resolve(here, 'package.json'), resolve(here, 'package-lock.json'), fileURLToPath(import.meta.url),
        resolve(repo, 'vendor/three.module.js'), resolve(repo, 'vendor/OrbitControls.js'), resolve(repo, 'vendor/edit_geometry.js')];
      const sources = Object.fromEntries(inputPaths.sort().map(path => [relative(repo, path).replaceAll('\\', '/'), sourceSha(path)]));
      writeFileSync(resolve(built, 'manifest.json'), JSON.stringify({ tool: 'vite', version: '8.2.2', three: '0.160.0', source_hash: 'sha256-lf', assets, sources }, null, 2) + '\n');
    }
  }]
});
