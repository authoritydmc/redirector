import { readFileSync } from 'node:fs'

/** Stops the API booted by global-setup. */
export default async function globalTeardown(): Promise<void> {
  const sidecar = process.env.RD_E2E_SIDECAR
  if (sidecar === undefined) {
    return
  }
  try {
    const { pid } = JSON.parse(readFileSync(sidecar, 'utf-8')) as { pid: number }
    process.kill(pid)
  } catch {
    // Already gone (CI teardown races, developer Ctrl-C) — nothing to do.
  }
}
