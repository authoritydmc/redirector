import { existsSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { E2E_ADMIN_PASSWORD } from './helpers'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const API_PORT = Number(process.env.E2E_API_PORT ?? 8123)

function python(): string {
  const venv =
    process.platform === 'win32'
      ? join(ROOT, '.venv', 'Scripts', 'python.exe')
      : join(ROOT, '.venv', 'bin', 'python')
  if (existsSync(venv)) {
    return venv
  }
  return process.platform === 'win32' ? 'python' : 'python3'
}

function sqliteUrl(file: string): string {
  return `sqlite:///${file.replace(/\\/g, '/')}`
}

async function run(cwd: string, exe: string, args: string[], env: NodeJS.ProcessEnv): Promise<void> {
  const { spawnSync } = await import('node:child_process')
  const result = spawnSync(exe, args, { cwd, env: { ...process.env, ...env }, encoding: 'utf-8' })
  if (result.status !== 0) {
    throw new Error(`setup command failed: ${exe} ${args.join(' ')}\n${result.stderr}`);
  }
}

async function waitReady(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    try {
      const res = await fetch(url)
      if (res.ok) {
        return
      }
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) {
      throw new Error(`API never became ready at ${url}`)
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
}

/** Boots the real API on a scratch DB before the suite (and kills it after).
 * If an API is already healthy on the port (developer runs it by hand),
 * it is reused as-is and teardown becomes a no-op — same philosophy as
 * Playwright's reuseExistingServer for vite. */
export default async function globalSetup(): Promise<void> {
  try {
    const res = await fetch(`http://127.0.0.1:${API_PORT}/healthz`)
    if (res.ok) {
      return
    }
  } catch {
    // nothing listening — boot our own below
  }
  const scratch = mkdtempSync(join(tmpdir(), 'rd-e2e-'))
  const dbFile = join(scratch, 'e2e.db')
  const dataDir = join(scratch, 'data')
  const exe = python()
  const env = {
    PYTHONPATH: ROOT,
    REDIRECTOR_DATA_DIR: dataDir,
    REDIRECTOR_DATABASE_URL: `sqlite+aiosqlite:///${dbFile.replace(/\\/g, '/')}`,
    REDIRECTOR_AUTO_REDIRECT_DELAY: '0',
    REDIRECTOR_ADMIN_PASSWORD: E2E_ADMIN_PASSWORD,
  }
  await run(ROOT, exe, [
    '-c',
    'from sqlmodel import SQLModel, create_engine; '
    + 'import backend.models.entities; '
    + `SQLModel.metadata.create_all(create_engine(${JSON.stringify(sqliteUrl(dbFile))})); `
    + "print('e2e tables ok')",
  ], env)

  const { spawn } = await import('node:child_process')
  const child = spawn(exe, ['-m', 'uvicorn', 'backend.main:app', '--port', String(API_PORT)], {
    cwd: ROOT,
    env: { ...process.env, ...env },
    stdio: 'ignore',
  })
  if (child.pid === undefined) {
    throw new Error('failed to spawn uvicorn for e2e')
  }
  const sidecar = join(scratch, 'api.json')
  writeFileSync(sidecar, JSON.stringify({ pid: child.pid }))
  process.env.RD_E2E_SIDECAR = sidecar
  await waitReady(`http://127.0.0.1:${API_PORT}/healthz`, 60000)
}
