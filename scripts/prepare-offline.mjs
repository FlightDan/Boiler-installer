// Compatibility entry point: prepare the locked guest dependencies, never a
// moving APKINDEX dependency resolution. No shell interpolation is involved.
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const portable = path.join(root, 'runtime/python/python.exe');
const python = process.env.BOILER_PYTHON || (process.platform === 'win32' && existsSync(portable)
  ? portable : (process.platform === 'win32' ? 'python' : 'python3'));
const result = spawnSync(python, [path.join(root, 'scripts/bootstrap-dependencies.py'), '--profile', 'guest', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit', shell: false,
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
